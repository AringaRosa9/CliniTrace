from typing import Any
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import Connection, select, text

from app.db.s1 import documents
from app.db.s2 import active_runs, facts, review_sets
from app.db.s4 import activations, mappings, terminologies
from app.modules.documents.service import Service, now
from app.modules.extractions.pipeline import digest
from app.modules.terminology.schema import MappingRequest, TerminologyImport


def active(conn: Connection) -> dict[str, Any] | None:
    row = (
        conn.execute(
            select(terminologies)
            .join(activations, activations.c.terminology_id == terminologies.c.id)
            .order_by(activations.c.created_at.desc(), activations.c.id)
            .limit(1)
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None


def import_version(svc: Service, body: TerminologyImport) -> dict[str, Any]:
    svc.require("terminology.manage")
    if len({t.code for t in body.terms}) != len(body.terms) or any(
        not a.strip() for t in body.terms for a in t.aliases
    ):
        raise HTTPException(422)
    if body.synthetic and svc.settings.app_env == "production":
        raise HTTPException(422)
    row = dict(
        id=uuid4(),
        **svc.scope,
        version=body.version,
        payload=body.model_dump(),
        digest=digest(body.model_dump()),
        actor_id=svc.actor,
        created_at=now(),
    )
    with svc.tx() as conn:
        conn.execute(terminologies.insert().values(**row))
        svc.event(conn, "terminology.import", row["id"], {"digest": row["digest"]})
    return row


def activate(svc: Service, tid: UUID) -> dict[str, Any]:
    svc.require("terminology.manage")
    with svc.tx() as conn:
        conn.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"terms:{svc.project}"},
        )
        row = (
            conn.execute(select(terminologies).where(terminologies.c.id == tid)).mappings().first()
        )
        if not row:
            raise HTTPException(404)
        payload = row["payload"]
        if not payload["authorization_reference"].strip() or (
            payload["synthetic"] and svc.settings.app_env == "production"
        ):
            raise HTTPException(422)
        current = active(conn)
        if not current or current["id"] != tid:
            conn.execute(
                activations.insert().values(
                    id=uuid4(),
                    **svc.scope,
                    terminology_id=tid,
                    actor_id=svc.actor,
                    created_at=now(),
                )
            )
            svc.event(conn, "terminology.activate", tid)
        return dict(row) | {"active": True}


def search(payload: dict[str, Any], query: str) -> list[dict[str, str]]:
    return [
        {
            "code": t["code"],
            "display": t["display"],
            "system": payload["system"],
            "version": payload["version"],
            "basis": t["context"],
        }
        for t in payload["terms"]
        if query.casefold() in t["display"].casefold()
        or query in t["aliases"]
        or query == t["code"]
    ][:50]


def decide(svc: Service, fid: UUID, body: MappingRequest) -> dict[str, Any]:
    from app.modules.reviews.service import current_facts, locked_set, refresh

    svc.require("terminology.map")
    svc.require("original.read")
    with svc.tx() as conn:
        original = conn.execute(select(facts).where(facts.c.id == fid)).mappings().first()
        if not original:
            raise HTTPException(404)
        sid = conn.execute(
            select(review_sets.c.id)
            .join(documents, documents.c.encounter_id == review_sets.c.encounter_id)
            .join(active_runs, active_runs.c.document_id == documents.c.id)
            .where(active_runs.c.run_id == original["run_id"])
        ).scalar_one_or_none()
        if sid is None:
            raise HTTPException(409)
        row = locked_set(conn, svc, sid)
        current = next(
            f for f in current_facts(conn, original["run_id"]) if f["fact_id"] == str(fid)
        )
        if current["revision_id"] != str(body.revision_id) or current["excluded"]:
            raise HTTPException(409)
        if current["field_path"] not in ("diagnoses", "history", "observations.name"):
            raise HTTPException(422)
        seq = (
            conn.execute(
                select(mappings.c.sequence)
                .where(mappings.c.fact_id == fid)
                .order_by(mappings.c.sequence.desc())
                .limit(1)
            ).scalar_one_or_none()
            or 0
        )
        if seq != body.expected_sequence:
            raise HTTPException(409)
        terms = (
            conn.execute(select(terminologies).where(terminologies.c.id == body.terminology_id))
            .mappings()
            .first()
        )
        if not terms:
            raise HTTPException(404)
        if not terms["payload"]["authorization_reference"].strip():
            raise HTTPException(422)
        selected = next((t for t in terms["payload"]["terms"] if t["code"] == body.code), None)
        if (body.status == "confirmed" and selected is None) or (
            body.status != "confirmed" and body.code is not None
        ):
            raise HTTPException(422)
        payload = body.model_dump(mode="json") | {
            "system": terms["payload"]["system"],
            "version": terms["version"],
            "term": selected,
        }
        result = dict(
            id=uuid4(),
            **svc.scope,
            fact_id=fid,
            revision_id=body.revision_id,
            terminology_id=body.terminology_id,
            sequence=seq + 1,
            payload=payload,
            actor_id=svc.actor,
            created_at=now(),
        )
        conn.execute(mappings.insert().values(**result))
        refresh(
            conn,
            svc,
            row,
            "terminology.decide",
            {"fact_id": str(fid), "decision_id": str(result["id"])},
        )
        return result


def enrich(conn: Connection, codings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for coding in codings:
        latest = (
            conn.execute(
                select(mappings)
                .where(
                    mappings.c.fact_id == UUID(coding["fact_id"]),
                    mappings.c.revision_id == UUID(coding["revision_id"]),
                )
                .order_by(mappings.c.sequence.desc())
                .limit(1)
            )
            .mappings()
            .first()
        )
        if latest:
            coding.update(latest["payload"])
            coding["decision_id"] = str(latest["id"])
            coding["sequence"] = latest["sequence"]
            coding["actor_id"] = str(latest["actor_id"])
    return codings
