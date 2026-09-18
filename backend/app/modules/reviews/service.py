"""All human mutations serialize with publication on the encounter lock."""

import json
import re
from datetime import date
from typing import Any
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import Connection, select, text

from app.contracts.models import ExtractedItem, FactRevision
from app.db.models import encounters, patients
from app.db.s1 import artifacts, audit, documents, versions
from app.db.s2 import active_runs, facts, relations, review_sets, runs
from app.db.s3 import checks, dispositions, revisions, snapshots
from app.integrations.storage.s3 import Storage
from app.modules.documents.service import Service, now
from app.modules.extractions.pipeline import (
    ExtractionFailure,
    candidates,
    compare_facts,
    issue,
    validate_evidence,
)
from app.modules.reviews.schema import (
    Approval,
    CheckRequest,
    Correction,
    DispositionRequest,
    Exclusion,
    ReturnRequest,
    Supplement,
)


def lock(conn: Connection, project: UUID, encounter: UUID) -> None:
    conn.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"review:{project}:{encounter}"},
    )


def current_facts(conn: Connection, run_id: UUID) -> list[dict[str, Any]]:
    originals = list(
        conn.execute(
            select(facts.c.payload)
            .where(facts.c.run_id == run_id)
            .order_by(facts.c.field_path, facts.c.id)
        ).scalars()
    )
    overrides = conn.execute(
        select(revisions.c.payload)
        .join(facts, facts.c.id == revisions.c.fact_id)
        .where(facts.c.run_id == run_id)
        .order_by(revisions.c.revision)
    ).scalars()
    latest = {f["fact_id"]: f for f in originals}
    for f in overrides:
        latest[f["fact_id"]] = f
    return list(latest.values())


def rules(rows: list[dict[str, Any]], source_issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    live = [f for f in rows if not f["excluded"]]
    problems = [i for i in source_issues if i["rule"] == "OCR_LOW_QUALITY"]
    if not live:
        problems.append(issue("EMPTY_EXTRACTION", [], "未抽取到可复核事实，请核对原文和模板。"))
    for f in live:
        v, rid = f["value"], [f["revision_id"]]
        quote = "\n".join(e["quote"] for e in f["evidence"])

        def add(rule: str, message: str, ids: list[str] = rid) -> None:
            problems.append(issue(rule, ids, message))

        if v["missing_reason"]:
            add(
                "MISSING_VALUE",
                "值缺失："
                + {
                    "missing": "应有而缺失",
                    "not_mentioned": "未提及",
                    "explicitly_unknown": "明确未知",
                    "not_applicable": "不适用",
                    "unreadable": "无法辨认",
                }[v["missing_reason"]]
                + "，请确认缺失语义。",
            )
        if re.search(r"否认|无.*史|未见", quote) and v["assertion"] == "affirmed":
            add("NEGATION_MISMATCH", "证据含否定表述，需核对断言。")
        if re.search(r"不详|未知|不清", v["raw"]) and v["missing_reason"] is None:
            add("UNKNOWN_MISMATCH", "原文表示未知，不得补写为已知值。")
        if f["field_path"] == "observations.result" and v["kind"] == "decimal" and not v["unit"]:
            add("MISSING_UNIT", "检验结果缺少原始单位，请核对。")
        if v["kind"] == "date" and v["normalized"]:
            try:
                date.fromisoformat(v["normalized"])
            except ValueError:
                add("INVALID_DATE", "日期格式或日历值无效。")
        if f["field_path"] == "medications.name" and not any(
            x["entity_group_id"] == f["entity_group_id"] and x["field_path"] == "medications.dose"
            for x in live
        ):
            add("MISSING_DOSE", "药物剂量未提及；不得按常规用量补全。")
        if f["field_path"] == "observations.name" and not any(
            x["entity_group_id"] == f["entity_group_id"]
            and x["field_path"] == "observations.result"
            for x in live
        ):
            problems.append(
                {
                    "id": "result-" + f["revision_id"],
                    "rule": "MISSING_RESULT",
                    "severity": "blocking",
                    "fact_revision_ids": rid,
                    "message": "检验项目缺少结果，请依据原文补录或排除。",
                    "can_accept_unknown": False,
                    "status": "open",
                }
            )
        if f["field_path"] in ("diagnoses", "history", "observations.name"):
            add("PENDING_MAPPING", "术语编码尚未确认，保留原文。")
        if f["field_path"].startswith(("medications.", "observations.")) and not f[
            "field_path"
        ].endswith(".name"):
            prefix = f["field_path"].split(".")[0]
            if not any(
                x["entity_group_id"] == f["entity_group_id"] and x["field_path"] == prefix + ".name"
                for x in live
            ):
                problems.append(
                    {
                        "id": "orphan-" + f["revision_id"],
                        "rule": "ORPHAN_RELATION",
                        "severity": "blocking",
                        "fact_revision_ids": rid,
                        "message": "子字段缺少有效的药物或检验名称，请补录或排除。",
                        "can_accept_unknown": False,
                        "status": "open",
                    }
                )
    problems.extend(compare_facts(live))
    return list({i["id"]: i for i in problems}.values())


def locked_set(
    conn: Connection, svc: Service, sid: UUID, expected: int | None = None
) -> dict[str, Any]:
    row = conn.execute(select(review_sets).where(review_sets.c.id == sid)).mappings().first()
    if not row:
        raise HTTPException(404)
    lock(conn, svc.project, row["encounter_id"])
    row = (
        conn.execute(select(review_sets).where(review_sets.c.id == sid).with_for_update())
        .mappings()
        .one()
    )
    if expected is not None and row["scope_revision"] != expected:
        raise HTTPException(409)
    return dict(row)


def workspace(conn: Connection, row: dict[str, Any]) -> dict[str, Any]:
    sid = row["id"]
    patient = (
        conn.execute(
            select(patients.c.id, patients.c.patient_key)
            .join(encounters, encounters.c.patient_id == patients.c.id)
            .where(encounters.c.id == row["encounter_id"])
        )
        .mappings()
        .one()
    )
    docs = [
        dict(d)
        for d in conn.execute(
            select(
                documents.c.id,
                documents.c.filename,
                versions.c.id.label("version_id"),
                active_runs.c.run_id,
                runs.c.parse_artifact_id,
            )
            .join(versions, versions.c.document_id == documents.c.id)
            .outerjoin(active_runs, active_runs.c.document_id == documents.c.id)
            .outerjoin(runs, runs.c.id == active_runs.c.run_id)
            .where(documents.c.encounter_id == row["encounter_id"])
            .order_by(documents.c.created_at)
        ).mappings()
    ]
    rows: list[dict[str, Any]] = []
    originals: list[dict[str, Any]] = []
    links: list[dict[str, Any]] = []
    configs: dict[str, Any] = {}
    codings: list[dict[str, Any]] = []
    for member in row["members"]:
        rid = UUID(member["extraction_run_id"])
        run_facts = current_facts(conn, rid)
        rows.extend(run_facts)
        originals.extend(
            conn.execute(select(facts.c.payload).where(facts.c.run_id == rid)).scalars()
        )
        links.extend(
            dict(r)
            for r in conn.execute(
                select(
                    relations.c.id, relations.c.source_id, relations.c.target_id, relations.c.kind
                ).where(relations.c.run_id == rid)
            ).mappings()
        )
        configs[str(rid)] = conn.execute(
            select(runs.c.configuration).where(runs.c.id == rid)
        ).scalar_one()
        terminology = configs[str(rid)]["terminology_version"]
        for f in run_facts:
            if (
                f["field_path"] in ("diagnoses", "history", "observations.name")
                and not f["excluded"]
            ):
                found = candidates(f["value"]["normalized"] or f["value"]["raw"], terminology)
                codings.append(
                    {
                        "fact_id": f["fact_id"],
                        "revision_id": f["revision_id"],
                        "version": terminology,
                        "status": "pending" if found else "unmapped",
                        "candidates": found,
                        "reason": "依据当前修订检索，仅候选，保留待映射。",
                    }
                )
    # Relations for supplements are materialized in the relation ledger.
    decisions = {
        d["issue_id"]: d
        for d in conn.execute(
            select(dispositions).where(
                dispositions.c.review_set_id == sid,
                dispositions.c.scope_revision == row["scope_revision"],
            )
        ).mappings()
    }
    problems = [dict(i) for i in row["issues"]]
    for i in problems:
        if i["id"] in decisions:
            i.update(status=decisions[i["id"]]["status"], reason=decisions[i["id"]]["reason"])
    checked = list(
        conn.execute(
            select(checks.c.fact_revision_id).where(
                checks.c.review_set_id == sid, checks.c.scope_revision == row["scope_revision"]
            )
        ).scalars()
    )
    snapshot = conn.execute(
        select(snapshots.c.id).where(
            snapshots.c.review_set_id == sid, snapshots.c.scope_revision == row["scope_revision"]
        )
    ).scalar_one_or_none()
    return {k: row[k] for k in ("id", "encounter_id", "scope_revision", "status", "members")} | {
        "patient_id": patient["id"],
        "patient_key": patient["patient_key"],
        "documents": docs,
        "facts": rows,
        "originals": originals,
        "relations": links,
        "issues": problems,
        "checked_revision_ids": checked,
        "snapshot_id": snapshot,
        "configurations": configs,
        "codings": codings,
    }


def get(svc: Service, sid: UUID) -> dict[str, Any]:
    svc.require("review")
    svc.require("original.read")
    with svc.tx() as conn:
        row = locked_set(conn, svc, sid)
        value = workspace(conn, row)
        svc.event(conn, "review.read", sid)
        return value


def refresh(
    conn: Connection, svc: Service, row: dict[str, Any], action: str, details: dict[str, Any]
) -> None:
    from app.modules.extractions.service import recompute

    recompute(conn, svc.scope, row["encounter_id"])
    svc.event(conn, action, row["id"], details)


def validate(svc: Service, conn: Connection, rid: UUID, fact: FactRevision) -> FactRevision:
    run = conn.execute(select(runs).where(runs.c.id == rid)).mappings().one()
    artifact = (
        conn.execute(select(artifacts).where(artifacts.c.id == run["parse_artifact_id"]))
        .mappings()
        .one()
    )
    parsed = json.loads(Storage(svc.settings).read(artifact["object_key"]))
    item = ExtractedItem(
        entity_group_id=fact.entity_group_id,
        value=fact.value,
        evidence=fact.evidence,
        event_time=fact.event_time,
    )
    try:
        validate_evidence(item, parsed, str(run["document_version_id"]), str(artifact["id"]))
    except ExtractionFailure:
        raise HTTPException(422) from None
    # Server assigns evidence identities; clients cannot alias different quotes.
    fact.evidence = [e.model_copy(update={"id": uuid4()}) for e in item.evidence]
    return fact


def edit(svc: Service, fid: UUID, body: Correction | Exclusion) -> dict[str, Any]:
    svc.require("review")
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
        if not sid:
            raise HTTPException(409)
        row = locked_set(conn, svc, sid, body.expected_scope_revision)
        old = next(f for f in current_facts(conn, original["run_id"]) if f["fact_id"] == str(fid))
        if old["revision"] != body.expected_revision:
            raise HTTPException(409)
        patch = {
            "revision_id": str(uuid4()),
            "revision": old["revision"] + 1,
            "reason": body.reason,
        }
        if isinstance(body, Correction):
            patch.update(body.model_dump(mode="json", include={"value", "evidence", "event_time"}))
            patch["excluded"] = False
        else:
            patch["excluded"] = True
        fact = FactRevision.model_validate(old | patch)
        if isinstance(body, Correction):
            fact = validate(svc, conn, original["run_id"], fact)
        payload = fact.model_dump(mode="json")
        conn.execute(
            revisions.insert().values(
                id=fact.revision_id,
                **svc.scope,
                fact_id=fid,
                revision=fact.revision,
                payload=payload,
                actor_id=svc.actor,
                created_at=now(),
            )
        )
        refresh(
            conn,
            svc,
            row,
            "fact.exclude" if fact.excluded else "fact.edit",
            {"fact_id": str(fid), "before": old, "after": payload, "reason": body.reason},
        )
        return payload


def supplement(svc: Service, body: Supplement) -> dict[str, Any]:
    svc.require("review")
    svc.require("original.read")
    with svc.tx() as conn:
        row = locked_set(conn, svc, body.review_set_id, body.expected_scope_revision)
        if str(body.run_id) not in [m["extraction_run_id"] for m in row["members"]]:
            raise HTTPException(404)
        config = conn.execute(
            select(runs.c.configuration).where(runs.c.id == body.run_id)
        ).scalar_one()
        allowed = (
            {
                "diagnoses",
                "history",
                "duration",
                "medications.name",
                "medications.dose",
                "medications.frequency",
            }
            if config["template_version"].startswith("outpatient")
            else {
                "observations.name",
                "observations.result",
                "observations.specimen",
                "observations.method",
            }
        )
        if body.field_path not in allowed:
            raise HTTPException(422)
        current = current_facts(conn, body.run_id)
        gid = body.entity_group_id or uuid4()
        if body.entity_group_id and not any(f["entity_group_id"] == str(gid) for f in current):
            raise HTTPException(404)
        if any(
            f["entity_group_id"] == str(gid)
            and f["field_path"] == body.field_path
            and not f["excluded"]
            for f in current
        ):
            raise HTTPException(409)
        fact = FactRevision(
            fact_id=uuid4(),
            revision_id=uuid4(),
            revision=1,
            entity_group_id=gid,
            field_path=body.field_path,
            value=body.value,
            evidence=body.evidence,
            event_time=body.event_time,
            reason=body.reason,
        )
        fact = validate(svc, conn, body.run_id, fact)
        payload = fact.model_dump(mode="json")
        conn.execute(
            facts.insert().values(
                id=fact.fact_id,
                **svc.scope,
                run_id=body.run_id,
                revision_id=fact.revision_id,
                entity_group_id=gid,
                field_path=fact.field_path,
                payload=payload,
            )
        )
        group = [f for f in current if f["entity_group_id"] == str(gid)] + [payload]
        root = next(
            (f for f in group if f["field_path"].endswith(".name") and not f["excluded"]), None
        )
        if root:
            for child in group:
                if child["fact_id"] != root["fact_id"] and (child == payload or root == payload):
                    conn.execute(
                        relations.insert().values(
                            id=uuid4(),
                            **svc.scope,
                            run_id=body.run_id,
                            source_id=UUID(root["fact_id"]),
                            target_id=UUID(child["fact_id"]),
                            kind=child["field_path"],
                        )
                    )
        refresh(conn, svc, row, "fact.supplement", {"after": payload, "reason": body.reason})
        return payload


def check(svc: Service, sid: UUID, body: CheckRequest) -> dict[str, Any]:
    svc.require("review")
    svc.require("original.read")
    with svc.tx() as conn:
        row = locked_set(conn, svc, sid, body.expected_scope_revision)
        value = workspace(conn, row)
        if str(body.fact_revision_id) not in [f["revision_id"] for f in value["facts"]]:
            raise HTTPException(409)
        if body.fact_revision_id not in value["checked_revision_ids"]:
            conn.execute(
                checks.insert().values(
                    id=uuid4(),
                    **svc.scope,
                    review_set_id=sid,
                    scope_revision=row["scope_revision"],
                    fact_revision_id=body.fact_revision_id,
                    actor_id=svc.actor,
                    created_at=now(),
                )
            )
            svc.event(conn, "fact.check", sid, {"fact_revision_id": str(body.fact_revision_id)})
    return get(svc, sid)


def dispose(svc: Service, iid: str, body: DispositionRequest) -> dict[str, Any]:
    svc.require("review")
    svc.require("original.read")
    with svc.tx() as conn:
        row = locked_set(conn, svc, body.review_set_id, body.expected_scope_revision)
        if row["status"] != "pending_review":
            raise HTTPException(409)
        problem = next((i for i in row["issues"] if i["id"] == iid), None)
        if not problem:
            raise HTTPException(404)
        if body.status == "accepted_unknown" and not problem["can_accept_unknown"]:
            raise HTTPException(422)
        # Structural/semantic errors must be fixed, never dismissed by a checkbox.
        if body.status == "resolved" and problem["rule"] not in (
            "CONFLICT",
            "DUPLICATE",
            "OCR_LOW_QUALITY",
            "MISSING_DIAGNOSIS",
        ):
            raise HTTPException(422)
        conn.execute(
            dispositions.insert().values(
                id=uuid4(),
                **svc.scope,
                review_set_id=row["id"],
                scope_revision=row["scope_revision"],
                issue_id=iid,
                status=body.status,
                reason=body.reason,
                actor_id=svc.actor,
                created_at=now(),
            )
        )
        svc.event(
            conn,
            "issue.dispose",
            row["id"],
            {"issue_id": iid, "status": body.status, "reason": body.reason},
        )
    return get(svc, body.review_set_id)


def approve(svc: Service, body: Approval) -> dict[str, Any]:
    svc.require("review")
    svc.require("original.read")
    with svc.tx() as conn:
        row = locked_set(conn, svc, body.review_set_id, body.expected_scope_revision)
        existing = (
            conn.execute(
                select(snapshots).where(
                    snapshots.c.review_set_id == row["id"],
                    snapshots.c.scope_revision == row["scope_revision"],
                )
            )
            .mappings()
            .first()
        )
        if existing:
            return {
                k: existing[k]
                for k in (
                    "id",
                    "review_set_id",
                    "scope_revision",
                    "created_at",
                    "actor_id",
                    "payload",
                )
            }
        value = workspace(conn, row)
        if (
            row["status"] == "returned"
            or not value["facts"]
            or any(d["run_id"] is None for d in value["documents"])
        ):
            raise HTTPException(422)
        if any(i["severity"] == "blocking" and i["status"] == "open" for i in value["issues"]):
            raise HTTPException(422)
        if {f["revision_id"] for f in value["facts"]} != {
            str(i) for i in value["checked_revision_ids"]
        }:
            raise HTTPException(422)
        sid, ts = uuid4(), now()
        # Serialization fixes the entire membership, facts, evidence and version bundle now.
        from app.modules.reviews.schema import ReviewWorkspace

        payload = ReviewWorkspace.model_validate(value).model_dump(mode="json")
        payload.update(
            status="approved",
            snapshot_id=str(sid),
            reviewed_at=ts.isoformat(),
            reviewer_id=str(svc.actor),
            final_confirmation=True,
        )
        result = {
            "id": sid,
            "review_set_id": row["id"],
            "scope_revision": row["scope_revision"],
            "created_at": ts,
            "actor_id": svc.actor,
            "payload": payload,
        }
        conn.execute(snapshots.insert().values(**svc.scope, **result))
        conn.execute(
            review_sets.update().where(review_sets.c.id == row["id"]).values(status="approved")
        )
        svc.event(
            conn,
            "review.approve",
            row["id"],
            {"snapshot_id": str(sid), "scope_revision": row["scope_revision"]},
        )
        return result


def return_material(svc: Service, sid: UUID, body: ReturnRequest) -> dict[str, Any]:
    svc.require("review")
    svc.require("original.read")
    with svc.tx() as conn:
        row = locked_set(conn, svc, sid, body.expected_scope_revision)
        refresh(conn, svc, row, "review.return", body.model_dump(mode="json"))
        conn.execute(review_sets.update().where(review_sets.c.id == sid).values(status="returned"))
    return get(svc, sid)


def history(svc: Service, sid: UUID) -> dict[str, Any]:
    svc.require("review")
    svc.require("original.read")
    with svc.tx() as conn:
        locked_set(conn, svc, sid)
        return {
            "events": [
                dict(r)
                for r in conn.execute(
                    select(
                        audit.c.id,
                        audit.c.actor_id,
                        audit.c.action,
                        audit.c.target_id,
                        audit.c.request_id,
                        audit.c.details,
                        audit.c.created_at,
                    )
                    .where(audit.c.target_id == sid)
                    .order_by(audit.c.created_at.desc())
                ).mappings()
            ],
            "snapshots": [
                {
                    k: r[k]
                    for k in (
                        "id",
                        "review_set_id",
                        "scope_revision",
                        "created_at",
                        "actor_id",
                        "payload",
                    )
                }
                for r in conn.execute(
                    select(snapshots)
                    .where(snapshots.c.review_set_id == sid)
                    .order_by(snapshots.c.created_at.desc())
                ).mappings()
            ],
        }
