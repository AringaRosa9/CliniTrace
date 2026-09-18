"""S3 runs against real PostgreSQL RLS, private object storage and the worker."""

import csv
import io
import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError

from app.db.s1 import memberships
from app.db.s3 import snapshots
from app.db.session import transaction
from app.workers.runtime import execute
from tests.integration.test_s1 import env as s1_env
from tests.integration.test_s1 import upload
from tests.integration.test_s2 import extract_doc

env = s1_env
pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_TESTS") != "1", reason="Requires migrated infrastructure"
)


def setup(env):
    with env["engine"].begin() as conn:
        conn.execute(
            memberships.update()
            .where(memberships.c.user_id == env["actor"])
            .values(
                capabilities=[
                    "import",
                    "documents.read",
                    "original.read",
                    "audit.read",
                    "review",
                    "export.reviewed",
                    "export.draft",
                ]
            )
        )
    extract_doc(env)
    extract_doc(env, "SYNTHETIC-S2\nobservation:FPG|<7.00|mmol/L\n", "laboratory")
    sid = env["client"].get(env["base"] + "/review-sets").json()["items"][0]["review_set_id"]
    return sid, get(env, sid)


def get(env, sid):
    response = env["client"].get(env["base"] + "/review-sets/" + sid)
    assert response.status_code == 200, response.text
    return response.json()


def post(env, path, body, status=200):
    response = env["client"].post(env["base"] + path, json=body)
    assert response.status_code == status, response.text
    return response.json()


def confirm(env, sid):
    w = get(env, sid)
    for i in w["issues"]:
        if i["status"] == "open" and i["can_accept_unknown"]:
            post(
                env,
                f"/issues/{i['id']}/dispositions",
                {
                    "review_set_id": sid,
                    "expected_scope_revision": w["scope_revision"],
                    "status": "accepted_unknown",
                    "reason": "原文没有提供信息，保持未知",
                },
            )
    for f in w["facts"]:
        post(
            env,
            f"/review-sets/{sid}/fact-checks",
            {"expected_scope_revision": w["scope_revision"], "fact_revision_id": f["revision_id"]},
        )
    return {
        "review_set_id": sid,
        "expected_scope_revision": w["scope_revision"],
        "final_confirmation": True,
    }


def export(env, w, fmt="json", reviewed=True):
    body = {
        "selections": [{"review_set_id": w["id"], "expected_scope_revision": w["scope_revision"]}],
        "format": fmt,
        "purpose": '合成研究,"核对"\n用途',
        "reviewed_only": reviewed,
    }
    headers = {"Idempotency-Key": str(uuid4())}
    response = env["client"].post(env["base"] + "/exports", json=body, headers=headers)
    assert response.status_code == 202, response.text
    assert (
        env["client"].post(env["base"] + "/exports", json=body, headers=headers).json()
        == response.json()
    )
    return response.json()


def edit(env, w, fact, reason="核对原文"):
    return env["client"].patch(
        env["base"] + "/facts/" + fact["fact_id"],
        json={
            "expected_revision": fact["revision"],
            "expected_scope_revision": w["scope_revision"],
            "reason": reason,
            "value": fact["value"],
            "evidence": fact["evidence"],
            "event_time": fact["event_time"],
        },
    )


def test_review_gates_snapshot_freeze_exports_invalidation(env):
    sid, w = setup(env)
    post(
        env,
        "/reviews",
        {
            "review_set_id": sid,
            "expected_scope_revision": w["scope_revision"],
            "final_confirmation": True,
        },
        422,
    )
    body = confirm(env, sid)
    post(env, "/reviews", {**body, "final_confirmation": False}, 422)
    snapshot = post(env, "/reviews", body, 201)
    assert post(env, "/reviews", body, 201)["id"] == snapshot["id"]
    w = get(env, sid)
    assert w["status"] == "approved" and len(w["documents"]) == 2
    accepted = export(env, w)
    csv_export = export(env, w, "csv")
    assert edit(env, w, w["facts"][0]).status_code == 200
    assert edit(env, w, w["facts"][0]).status_code == 409
    current = get(env, sid)
    assert current["status"] == "pending_review" and current["checked_revision_ids"] == []
    assert (
        len(env["client"].get(env["base"] + f"/review-sets/{sid}/events").json()["snapshots"]) == 1
    )
    for accepted_export in (accepted, csv_export):
        execute(str(env["tenant"]), str(env["project"]), accepted_export["job_id"], env["cfg"])
        execute(str(env["tenant"]), str(env["project"]), accepted_export["job_id"], env["cfg"])
        view = env["client"].get(accepted_export["status_url"]).json()
        assert view["status"] == "succeeded", view
        assert view["sha256"] and view["size"] > 0
    value = env["client"].get(accepted["status_url"] + "/download").json()
    record = value["records"][0]
    assert record["scope_revision"] == w["scope_revision"]
    assert record["facts"][0]["revision_id"] == w["facts"][0]["revision_id"]
    assert record["facts"][0]["review_status"] == "reviewed"
    csv_value = (
        env["client"].get(csv_export["status_url"] + "/download").content.decode("utf-8-sig")
    )
    rows = list(csv.DictReader(io.StringIO(csv_value)))
    assert len(rows) == len(w["facts"])
    assert rows[0]["purpose"] == '合成研究,"核对"\n用途'
    assert rows[0]["evidence_json"] and rows[0]["configurations_json"]
    with (
        pytest.raises(DBAPIError),
        transaction(env["cfg"], tenant=env["tenant"], project=env["project"]) as conn,
    ):
        conn.execute(snapshots.delete())
    # New upload immediately invalidates approval even before parse/extraction.
    post(env, "/reviews", confirm(env, sid), 201)
    assert upload(env).status_code == 202
    w = get(env, sid)
    assert w["status"] == "pending_review"
    body = confirm(env, sid)
    post(env, "/reviews", body, 422)


def test_concurrent_edit_draft_permissions_and_expiry(env):
    sid, w = setup(env)
    with ThreadPoolExecutor(2) as pool:
        codes = list(pool.map(lambda _: edit(env, w, w["facts"][0]).status_code, range(2)))
    assert sorted(codes) == [200, 409]
    w = get(env, sid)
    body = {
        "selections": [{"review_set_id": sid, "expected_scope_revision": w["scope_revision"]}],
        "format": "json",
        "purpose": "测试",
    }
    post(env, "/reviews", confirm(env, sid), 201)
    post(
        env,
        f"/review-sets/{sid}/return",
        {
            "expected_scope_revision": w["scope_revision"],
            "reason": "资料不足",
            "required_material": "检验原件",
        },
    )
    body["selections"][0]["expected_scope_revision"] = get(env, sid)["scope_revision"]
    response = env["client"].post(
        env["base"] + "/exports", json=body, headers={"Idempotency-Key": str(uuid4())}
    )
    assert response.status_code == 422
    accepted = export(env, get(env, sid), reviewed=False)
    execute(str(env["tenant"]), str(env["project"]), accepted["job_id"], env["cfg"])
    assert all(
        f["review_status"] == "draft"
        for f in env["client"]
        .get(accepted["status_url"] + "/download")
        .json()["records"][0]["facts"]
    )
    with env["engine"].begin() as conn:
        conn.execute(
            memberships.update()
            .where(memberships.c.user_id == env["actor"])
            .values(capabilities=["documents.read", "export.reviewed"])
        )
    assert env["client"].get(accepted["status_url"] + "/download").status_code == 403
    assert env["client"].get(env["base"] + f"/review-sets/{sid}").status_code == 403
    assert (
        env["client"]
        .get(f"/api/v1/projects/{env['other_project']}/exports/{accepted['export_id']}")
        .status_code
        == 404
    )
    body["reviewed_only"] = False
    assert (
        env["client"]
        .post(env["base"] + "/exports", json=body, headers={"Idempotency-Key": str(uuid4())})
        .status_code
        == 403
    )


def test_invalid_evidence_supplement_exclude_and_filters(env):
    sid, w = setup(env)
    fact = w["facts"][0]
    invalid = {**fact, "evidence": [{**fact["evidence"][0], "quote": "伪造"}]}
    assert edit(env, w, invalid).status_code == 422
    supplemental = {
        "review_set_id": sid,
        "run_id": w["members"][0]["extraction_run_id"],
        "expected_scope_revision": w["scope_revision"],
        "field_path": fact["field_path"],
        "value": fact["value"],
        "evidence": fact["evidence"],
        "reason": "补录并留痕",
    }
    # Match the run to its evidence (membership order is not assumed).
    supplemental["run_id"] = next(
        m["extraction_run_id"]
        for m in w["members"]
        if m["document_version_id"] == fact["evidence"][0]["document_version_id"]
    )
    added = post(env, "/facts", supplemental, 201)
    w = get(env, sid)
    post(
        env,
        f"/facts/{added['fact_id']}/exclude",
        {
            "expected_revision": 1,
            "expected_scope_revision": w["scope_revision"],
            "reason": "重复补录",
        },
    )
    w = get(env, sid)
    assert next(f for f in w["facts"] if f["fact_id"] == added["fact_id"])["excluded"]
    selected = (
        env["client"]
        .get(
            env["base"] + "/review-sets",
            params={
                "patient": w["patient_key"],
                "encounter_id": w["encounter_id"],
                "document": "synthetic",
                "status": "pending_review",
            },
        )
        .json()
    )
    assert selected["total"] == 1 and selected["document_count"] == 2
    ds = post(env, "/datasets", {"name": "合成研究", "filters": {"patient": w["patient_key"]}}, 201)
    assert env["client"].get(env["base"] + f"/datasets/{ds['id']}/records").json()[
        "fact_count"
    ] == len(w["facts"])


def test_review_races_run_switch_and_nonwaivable_errors(env):
    from uuid import UUID

    from fastapi import HTTPException

    from app.modules.documents.service import Service
    from app.modules.reviews.schema import Approval, Correction
    from app.modules.reviews.service import approve as approve_service
    from app.modules.reviews.service import edit as edit_service

    sid, w = setup(env)
    body = confirm(env, sid)
    f = w["facts"][0]
    svc = Service(env["cfg"], env["actor"], env["project"], uuid4())

    def finish(which):
        try:
            if which:
                approve_service(svc, Approval.model_validate(body))
            else:
                edit_service(
                    svc,
                    UUID(f["fact_id"]),
                    Correction(
                        expected_scope_revision=w["scope_revision"],
                        expected_revision=f["revision"],
                        reason="并发核对",
                        value=f["value"],
                        evidence=f["evidence"],
                        event_time=f["event_time"],
                    ),
                )
            return 200
        except HTTPException as exc:
            return exc.status_code

    with ThreadPoolExecutor(2) as pool:
        outcomes = list(pool.map(finish, [True, False]))
    assert outcomes in ([200, 200], [409, 200])
    assert get(env, sid)["status"] == "pending_review"
    post(env, "/reviews", confirm(env, sid), 201)
    w = get(env, sid)
    doc = w["documents"][0]
    template = w["configurations"][doc["run_id"]]["template_version"]
    response = env["client"].post(
        env["base"] + f"/documents/{doc['id']}/extractions",
        headers={"Idempotency-Key": str(uuid4())},
        json={"template_version": template, "parse_artifact_id": doc["parse_artifact_id"]},
    )
    assert response.status_code == 202
    accepted = response.json()
    execute(str(env["tenant"]), str(env["project"]), accepted["job_id"], env["cfg"])
    assert get(env, sid)["status"] == "approved"  # candidate alone cannot invalidate
    post(
        env,
        f"/documents/{doc['id']}/active-run",
        {
            "run_id": accepted["run_id"],
            "expected_scope_revision": w["scope_revision"],
            "reason": "采用新运行",
        },
    )
    current = get(env, sid)
    assert current["status"] == "pending_review" and not current["checked_revision_ids"]
    assert current["scope_revision"] > w["scope_revision"]
    post(
        env,
        f"/review-sets/{sid}/fact-checks",
        {
            "expected_scope_revision": current["scope_revision"],
            "fact_revision_id": f["revision_id"],
        },
        409,
    )
    # A semantic mismatch cannot be accepted as unknown or marked resolved.
    negated = next(f for f in current["facts"] if f["value"]["assertion"] == "negated")
    changed = {**negated, "value": {**negated["value"], "assertion": "affirmed"}}
    assert edit(env, current, changed).status_code == 200
    current = get(env, sid)
    problem = next(i for i in current["issues"] if i["rule"] == "NEGATION_MISMATCH")
    for status in ("accepted_unknown", "resolved"):
        post(
            env,
            f"/issues/{problem['id']}/dispositions",
            {
                "review_set_id": sid,
                "expected_scope_revision": current["scope_revision"],
                "status": status,
                "reason": "不可绕过",
            },
            422,
        )


def test_export_cancel_recovery_expiry_and_audit(env, monkeypatch):
    from datetime import timedelta

    from app.db.s1 import audit, documents, jobs
    from app.integrations.storage.s3 import Storage
    from app.modules.documents.service import now
    from app.modules.exports import service as exports_service
    from app.workers.runtime import recover_once

    sid, w = setup(env)
    accepted = export(env, w, reviewed=False)
    before = env["client"].get(env["base"] + "/documents").json()
    post(env, f"/jobs/{accepted['job_id']}/cancel", None)
    execute(str(env["tenant"]), str(env["project"]), accepted["job_id"], env["cfg"])
    assert env["client"].get(accepted["status_url"]).json()["status"] == "cancelled"
    assert env["client"].get(env["base"] + "/documents").json() == before
    post(env, f"/jobs/{accepted['job_id']}/retry", None)
    original_put = Storage.put
    reclaim = True

    def expired(storage, key, stream, mime):
        nonlocal reclaim
        original_put(storage, key, stream, mime)
        if key.startswith("exports/") and reclaim:
            reclaim = False
            with env["engine"].begin() as conn:
                conn.execute(
                    jobs.update()
                    .where(jobs.c.id == accepted["job_id"])
                    .values(lease_until=now() - timedelta(seconds=1))
                )
            recover_once(env["cfg"])
            execute(str(env["tenant"]), str(env["project"]), accepted["job_id"], env["cfg"])

    monkeypatch.setattr(Storage, "put", expired)
    execute(str(env["tenant"]), str(env["project"]), accepted["job_id"], env["cfg"])
    assert env["client"].get(accepted["status_url"]).json()["status"] == "succeeded"
    assert env["client"].get(accepted["status_url"] + "/download").status_code == 200
    with env["engine"].connect() as conn:
        actions = list(
            conn.execute(
                select(audit.c.action).where(audit.c.target_id == accepted["export_id"])
            ).scalars()
        )
        assert actions == ["export.create", "export.download"]
        assert all(
            s == "pending_review"
            for s in conn.execute(
                select(documents.c.processing_status).where(
                    documents.c.project_id == env["project"]
                )
            ).scalars()
        )
    with env["engine"].begin() as conn:
        conn.execute(
            memberships.update()
            .where(memberships.c.user_id == env["actor"])
            .values(capabilities=["export.reviewed", "export.draft"])
        )
    assert env["client"].get(env["base"] + f"/jobs/{accepted['job_id']}").status_code == 200
    another = export(env, w, reviewed=False)
    post(env, f"/jobs/{another['job_id']}/cancel", None)
    monkeypatch.setattr(exports_service, "now", lambda: now() + timedelta(days=2))
    assert env["client"].get(accepted["status_url"] + "/download").status_code == 410
