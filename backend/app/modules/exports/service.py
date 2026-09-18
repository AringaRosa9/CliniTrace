import csv
import hashlib
import io
import json
import re
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import Connection, select, text

from app.db.s1 import idempotency, jobs
from app.db.s2 import review_sets
from app.db.s3 import export_files, exports
from app.integrations.storage.s3 import Storage
from app.modules.documents.service import Service, enqueue, now
from app.modules.exports.schema import ExportCreate
from app.modules.extractions.pipeline import digest
from app.modules.reviews.schema import ReviewWorkspace
from app.modules.reviews.service import locked_set, workspace


def authorize(svc: Service, row: dict[str, Any]) -> None:
    svc.require("export.reviewed")
    if not row["manifest"]["reviewed_only"]:
        svc.require("export.draft")
    if row["actor_id"] != svc.actor:
        raise HTTPException(404)


def create(svc: Service, body: ExportCreate, key: str) -> dict[str, Any]:
    svc.require("export.reviewed")
    if not body.reviewed_only:
        svc.require("export.draft")
    summary = digest(body.model_dump(mode="json"))
    with svc.tx() as conn:
        conn.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"export:{svc.project}:{svc.actor}:{key}"},
        )
        previous = (
            conn.execute(
                select(idempotency).where(
                    idempotency.c.actor_id == svc.actor,
                    idempotency.c.operation == "export",
                    idempotency.c.key == key,
                )
            )
            .mappings()
            .first()
        )
        if previous:
            if previous["digest"] != summary:
                raise HTTPException(409)
            return dict(previous["response"])
        selected = []
        scopes = {
            sid: encounter
            for sid, encounter in conn.execute(
                select(review_sets.c.id, review_sets.c.encounter_id).where(
                    review_sets.c.id.in_([s.review_set_id for s in body.selections])
                )
            ).all()
        }
        if len(scopes) != len(body.selections):
            raise HTTPException(404)
        for choice in sorted(body.selections, key=lambda x: str(scopes[x.review_set_id])):
            row = locked_set(conn, svc, choice.review_set_id, choice.expected_scope_revision)
            value = workspace(conn, row)
            if body.reviewed_only and (row["status"] != "approved" or not value["snapshot_id"]):
                raise HTTPException(422)
            if row["status"] == "approved":
                from app.db.s3 import snapshots

                value = conn.execute(
                    select(snapshots.c.payload).where(snapshots.c.id == value["snapshot_id"])
                ).scalar_one()
            else:
                value = ReviewWorkspace.model_validate(value).model_dump(mode="json")
            value = dict(value)
            value["export_status"] = "reviewed" if row["status"] == "approved" else "draft"
            # Tag each exported revision, including excluded records retained for traceability.
            value["facts"] = [dict(f, review_status=value["export_status"]) for f in value["facts"]]
            selected.append(value)
        if sum(len(s["facts"]) for s in selected) > 10000:
            raise HTTPException(413)
        eid, jid, ts = uuid4(), uuid4(), now()
        manifest = {
            "version": "1.0",
            "export_id": str(eid),
            "purpose": body.purpose,
            "reviewed_only": body.reviewed_only,
            "created_at": ts.isoformat(),
            "records": selected,
            "null_semantics": (
                "normalized=null requires missing_reason; assertion is independent; "
                "excluded facts are retained and flagged"
            ),
            "csv_text_safety": (
                "All text cells with leading whitespace/control characters followed by "
                "= + - @ or leading tab/CR are prefixed with an apostrophe. "
                "JSON preserves original values."
            ),
        }
        conn.execute(
            jobs.insert().values(
                id=jid,
                **svc.scope,
                document_version_id=UUID(selected[0]["documents"][0]["version_id"]),
                kind="exporting",
                status="queued",
                attempt=0,
                generation=0,
                progress=0,
                cancel_requested=False,
                created_at=ts,
                request_id=svc.request_id,
            )
        )
        conn.execute(
            exports.insert().values(
                id=eid,
                **svc.scope,
                job_id=jid,
                actor_id=svc.actor,
                format=body.format,
                purpose=body.purpose,
                manifest=manifest,
                expires_at=ts + timedelta(hours=24),
                created_at=ts,
            )
        )
        enqueue(conn, svc.scope, jid)
        result = {
            "export_id": str(eid),
            "job_id": str(jid),
            "status_url": f"/api/v1/projects/{svc.project}/exports/{eid}",
            "reviewed_only": body.reviewed_only,
            "manifest_version": "1.0",
        }
        conn.execute(
            idempotency.insert().values(
                id=uuid4(),
                **svc.scope,
                actor_id=svc.actor,
                operation="export",
                key=key,
                digest=summary,
                response=result,
                created_at=ts,
            )
        )
        svc.event(
            conn,
            "export.create",
            eid,
            {
                "purpose": body.purpose,
                "reviewed_only": body.reviewed_only,
                "scope_ids": [str(x.review_set_id) for x in body.selections],
            },
        )
        return result


def view(svc: Service, eid: UUID) -> dict[str, Any]:
    with svc.tx() as conn:
        row = (
            conn.execute(
                select(exports, jobs.c.status, jobs.c.error)
                .join(jobs, jobs.c.id == exports.c.job_id)
                .where(exports.c.id == eid)
            )
            .mappings()
            .first()
        )
        if not row:
            raise HTTPException(404)
        authorize(svc, dict(row))
        file = (
            conn.execute(select(export_files).where(export_files.c.export_id == eid))
            .mappings()
            .first()
        )
        return {
            k: row[k]
            for k in (
                "id",
                "job_id",
                "status",
                "format",
                "purpose",
                "created_at",
                "expires_at",
                "error",
            )
        } | {
            "reviewed_only": row["manifest"]["reviewed_only"],
            "sha256": file["sha256"] if file else None,
            "size": file["size"] if file else None,
        }


def download(svc: Service, eid: UUID) -> tuple[bytes, str]:
    with svc.tx() as conn:
        row = (
            conn.execute(
                select(exports, jobs.c.status)
                .join(jobs, jobs.c.id == exports.c.job_id)
                .where(exports.c.id == eid)
            )
            .mappings()
            .first()
        )
        if not row:
            raise HTTPException(404)
        authorize(svc, dict(row))
        if row["expires_at"] <= now():
            raise HTTPException(410)
        if row["status"] != "succeeded":
            raise HTTPException(409)
        file = (
            conn.execute(select(export_files).where(export_files.c.export_id == eid))
            .mappings()
            .one()
        )
        data = Storage(svc.settings).read(file["object_key"])
        if hashlib.sha256(data).hexdigest() != file["sha256"]:
            raise HTTPException(503)
        svc.event(
            conn, "export.download", eid, {"sha256": file["sha256"], "purpose": row["purpose"]}
        )
        return data, row["format"]


def csv_safe(value: Any) -> str:
    if value is None:
        return ""
    raw = str(value)
    stripped = re.sub(r"^[\s\x00-\x1f\ufeff]*", "", raw)
    if stripped.startswith(("=", "+", "-", "@")) or raw.startswith(("\t", "\r", "\n")):
        return "'" + raw
    return raw


def render(manifest: dict[str, Any], format: str) -> bytes:
    if format == "json":
        return json.dumps(manifest, ensure_ascii=False, indent=2).encode()
    output = io.StringIO(newline="")
    fields = [
        "export_id",
        "purpose",
        "review_set_id",
        "snapshot_id",
        "scope_revision",
        "review_rules_version",
        "patient_id",
        "patient_key",
        "encounter_id",
        "review_status",
        "fact_id",
        "revision_id",
        "revision",
        "entity_group_id",
        "field_path",
        "raw",
        "normalized",
        "kind",
        "unit",
        "comparator",
        "missing_reason",
        "assertion",
        "experiencer",
        "excluded",
        "reason",
        "evidence_json",
        "relations_json",
        "event_time_json",
        "members_json",
        "configurations_json",
        "codings_json",
        "null_semantics",
    ]
    writer = csv.DictWriter(output, fieldnames=fields, lineterminator="\r\n")
    writer.writeheader()
    for record in manifest["records"]:
        for fact in record["facts"]:
            row = {k: fact.get(k) for k in fields}
            row.update(fact["value"])
            row.update(
                {
                    k: record.get(k)
                    for k in (
                        "snapshot_id",
                        "scope_revision",
                        "review_rules_version",
                        "patient_id",
                        "patient_key",
                        "encounter_id",
                    )
                }
            )
            row.update(
                export_id=manifest["export_id"],
                purpose=manifest["purpose"],
                review_set_id=record["id"],
                null_semantics=manifest["null_semantics"],
            )
            for name, value in {
                "evidence": fact["evidence"],
                "relations": [
                    r
                    for r in record["relations"]
                    if fact["fact_id"] in (r["source_id"], r["target_id"])
                ],
                "event_time": fact["event_time"],
                "members": record["members"],
                "configurations": record["configurations"],
                "codings": record["codings"],
            }.items():
                row[name + "_json"] = json.dumps(value, ensure_ascii=False)
            writer.writerow({k: csv_safe(row.get(k)) for k in fields})
    return ("\ufeff" + output.getvalue()).encode()


def publish_file(
    conn: Connection, scope: dict[str, UUID], eid: UUID, key: str, data: bytes
) -> None:
    conn.execute(
        export_files.insert().values(
            id=uuid4(),
            **scope,
            export_id=eid,
            object_key=key,
            sha256=hashlib.sha256(data).hexdigest(),
            size=len(data),
        )
    )
