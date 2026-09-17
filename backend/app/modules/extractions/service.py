import json
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import Connection, func, select, text

from app.db.s1 import artifacts, documents, idempotency, jobs, versions
from app.db.s2 import (
    active_runs,
    evidence,
    fact_evidence,
    facts,
    measurements,
    relations,
    results,
    review_sets,
    runs,
)
from app.integrations.storage.s3 import Storage
from app.modules.documents.service import Service, enqueue, now
from app.modules.extractions.pipeline import PROMPT, RULES, TERMS, compare_facts, digest, model_for
from app.modules.extractions.schema import Activation, ExtractionCreate


def create(svc: Service, document_id: UUID, body: ExtractionCreate, key: str) -> dict[str, Any]:
    svc.require("import")
    svc.require("original.read")
    cfg = svc.settings
    if cfg.extraction_provider == "disabled" or (
        cfg.extraction_provider == "synthetic" and not cfg.allow_synthetic_mock
    ):
        raise HTTPException(409)
    summary = digest([str(document_id), body.model_dump(mode="json")])
    with svc.tx() as conn:
        conn.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"extract:{svc.project}"},
        )
        previous = (
            conn.execute(
                select(idempotency).where(
                    idempotency.c.actor_id == svc.actor,
                    idempotency.c.operation == "extract",
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
        row = (
            conn.execute(
                select(documents, versions.c.id.label("version_id"), versions.c.sha256)
                .join(versions, versions.c.document_id == documents.c.id)
                .where(documents.c.id == document_id)
                .with_for_update(of=documents)
            )
            .mappings()
            .first()
        )
        if not row:
            raise HTTPException(404)
        artifact = (
            conn.execute(
                select(artifacts).where(
                    artifacts.c.id == body.parse_artifact_id,
                    artifacts.c.document_version_id == row["version_id"],
                )
            )
            .mappings()
            .first()
        )
        if not artifact:
            raise HTTPException(404)
        if artifact["needs_ocr"] or body.template_version != row["document_type"] + "-1.0.0":
            raise HTTPException(409)
        pending = conn.execute(
            select(func.count())
            .select_from(jobs)
            .where(jobs.c.kind == "extracting", jobs.c.status.in_(["queued", "running"]))
        ).scalar_one()
        recent = conn.execute(
            select(func.count())
            .select_from(runs)
            .where(runs.c.created_at > now() - timedelta(minutes=1))
        ).scalar_one()
        if pending >= cfg.extraction_max_inflight or recent >= cfg.extraction_requests_per_minute:
            raise HTTPException(429)
        parsed = json.loads(Storage(cfg).read(artifact["object_key"]))
        if not parsed["text"].strip() or len(parsed["text"]) > cfg.extraction_max_chars:
            raise HTTPException(422)
        config = {
            "template_version": body.template_version,
            "schema_digest": digest(model_for(body.template_version).model_json_schema()),
            "prompt_version": "extraction-1.0.0",
            "prompt_digest": digest(PROMPT),
            "rules_version": RULES["version"],
            "rules_digest": digest(RULES),
            "terminology_version": cfg.terminology_version,
            "terminology_digest": digest(TERMS)
            if cfg.terminology_version.startswith("synthetic")
            else None,
            "model": cfg.extraction_model
            if cfg.extraction_provider == "gateway"
            else "synthetic-grammar-1.0.0",
            "provider": cfg.extraction_provider,
            "gateway_digest": digest(cfg.extraction_gateway_url),
            "authorization": cfg.extraction_authorization,
            "parser_version": artifact["parser_version"],
            "parameters": {
                "max_chars": cfg.extraction_max_chars,
                "max_output_bytes": cfg.extraction_max_output_bytes,
                "max_repairs": cfg.extraction_max_repairs,
                "timeout_seconds": cfg.extraction_timeout_seconds,
                "max_cost_cny": "0",
            },
        }
        run_id, job_id = uuid4(), uuid4()
        conn.execute(
            jobs.insert().values(
                id=job_id,
                **svc.scope,
                document_version_id=row["version_id"],
                kind="extracting",
                status="queued",
                attempt=0,
                generation=0,
                progress=0,
                cancel_requested=False,
                created_at=now(),
                request_id=svc.request_id,
            )
        )
        conn.execute(
            runs.insert().values(
                id=run_id,
                **svc.scope,
                document_version_id=row["version_id"],
                parse_artifact_id=artifact["id"],
                job_id=job_id,
                document_revision=row["revision"],
                configuration=config,
                input_digest=digest(parsed),
                created_at=now(),
            )
        )
        enqueue(conn, svc.scope, job_id)
        response = {
            "run_id": str(run_id),
            "job_id": str(job_id),
            "status_url": f"/api/v1/projects/{svc.project}/jobs/{job_id}",
        }
        conn.execute(
            idempotency.insert().values(
                id=uuid4(),
                **svc.scope,
                actor_id=svc.actor,
                operation="extract",
                key=key,
                digest=summary,
                response=response,
                created_at=now(),
            )
        )
        svc.event(conn, "extraction.create", run_id)
        return response


def view(svc: Service, run_id: UUID) -> dict[str, Any]:
    svc.require("original.read")
    with svc.tx() as conn:
        row = (
            conn.execute(
                select(runs, jobs.c.status, jobs.c.error, jobs.c.attempt)
                .join(jobs, jobs.c.id == runs.c.job_id)
                .where(runs.c.id == run_id)
            )
            .mappings()
            .first()
        )
        if not row:
            raise HTTPException(404)
        result = conn.execute(select(results).where(results.c.run_id == run_id)).mappings().first()
        attempts_usage = [
            dict(m)
            for m in conn.execute(
                select(
                    measurements.c.generation,
                    measurements.c.usage,
                    measurements.c.duration_ms,
                    measurements.c.outcome,
                )
                .where(measurements.c.run_id == run_id)
                .order_by(measurements.c.generation)
            ).mappings()
        ]
        measured_usage = [m["usage"] for m in attempts_usage if m["usage"] is not None]
        usage = None
        if measured_usage:
            from decimal import Decimal

            complete = len(measured_usage) == row["attempt"]
            usage = {
                "provider": row["configuration"]["provider"],
                "input_tokens": sum(m["input_tokens"] for m in measured_usage),
                "output_tokens": sum(m["output_tokens"] for m in measured_usage),
                "request_ids": [rid for m in measured_usage for rid in m["request_ids"]],
                "cost_amount": str(
                    sum((Decimal(m["cost_amount"]) for m in measured_usage), Decimal(0))
                )
                if complete
                else None,
                "cost_currency": "CNY",
                "complete": complete,
                "measurement": measured_usage[0]["measurement"],
            }
        payloads = list(
            conn.execute(
                select(facts.c.payload)
                .where(facts.c.run_id == run_id)
                .order_by(facts.c.field_path, facts.c.id)
            ).scalars()
        )
        links = [
            dict(r)
            for r in conn.execute(
                select(
                    relations.c.id, relations.c.source_id, relations.c.target_id, relations.c.kind
                ).where(relations.c.run_id == run_id)
            ).mappings()
        ]
        svc.event(conn, "extraction.read", run_id)
        return {
            k: row[k]
            for k in (
                "id",
                "job_id",
                "document_version_id",
                "parse_artifact_id",
                "configuration",
                "input_digest",
                "created_at",
                "status",
                "error",
            )
        } | {
            "duration_ms": sum(m["duration_ms"] for m in attempts_usage)
            if attempts_usage
            else None,
            "attempt_measurements": attempts_usage,
            "usage": usage,
            "facts": payloads,
            "relations": links,
            "issues": result["issues"] if result else [],
            "codings": result["codings"] if result else [],
        }


def recompute(conn: Connection, scope: dict[str, UUID], encounter_id: UUID) -> None:
    # Encounter lock serializes publication, activation and scope recomputation.
    conn.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"review:{scope['project_id']}:{encounter_id}"},
    )
    selected = (
        conn.execute(
            select(active_runs.c.run_id)
            .join(documents, documents.c.id == active_runs.c.document_id)
            .where(documents.c.encounter_id == encounter_id)
        )
        .scalars()
        .all()
    )
    members, rows, issues = [], [], []
    for rid in selected:
        run = conn.execute(select(runs).where(runs.c.id == rid)).mappings().one()
        members_facts = list(
            conn.execute(select(facts.c.payload).where(facts.c.run_id == rid)).scalars()
        )
        rows.extend(members_facts)
        members.append(
            {
                "extraction_run_id": str(rid),
                "document_version_id": str(run["document_version_id"]),
                "fact_revision_ids": [f["revision_id"] for f in members_facts],
            }
        )
        issues.extend(
            conn.execute(select(results.c.issues).where(results.c.run_id == rid)).scalar_one()
        )
    issues.extend(compare_facts(rows))
    issues = list({i["id"]: i for i in issues}.values())
    current = (
        conn.execute(
            select(review_sets).where(review_sets.c.encounter_id == encounter_id).with_for_update()
        )
        .mappings()
        .first()
    )
    values = {
        "status": "pending_review",
        "members": members,
        "issues": issues,
        "scope_revision": current["scope_revision"] + 1 if current else 1,
    }
    if current:
        conn.execute(review_sets.update().where(review_sets.c.id == current["id"]).values(**values))
    else:
        conn.execute(
            review_sets.insert().values(id=uuid4(), **scope, encounter_id=encounter_id, **values)
        )


def publish(
    conn: Connection,
    scope: dict[str, UUID],
    run: dict[str, Any],
    payload: dict[str, Any],
    prefix: str,
    duration_ms: int,
) -> None:
    doc_id = conn.execute(
        select(versions.c.document_id).where(versions.c.id == run["document_version_id"])
    ).scalar_one()
    doc = (
        conn.execute(select(documents).where(documents.c.id == doc_id).with_for_update())
        .mappings()
        .one()
    )
    if doc["revision"] != run["document_revision"]:
        from app.modules.extractions.pipeline import ExtractionFailure

        raise ExtractionFailure("DOCUMENT_ASSOCIATION_CHANGED")
    conn.execute(
        results.insert().values(
            id=uuid4(),
            **scope,
            run_id=run["id"],
            raw_object_key=prefix + "/raw.json",
            usage=payload["usage"],
            duration_ms=duration_ms,
            issues=payload["issues"],
            codings=payload["codings"],
            created_at=now(),
        )
    )
    evs = {}
    for fact in payload["facts"]:
        conn.execute(
            facts.insert().values(
                id=UUID(fact["fact_id"]),
                **scope,
                run_id=run["id"],
                revision_id=UUID(fact["revision_id"]),
                entity_group_id=UUID(fact["entity_group_id"]),
                field_path=fact["field_path"],
                payload=fact,
            )
        )
        for ev in fact["evidence"]:
            if ev["id"] not in evs:
                # Pipeline assigns internal evidence IDs consistently to payload and ledger.
                evs[ev["id"]] = UUID(ev["id"])
                conn.execute(
                    evidence.insert().values(
                        id=evs[ev["id"]],
                        **scope,
                        run_id=run["id"],
                        document_version_id=run["document_version_id"],
                        parse_artifact_id=run["parse_artifact_id"],
                        payload=ev,
                    )
                )
            conn.execute(
                fact_evidence.insert().values(
                    id=uuid4(), **scope, fact_id=UUID(fact["fact_id"]), evidence_id=evs[ev["id"]]
                )
            )
    for link in payload["relations"]:
        conn.execute(
            relations.insert().values(
                **scope,
                run_id=run["id"],
                kind=link["kind"],
                **{k: UUID(link[k]) for k in ("id", "source_id", "target_id")},
            )
        )
    current = conn.execute(select(active_runs).where(active_runs.c.document_id == doc_id)).first()
    if not current:
        conn.execute(
            active_runs.insert().values(id=uuid4(), **scope, document_id=doc_id, run_id=run["id"])
        )
        recompute(conn, scope, doc["encounter_id"])


def activate(svc: Service, document_id: UUID, body: Activation) -> dict[str, Any]:
    svc.require("import")
    svc.require("original.read")
    with svc.tx() as conn:
        doc = (
            conn.execute(select(documents).where(documents.c.id == document_id).with_for_update())
            .mappings()
            .first()
        )
        if not doc:
            raise HTTPException(404)
        conn.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"review:{svc.project}:{doc['encounter_id']}"},
        )
        run = (
            conn.execute(
                select(runs)
                .join(versions, versions.c.id == runs.c.document_version_id)
                .join(results, results.c.run_id == runs.c.id)
                .where(runs.c.id == body.run_id, versions.c.document_id == document_id)
            )
            .mappings()
            .first()
        )
        if not run:
            raise HTTPException(404)
        current = (
            conn.execute(
                select(review_sets).where(review_sets.c.encounter_id == doc["encounter_id"])
            )
            .mappings()
            .first()
        )
        if (
            not current
            or current["scope_revision"] != body.expected_scope_revision
            or run["document_revision"] != doc["revision"]
        ):
            raise HTTPException(409)
        conn.execute(
            active_runs.update()
            .where(active_runs.c.document_id == document_id)
            .values(run_id=body.run_id)
        )
        recompute(conn, svc.scope, doc["encounter_id"])
        svc.event(conn, "extraction.activate", body.run_id, {"reason": body.reason})
    return review(svc, doc["encounter_id"])


def review(svc: Service, encounter_id: UUID) -> dict[str, Any]:
    svc.require("original.read")
    with svc.tx() as conn:
        row = (
            conn.execute(select(review_sets).where(review_sets.c.encounter_id == encounter_id))
            .mappings()
            .first()
        )
        if not row:
            raise HTTPException(404)
        return {
            k: row[k]
            for k in ("id", "encounter_id", "scope_revision", "status", "members", "issues")
        }


def start_ocr(svc: Service, document_id: UUID, key: str) -> dict[str, Any]:
    """Reparse an S1 scan after local OCR is enabled, retaining the old artifact."""
    svc.require("import")
    svc.require("original.read")
    if svc.settings.ocr_provider == "disabled":
        raise HTTPException(409)
    with svc.tx() as conn:
        row = (
            conn.execute(select(documents).where(documents.c.id == document_id).with_for_update())
            .mappings()
            .first()
        )
        if not row:
            raise HTTPException(404)
        previous = (
            conn.execute(
                select(idempotency).where(
                    idempotency.c.actor_id == svc.actor,
                    idempotency.c.operation == "ocr",
                    idempotency.c.key == key,
                )
            )
            .mappings()
            .first()
        )
        summary = digest(str(document_id))
        if previous:
            if previous["digest"] != summary:
                raise HTTPException(409)
            return dict(previous["response"])
        version_id = conn.execute(
            select(versions.c.id).where(versions.c.document_id == document_id)
        ).scalar_one()
        if conn.execute(select(runs.c.id).where(runs.c.document_version_id == version_id)).first():
            raise HTTPException(409)
        if conn.execute(
            select(jobs.c.id).where(
                jobs.c.document_version_id == version_id, jobs.c.status.in_(["queued", "running"])
            )
        ).first():
            raise HTTPException(409)
        jid = uuid4()
        conn.execute(
            jobs.insert().values(
                id=jid,
                **svc.scope,
                document_version_id=version_id,
                kind="parsing",
                status="queued",
                attempt=0,
                generation=0,
                progress=0,
                cancel_requested=False,
                created_at=now(),
                request_id=svc.request_id,
            )
        )
        enqueue(conn, svc.scope, jid)
        conn.execute(
            documents.update()
            .where(documents.c.id == document_id)
            .values(processing_status="queued")
        )
        response = {"job_id": str(jid), "status_url": f"/api/v1/projects/{svc.project}/jobs/{jid}"}
        conn.execute(
            idempotency.insert().values(
                id=uuid4(),
                **svc.scope,
                actor_id=svc.actor,
                operation="ocr",
                key=key,
                digest=summary,
                response=response,
                created_at=now(),
            )
        )
        svc.event(conn, "document.ocr", document_id)
        return response
