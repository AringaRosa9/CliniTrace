from typing import Any
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import Connection, Table, select, text

from app.db.models import encounters
from app.db.s1 import audit, documents, jobs, versions
from app.db.s2 import facts, measurements, relations, results, runs
from app.db.s3 import revisions
from app.db.s4 import (
    annotations,
    correction_releases,
    correction_reviews,
    corrections,
    evaluations,
    gold_versions,
    samples,
    template_versions,
    terminologies,
)
from app.modules.documents.service import Service, now
from app.modules.extractions.pipeline import digest
from app.modules.quality.metrics import PROTOCOL, evaluate, label_digest
from app.modules.quality.schema import (
    AnnotationCreate,
    CorrectionRelease,
    CorrectionReview,
    EvaluationCreate,
    FreezeRequest,
    SampleCreate,
)


def lock(svc: Service, conn: Connection) -> None:
    # Shared lock: enrolling a test patient and publishing corrections must never race.
    conn.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"quality:{svc.project}"},
    )


def one(conn: Connection, table: Table, rid: UUID) -> dict[str, Any]:
    row = conn.execute(select(table).where(table.c.id == rid)).mappings().first()
    if not row:
        raise HTTPException(404)
    return dict(row)


def insert(svc: Service, conn: Connection, table: Table, **values: Any) -> dict[str, Any]:
    row = dict(id=uuid4(), **svc.scope, actor_id=svc.actor, created_at=now(), **values)
    conn.execute(table.insert().values(**row))
    svc.event(conn, table.name + ".create", row["id"])
    return row


def run_context(conn: Connection, rid: UUID) -> dict[str, Any]:
    row = (
        conn.execute(
            select(runs, documents.c.document_type, encounters.c.patient_id, jobs.c.status)
            .join(versions, versions.c.id == runs.c.document_version_id)
            .join(documents, documents.c.id == versions.c.document_id)
            .join(encounters, encounters.c.id == documents.c.encounter_id)
            .join(jobs, jobs.c.id == runs.c.job_id)
            .where(runs.c.id == rid)
        )
        .mappings()
        .first()
    )
    if not row:
        raise HTTPException(404)
    return dict(row)


def create_sample(svc: Service, body: SampleCreate) -> dict[str, Any]:
    svc.require("quality.annotate")
    svc.require("original.read")
    with svc.tx() as conn:
        lock(svc, conn)
        run = run_context(conn, body.run_id)
        if run["status"] != "succeeded" or (
            body.synthetic and svc.settings.app_env == "production"
        ):
            raise HTTPException(422)
        if run["configuration"]["provider"] == "synthetic" and not body.synthetic:
            raise HTTPException(422)
        template = one(conn, template_versions, body.template_version_id)
        if template["payload"]["document_type"] != run["document_type"]:
            raise HTTPException(422)
        group = digest([str(svc.project), body.patient_group.strip()])
        # A patient cannot change identity group or split between document samples.
        for existing in conn.execute(select(samples)).mappings():
            if existing["patient_id"] == run["patient_id"] and existing["patient_group"] != group:
                raise HTTPException(409)
            if (
                existing["patient_id"] == run["patient_id"] or existing["patient_group"] == group
            ) and existing["split"] != body.split:
                raise HTTPException(409)
        if body.split in ("test", "external"):
            for release in conn.execute(select(correction_releases.c.manifest)).scalars():
                if (
                    str(run["patient_id"]) in release["patient_ids"]
                    or group in release["patient_groups"]
                ):
                    raise HTTPException(409)
        payload = body.model_dump(mode="json", exclude={"patient_group", "run_id", "split"}) | {
            "document_type": run["document_type"],
            "document_version_id": str(run["document_version_id"]),
            "parse_artifact_id": str(run["parse_artifact_id"]),
            "template_digest": template["digest"],
            "guide_version": template["version"],
        }
        row = insert(
            svc,
            conn,
            samples,
            run_id=body.run_id,
            patient_id=run["patient_id"],
            patient_group=group,
            split=body.split,
            payload=payload,
        )
        return row | {"state": "awaiting_annotation", "annotations": []}


def sample_view(conn: Connection, sample: dict[str, Any]) -> dict[str, Any]:
    entries = [
        dict(r)
        for r in conn.execute(
            select(annotations)
            .where(annotations.c.sample_id == sample["id"])
            .order_by(annotations.c.created_at)
        ).mappings()
    ]
    stages = {e["stage"]: e for e in entries}
    state = "awaiting_annotation"
    if "annotation" in stages:
        state = "awaiting_review"
    if "review" in stages:
        state = (
            "accepted"
            if label_digest(stages["annotation"]["payload"]["labels"])
            == label_digest(stages["review"]["payload"]["labels"])
            else "disputed"
        )
    if "adjudication" in stages:
        state = "accepted"
    return sample | {"state": state, "annotations": entries}


def annotate(svc: Service, sid: UUID, body: AnnotationCreate) -> dict[str, Any]:
    from app.modules.reviews.service import validate

    svc.require(
        {
            "annotation": "quality.annotate",
            "review": "quality.review",
            "adjudication": "quality.adjudicate",
        }[body.stage]
    )
    svc.require("original.read")
    with svc.tx() as conn:
        lock(svc, conn)
        sample = sample_view(conn, one(conn, samples, sid))
        expected = {
            "annotation": "awaiting_annotation",
            "review": "awaiting_review",
            "adjudication": "disputed",
        }
        if sample["state"] != expected[body.stage]:
            raise HTTPException(409)
        if svc.actor in {a["actor_id"] for a in sample["annotations"]}:
            raise HTTPException(403)
        kind = sample["payload"]["document_type"]
        allowed = (
            {
                "diagnoses",
                "history",
                "duration",
                "medications.name",
                "medications.dose",
                "medications.frequency",
            }
            if kind == "outpatient"
            else {
                "observations.name",
                "observations.result",
                "observations.specimen",
                "observations.method",
            }
        )
        for fact in body.labels.facts:
            if fact.field_path not in allowed:
                raise HTTPException(422)
            validate(svc, conn, sample["run_id"], fact)
        for coding in body.labels.codings:
            if coding.code is not None:
                vocab = conn.execute(
                    select(terminologies.c.payload).where(terminologies.c.version == coding.version)
                ).scalar_one_or_none()
                if (
                    not vocab
                    or vocab["system"] != coding.system
                    or not vocab["authorization_reference"].strip()
                    or not any(t["code"] == coding.code for t in vocab["terms"])
                ):
                    raise HTTPException(422)
        payload = body.model_dump(mode="json")
        insert(svc, conn, annotations, sample_id=sid, stage=body.stage, payload=payload)
        return sample_view(conn, one(conn, samples, sid))


def freeze(svc: Service, body: FreezeRequest) -> dict[str, Any]:
    svc.require("quality.manage")
    svc.require("original.read")
    if len(set(body.sample_ids)) != len(body.sample_ids):
        raise HTTPException(422)
    with svc.tx() as conn:
        lock(svc, conn)
        members = []
        for sid in sorted(body.sample_ids, key=str):
            sample = sample_view(conn, one(conn, samples, sid))
            if sample["state"] != "accepted":
                raise HTTPException(422)
            decision = sample["annotations"][-1]
            members.append(
                {
                    "sample_id": str(sid),
                    "patient_id": str(sample["patient_id"]),
                    "patient_group": sample["patient_group"],
                    "split": sample["split"],
                    "source": sample["payload"],
                    "annotation_ids": [str(a["id"]) for a in sample["annotations"]],
                    "gold": decision["payload"]["labels"],
                    "active_seconds": sum(
                        a["payload"]["active_seconds"] for a in sample["annotations"]
                    ),
                }
            )
        splits = {m["split"] for m in members}
        if len(splits) != 1:
            raise HTTPException(422)
        manifest = {
            "protocol": PROTOCOL,
            "split": members[0]["split"],
            "members": members,
            "sample_count": len(members),
            "synthetic": any(m["source"]["synthetic"] for m in members),
        }
        return insert(
            svc, conn, gold_versions, name=body.name, manifest=manifest, digest=digest(manifest)
        )


def evaluate_version(svc: Service, body: EvaluationCreate) -> dict[str, Any]:
    svc.require("quality.manage")
    svc.require("original.read")
    with svc.tx() as conn:
        dataset = one(conn, gold_versions, body.dataset_id)
        members = dataset["manifest"]["members"]
        if set(body.predictions) != {UUID(m["sample_id"]) for m in members}:
            raise HTTPException(422)
        records = []
        for member in members:
            run = run_context(conn, body.predictions[UUID(member["sample_id"])])
            if (
                str(run["document_version_id"]) != member["source"]["document_version_id"]
                or str(run["patient_id"]) != member["patient_id"]
            ):
                raise HTTPException(422)
            if run["status"] not in ("succeeded", "failed", "cancelled"):
                raise HTTPException(409)
            result = (
                conn.execute(select(results).where(results.c.run_id == run["id"]))
                .mappings()
                .first()
            )
            # Model output only. Never evaluate on post-hoc human corrections.
            original = [
                f
                for f in conn.execute(
                    select(facts.c.payload).where(facts.c.run_id == run["id"])
                ).scalars()
                if f.get("reason") is None
            ]
            ids = {f["fact_id"] for f in original}
            links = [
                {k: str(v) for k, v in r.items()}
                for r in conn.execute(
                    select(
                        relations.c.id,
                        relations.c.source_id,
                        relations.c.target_id,
                        relations.c.kind,
                    ).where(relations.c.run_id == run["id"])
                ).mappings()
                if str(r["source_id"]) in ids and str(r["target_id"]) in ids
            ]
            predicted_codings = []
            # Candidate retrieval is not a confirmed prediction: record abstention explicitly.
            for c in result["codings"] if result else []:
                predicted_codings.append(
                    {
                        "fact_id": c["fact_id"],
                        "system": c["candidates"][0]["system"] if c["candidates"] else "unmapped",
                        "version": c["version"],
                        "code": None,
                    }
                )
            attempts = list(
                conn.execute(
                    select(measurements).where(measurements.c.run_id == run["id"])
                ).mappings()
            )
            from decimal import Decimal

            cost = (
                str(sum((Decimal(m["usage"]["cost_amount"]) for m in attempts), Decimal(0)))
                if attempts
                and all(m["usage"] and m["usage"].get("cost_amount") is not None for m in attempts)
                else None
            )
            human_events = list(
                conn.execute(
                    select(audit).where(audit.c.action.in_(["review.activity", "review.return"]))
                ).mappings()
            )
            activity = [
                e["details"]
                for e in human_events
                if e["action"] == "review.activity"
                and str(run["id"]) in e["details"].get("run_ids", [])
            ]
            change_count = len(
                list(
                    conn.execute(
                        select(revisions.c.id)
                        .join(facts, facts.c.id == revisions.c.fact_id)
                        .where(facts.c.run_id == run["id"])
                    ).scalars()
                )
            )
            records.append(
                {
                    "sample_id": member["sample_id"],
                    "patient_id": member["patient_id"],
                    "prediction_patient_id": str(run["patient_id"]),
                    "document_type": member["source"]["document_type"],
                    "gold": member["gold"],
                    "prediction": {
                        "facts": original,
                        "relations": links,
                        "codings": predicted_codings,
                    },
                    "run_id": str(run["id"]),
                    "configuration": run["configuration"],
                    "input_digest": run["input_digest"],
                    "duration_ms": sum(m["duration_ms"] for m in attempts) if attempts else None,
                    "cost_amount": cost,
                    "active_seconds": sum(
                        a["active_seconds"] / max(1, len(a["run_ids"])) for a in activity
                    )
                    if activity
                    else None,
                    "annotation_seconds": member["active_seconds"],
                    "modifications": change_count,
                    "returned": any(
                        str(run["id"]) in e["details"].get("run_ids", [])
                        for e in human_events
                        if e["action"] == "review.return"
                    ),
                    "failed": run["status"] != "succeeded",
                }
            )
        report = evaluate(records)
        payload = {
            "dataset_digest": dataset["digest"],
            "protocol": PROTOCOL,
            "records_digest": digest(records),
            "records": records,
            "report": report,
            "synthetic": dataset["manifest"]["synthetic"]
            or any(r["configuration"]["provider"] == "synthetic" for r in records),
            "measurement_notes": (
                "人工审核按活动区间采集，60 秒闲置切分；同范围多文书均分。"
                "标注时间单列。模型费用不含 OCR/基础设施成本，未知项显示 N/A。"
            ),
        }
        return insert(
            svc, conn, evaluations, dataset_id=body.dataset_id, name=body.name, payload=payload
        )


def capture_correction(svc: Service, conn: Connection, revision: dict[str, Any], rid: UUID) -> None:
    run = run_context(conn, rid)
    insert(
        svc,
        conn,
        corrections,
        revision_id=UUID(revision["revision_id"]),
        patient_id=run["patient_id"],
        payload={
            "run_id": str(rid),
            "fact": revision,
            "document_version_id": str(run["document_version_id"]),
            "status": "pending_secondary_review",
        },
    )


def review_correction(svc: Service, cid: UUID, body: CorrectionReview) -> dict[str, Any]:
    svc.require("quality.review")
    svc.require("original.read")
    with svc.tx() as conn:
        lock(svc, conn)
        candidate = one(conn, corrections, cid)
        if candidate["actor_id"] == svc.actor:
            raise HTTPException(403)
        insert(svc, conn, correction_reviews, candidate_id=cid, **body.model_dump())
        return candidate


def release_corrections(svc: Service, body: CorrectionRelease) -> dict[str, Any]:
    svc.require("quality.manage")
    svc.require("original.read")
    if len(set(body.candidate_ids)) != len(body.candidate_ids):
        raise HTTPException(422)
    with svc.tx() as conn:
        lock(svc, conn)
        members: list[dict[str, Any]] = []
        patient_ids: set[str] = set()
        groups: set[str] = set()
        for cid in sorted(body.candidate_ids, key=str):
            candidate = one(conn, corrections, cid)
            review = (
                conn.execute(
                    select(correction_reviews).where(correction_reviews.c.candidate_id == cid)
                )
                .mappings()
                .first()
            )
            if not review or review["decision"] != "accepted":
                raise HTTPException(422)
            memberships = list(
                conn.execute(
                    select(samples).where(samples.c.patient_id == candidate["patient_id"])
                ).mappings()
            )
            if not memberships:
                raise HTTPException(422)
            if any(m["split"] in ("test", "external") for m in memberships):
                raise HTTPException(409)
            patient_ids.add(str(candidate["patient_id"]))
            groups.update(m["patient_group"] for m in memberships)
            members.append(
                {
                    "candidate_id": str(cid),
                    "review_id": str(review["id"]),
                    "payload": candidate["payload"],
                }
            )
        # Includes cross-source aliases sharing a controlled patient group.
        for s in conn.execute(select(samples)).mappings():
            if s["split"] in ("test", "external") and s["patient_group"] in groups:
                raise HTTPException(409)
        manifest = {
            "purpose": "training_candidates_only",
            "patient_ids": sorted(patient_ids),
            "patient_groups": sorted(groups),
            "members": members,
        }
        return insert(
            svc,
            conn,
            correction_releases,
            name=body.name,
            manifest=manifest,
            digest=digest(manifest),
        )


def public_evaluation(row: dict[str, Any]) -> dict[str, Any]:
    # Summary permission never exposes per-sample IDs, source text or prediction input.
    payload = {k: v for k, v in row["payload"].items() if k != "records"}
    payload["report"] = {k: v for k, v in payload["report"].items() if k != "errors"}
    return row | {"payload": payload}


def record_activity(
    svc: Service, sid: UUID, session_id: UUID, active_seconds: int
) -> dict[str, int]:
    from app.modules.reviews.service import locked_set

    svc.require("review")
    svc.require("original.read")
    with svc.tx() as conn:
        review = locked_set(conn, svc, sid)
        previous = list(
            conn.execute(
                select(audit.c.details).where(
                    audit.c.action == "review.activity",
                    audit.c.target_id == sid,
                    audit.c.actor_id == svc.actor,
                    audit.c.details["session_id"].as_string() == str(session_id),
                )
            ).scalars()
        )
        maximum = max((p["cumulative_seconds"] for p in previous), default=0)
        delta = max(0, active_seconds - maximum)
        if delta:
            svc.event(
                conn,
                "review.activity",
                sid,
                {
                    "session_id": str(session_id),
                    "cumulative_seconds": active_seconds,
                    "active_seconds": delta,
                    "scope_revision": review["scope_revision"],
                    "run_ids": [m["extraction_run_id"] for m in review["members"]],
                },
            )
        return {"recorded_seconds": delta}
