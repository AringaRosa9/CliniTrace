import base64
import hashlib
import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any, BinaryIO
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import Connection, and_, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import aliased

from app.core.config import Settings
from app.db.models import encounters, patients
from app.db.s1 import (
    artifacts,
    audit,
    documents,
    encounter_details,
    idempotency,
    identities,
    jobs,
    outbox,
    versions,
)
from app.db.session import transaction
from app.integrations.parsers.document import ParseFailure, detect
from app.integrations.storage.s3 import Storage
from app.modules.documents.schema import Association, EncounterCreate, PatientCreate
from app.modules.identity.service import membership


def now() -> datetime:
    return datetime.now(UTC)


class Service:
    def __init__(self, settings: Settings, actor: UUID, project: UUID, request_id: UUID):
        self.settings, self.actor, self.project, self.request_id = (
            settings,
            actor,
            project,
            request_id,
        )
        self.member = membership(settings, actor, project)
        self.tenant: UUID = self.member["tenant_id"]
        self.scope = {"tenant_id": self.tenant, "project_id": project}

    def require(self, capability: str) -> None:
        if capability not in self.member["capabilities"]:
            raise HTTPException(403)

    @contextmanager
    def tx(self) -> Iterator[Connection]:
        try:
            with transaction(
                self.settings, tenant=self.tenant, project=self.project, user=self.actor
            ) as conn:
                yield conn
        except IntegrityError:
            raise HTTPException(409) from None

    def event(
        self, conn: Connection, action: str, target: UUID, details: dict[str, Any] | None = None
    ) -> None:
        conn.execute(
            audit.insert().values(
                id=uuid4(),
                **self.scope,
                actor_id=self.actor,
                action=action,
                target_id=target,
                request_id=self.request_id,
                details=details or {},
                created_at=now(),
            )
        )

    def patient(self, body: PatientCreate) -> dict[str, Any]:
        self.require("import")
        with self.tx() as conn:
            existing = (
                conn.execute(
                    select(identities).where(
                        identities.c.source == body.source,
                        identities.c.source_key == body.source_key,
                    )
                )
                .mappings()
                .first()
            )
            if existing:
                row = (
                    conn.execute(select(patients).where(patients.c.id == existing["patient_id"]))
                    .mappings()
                    .one()
                )
                if row["patient_key"] != body.patient_key:
                    raise HTTPException(409)
                return {"id": row["id"], "patient_key": row["patient_key"]}
            patient_id = uuid4()
            conn.execute(
                patients.insert().values(id=patient_id, **self.scope, patient_key=body.patient_key)
            )
            conn.execute(
                identities.insert().values(
                    id=uuid4(),
                    **self.scope,
                    patient_id=patient_id,
                    source=body.source,
                    source_key=body.source_key,
                )
            )
            self.event(conn, "patient.create", patient_id)
            return {"id": patient_id, "patient_key": body.patient_key}

    def encounter(self, body: EncounterCreate) -> dict[str, Any]:
        self.require("import")
        with self.tx() as conn:
            if not conn.execute(
                select(patients.c.id).where(patients.c.id == body.patient_id)
            ).first():
                raise HTTPException(404)
            row = (
                conn.execute(
                    select(
                        encounters.c.id,
                        encounters.c.patient_id,
                        encounters.c.kind,
                        encounter_details.c.occurred_on,
                        encounter_details.c.department,
                    )
                    .join(encounter_details, encounters.c.id == encounter_details.c.encounter_id)
                    .where(
                        encounter_details.c.source == body.source,
                        encounter_details.c.source_key == body.source_key,
                    )
                )
                .mappings()
                .first()
            )
            if row:
                if any(
                    row[k] != getattr(body, k)
                    for k in ("patient_id", "kind", "occurred_on", "department")
                ):
                    raise HTTPException(409)
                return dict(row)
            encounter_id = uuid4()
            conn.execute(
                encounters.insert().values(
                    id=encounter_id, **self.scope, patient_id=body.patient_id, kind=body.kind
                )
            )
            conn.execute(
                encounter_details.insert().values(
                    id=uuid4(),
                    **self.scope,
                    encounter_id=encounter_id,
                    **body.model_dump(exclude={"patient_id", "kind"}),
                )
            )
            self.event(conn, "encounter.create", encounter_id)
            return {"id": encounter_id, **body.model_dump(exclude={"source", "source_key"})}

    def validate_association(self, conn: Connection, patient: UUID, encounter: UUID) -> None:
        if not conn.execute(
            select(encounters.c.id).where(
                encounters.c.id == encounter, encounters.c.patient_id == patient
            )
        ).first():
            raise HTTPException(404)

    def upload(
        self,
        stream: BinaryIO,
        filename: str,
        key: str,
        patient: UUID,
        encounter: UUID,
        document_type: str,
        source: str,
        authorization: str,
    ) -> dict[str, Any]:
        self.require("import")
        stream.seek(0)
        hasher = hashlib.sha256()
        size = 0
        while chunk := stream.read(64 * 1024):
            size += len(chunk)
            if size > self.settings.max_upload_bytes:
                raise HTTPException(413)
            hasher.update(chunk)
        if size == 0:
            raise HTTPException(422)
        stream.seek(0)
        try:
            mime = detect(
                stream.read() if filename.lower().endswith(".txt") else stream.read(64), filename
            )
        except ParseFailure:
            raise HTTPException(415) from None
        sha = hasher.hexdigest()
        summary = {
            "sha256": sha,
            "patient": str(patient),
            "encounter": str(encounter),
            "type": document_type,
            "source": source,
            "authorization": authorization,
            "filename": filename,
        }
        digest = hashlib.sha256(json.dumps(summary, sort_keys=True).encode()).hexdigest()
        object_key: str | None = None
        committed = False
        try:
            with self.tx() as conn:
                # Serialize identical actor+key and project+hash operations across API processes.
                for lock_key in sorted(
                    [f"{self.project}:{self.actor}:{key}", f"{self.project}:{sha}"]
                ):
                    conn.execute(
                        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                        {"key": lock_key},
                    )
                previous = (
                    conn.execute(
                        select(idempotency).where(
                            idempotency.c.actor_id == self.actor,
                            idempotency.c.operation == "upload",
                            idempotency.c.key == key,
                        )
                    )
                    .mappings()
                    .first()
                )
                if previous:
                    if previous["digest"] != digest:
                        raise HTTPException(409)
                    return dict(previous["response"])
                self.validate_association(conn, patient, encounter)
                duplicate = (
                    conn.execute(
                        select(
                            versions.c.id,
                            versions.c.document_id,
                            jobs.c.id.label("job_id"),
                            documents.c.processing_status,
                        )
                        .join(documents, documents.c.id == versions.c.document_id)
                        .join(
                            jobs,
                            and_(
                                jobs.c.document_version_id == versions.c.id,
                                jobs.c.kind == "parsing",
                            ),
                        )
                        .where(versions.c.sha256 == sha)
                        .order_by(jobs.c.created_at.desc(), jobs.c.id.desc())
                        .limit(1)
                    )
                    .mappings()
                    .first()
                )
                if duplicate:
                    # A duplicate across associations cannot silently move a document.
                    doc = (
                        conn.execute(
                            select(documents).where(documents.c.id == duplicate["document_id"])
                        )
                        .mappings()
                        .one()
                    )
                    if (
                        doc["patient_id"] != patient
                        or doc["encounter_id"] != encounter
                        or doc["document_type"] != document_type
                    ):
                        raise HTTPException(409)
                    result = {
                        "document_id": str(duplicate["document_id"]),
                        "document_version_id": str(duplicate["id"]),
                        "job_id": str(duplicate["job_id"]),
                        "processing_status": duplicate["processing_status"],
                        "duplicate": True,
                    }
                else:
                    document_id, version_id, job_id = uuid4(), uuid4(), uuid4()
                    object_key = f"quarantine/{self.tenant}/{self.project}/{version_id}/original"
                    stream.seek(0)
                    Storage(self.settings).put(object_key, stream, mime)
                    conn.execute(
                        documents.insert().values(
                            id=document_id,
                            **self.scope,
                            patient_id=patient,
                            encounter_id=encounter,
                            document_type=document_type,
                            filename=filename,
                            processing_status="queued",
                            revision=1,
                            created_at=now(),
                        )
                    )
                    conn.execute(
                        versions.insert().values(
                            id=version_id,
                            **self.scope,
                            document_id=document_id,
                            sha256=sha,
                            mime=mime,
                            size=size,
                            object_key=object_key,
                            source=source,
                            authorization_reference=authorization,
                            actor_id=self.actor,
                            created_at=now(),
                        )
                    )
                    conn.execute(
                        jobs.insert().values(
                            id=job_id,
                            **self.scope,
                            document_version_id=version_id,
                            status="queued",
                            attempt=0,
                            generation=0,
                            progress=0,
                            cancel_requested=False,
                            created_at=now(),
                            request_id=self.request_id,
                        )
                    )
                    enqueue(conn, self.scope, job_id)
                    self.event(
                        conn, "document.upload", document_id, {"version_id": str(version_id)}
                    )
                    result = {
                        "document_id": str(document_id),
                        "document_version_id": str(version_id),
                        "job_id": str(job_id),
                        "processing_status": "queued",
                        "duplicate": False,
                    }
                result["status_url"] = f"/api/v1/projects/{self.project}/jobs/{result['job_id']}"
                conn.execute(
                    idempotency.insert().values(
                        id=uuid4(),
                        **self.scope,
                        actor_id=self.actor,
                        operation="upload",
                        key=key,
                        digest=digest,
                        response=result,
                        created_at=now(),
                    )
                )
            committed = True
            return result
        finally:
            if object_key and not committed:
                # Failed cleanup is handled by the orphan sweeper, never mask the original error.
                try:
                    with self.tx() as cleanup:
                        referenced = cleanup.execute(
                            select(versions.c.id).where(versions.c.object_key == object_key)
                        ).first()
                    if not referenced:
                        Storage(self.settings).delete(object_key)
                except Exception:
                    pass

    def listing(
        self,
        query: str,
        status: str | None,
        kind: str | None,
        cursor: str | None,
        limit: int,
        document_id: UUID | None = None,
    ) -> dict[str, Any]:
        self.require("documents.read")
        latest = aliased(jobs)
        latest_id = (
            select(latest.c.id)
            .where(latest.c.document_version_id == versions.c.id, latest.c.kind == "parsing")
            .order_by(latest.c.created_at.desc(), latest.c.id.desc())
            .limit(1)
            .correlate(versions)
            .scalar_subquery()
        )
        statement = (
            select(
                documents,
                patients.c.patient_key,
                encounter_details.c.occurred_on.label("encounter_date"),
                encounter_details.c.department,
                versions.c.id.label("version_id"),
                versions.c.mime,
                versions.c.size,
                jobs.c.id.label("job_id"),
                jobs.c.status.label("job_status"),
                jobs.c.attempt,
                jobs.c.progress,
                jobs.c.error,
                jobs.c.cancel_requested,
            )
            .join(versions, versions.c.document_id == documents.c.id)
            .join(jobs, jobs.c.id == latest_id)
            .join(patients, patients.c.id == documents.c.patient_id)
            .join(encounter_details, encounter_details.c.encounter_id == documents.c.encounter_id)
        )
        if document_id:
            statement = statement.where(documents.c.id == document_id)
        if query:
            statement = statement.where(documents.c.filename.icontains(query, autoescape=True))
        if status:
            statement = statement.where(documents.c.processing_status == status)
        if kind:
            statement = statement.where(documents.c.document_type == kind)
        if cursor:
            try:
                ts, identifier = json.loads(base64.urlsafe_b64decode(cursor))
                created, identifier = datetime.fromisoformat(ts), UUID(identifier)
                if created.tzinfo is None:
                    raise ValueError
                statement = statement.where(
                    or_(
                        documents.c.created_at > created,
                        and_(documents.c.created_at == created, documents.c.id > identifier),
                    )
                )
            except Exception:
                raise HTTPException(400) from None
        with self.tx() as conn:
            rows = [
                dict(row)
                for row in conn.execute(
                    statement.order_by(documents.c.created_at, documents.c.id).limit(limit + 1)
                ).mappings()
            ]
            items = rows[:limit]
            for item in items:
                item["max_attempts"] = self.settings.max_attempts
                item.pop("tenant_id")
                item.pop("project_id")
                item["artifact_id"] = conn.execute(
                    select(artifacts.c.id)
                    .where(artifacts.c.document_version_id == item["version_id"])
                    .order_by(artifacts.c.created_at.desc())
                    .limit(1)
                ).scalar_one_or_none()
            next_cursor = None
            if len(rows) > limit:
                last = items[-1]
                next_cursor = base64.urlsafe_b64encode(
                    json.dumps([last["created_at"].isoformat(), str(last["id"])]).encode()
                ).decode()
            return {"items": items, "next_cursor": next_cursor}

    def associate(self, document_id: UUID, body: Association) -> dict[str, Any]:
        self.require("import")
        with self.tx() as conn:
            row = (
                conn.execute(
                    select(documents).where(documents.c.id == document_id).with_for_update()
                )
                .mappings()
                .first()
            )
            if not row:
                raise HTTPException(404)
            if row["revision"] != body.expected_revision:
                raise HTTPException(409)
            from app.db.s2 import runs

            if conn.execute(
                select(runs.c.id)
                .join(versions, versions.c.id == runs.c.document_version_id)
                .where(versions.c.document_id == document_id)
            ).first():
                # Moving an extracted document requires a new explicit scope workflow (S3).
                raise HTTPException(409)
            self.validate_association(conn, body.patient_id, body.encounter_id)
            conn.execute(
                documents.update()
                .where(documents.c.id == document_id)
                .values(
                    patient_id=body.patient_id,
                    encounter_id=body.encounter_id,
                    revision=body.expected_revision + 1,
                )
            )
            self.event(
                conn,
                "document.associate",
                document_id,
                {
                    "reason": body.reason,
                    "before": {
                        "patient_id": str(row["patient_id"]),
                        "encounter_id": str(row["encounter_id"]),
                    },
                    "after": {
                        "patient_id": str(body.patient_id),
                        "encounter_id": str(body.encounter_id),
                    },
                    "revision": body.expected_revision + 1,
                },
            )
        return self.listing("", None, None, None, 1, document_id)["items"][0]  # type: ignore[no-any-return]

    def original(self, document_id: UUID) -> tuple[bytes, str]:
        self.require("original.read")
        with self.tx() as conn:
            row = (
                conn.execute(select(versions).where(versions.c.document_id == document_id))
                .mappings()
                .first()
            )
            if not row:
                raise HTTPException(404)
            if not conn.execute(
                select(artifacts.c.id).where(artifacts.c.document_version_id == row["id"])
            ).first():
                raise HTTPException(409)
            self.event(conn, "document.original.read", document_id)
        return Storage(self.settings).read(row["object_key"]), row["mime"]

    def preview(self, artifact_id: UUID, page: int | None = None) -> Any:
        self.require("original.read")
        with self.tx() as conn:
            row = (
                conn.execute(select(artifacts).where(artifacts.c.id == artifact_id))
                .mappings()
                .first()
            )
            if not row:
                raise HTTPException(404)
            self.event(conn, "document.preview.read", artifact_id)
        storage = Storage(self.settings)
        value = json.loads(storage.read(row["object_key"]))
        if page is not None:
            if not 1 <= page <= len(value["pages"]) or not value["pages"][page - 1]["image"]:
                raise HTTPException(404)
            return storage.read(row["object_key"].rsplit("/", 1)[0] + f"/page-{page}.png")
        return {"id": artifact_id, **value}


def enqueue(conn: Connection, scope: dict[str, UUID], job_id: UUID) -> None:
    conn.execute(
        outbox.insert().values(id=uuid4(), **scope, job_id=job_id, available_at=now(), deliveries=0)
    )
