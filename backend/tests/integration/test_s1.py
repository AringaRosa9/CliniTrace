"""Synthetic S1 integration against migrated PostgreSQL, Redis and private MinIO."""

import io
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4

import pymupdf
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import DBAPIError

from app.core.config import Settings
from app.db.models import projects, tenants
from app.db.s1 import (
    artifacts,
    attempts,
    audit,
    documents,
    jobs,
    memberships,
    users,
    versions,
)
from app.db.session import transaction
from app.main import create_app
from app.modules.documents.service import now
from app.modules.identity.service import signed
from app.workers.runtime import execute, recover_once

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_TESTS") != "1", reason="Requires disposable migrated infrastructure"
)


@pytest.fixture
def env():
    cfg = Settings(_env_file=None, app_env="test", synthetic_access_code="test-access-code")
    engine = create_engine(cfg.database_url)
    tenant, project, actor, other_project, other_tenant, other_actor = [uuid4() for _ in range(6)]
    subject = str(actor)
    with engine.begin() as conn:
        conn.execute(
            tenants.insert(),
            [
                {"id": tenant, "name": "Synthetic test"},
                {"id": other_tenant, "name": "Other synthetic"},
            ],
        )
        conn.execute(
            projects.insert(),
            [
                {"id": project, "tenant_id": tenant, "name": "Test"},
                {"id": other_project, "tenant_id": other_tenant, "name": "Other"},
            ],
        )
        conn.execute(
            users.insert(),
            [
                {
                    "id": actor,
                    "issuer": "synthetic",
                    "subject": subject,
                    "display_name": "Test operator",
                },
                {
                    "id": other_actor,
                    "issuer": "synthetic",
                    "subject": str(other_actor),
                    "display_name": "Other",
                },
            ],
        )
        conn.execute(
            memberships.insert(),
            [
                {
                    "id": uuid4(),
                    "tenant_id": t,
                    "project_id": p,
                    "user_id": u,
                    "project_name": "Test",
                    "roles": ["operator"],
                    "capabilities": ["import", "documents.read", "original.read", "audit.read"],
                }
                for t, p, u in [
                    (tenant, project, actor),
                    (other_tenant, other_project, other_actor),
                ]
            ],
        )
    client = TestClient(create_app(cfg))
    token = signed(
        cfg, {"sub": subject, "iss": "synthetic", "kind": "session", "csrf": "test-csrf"}, 3600
    )
    client.cookies.set("bljgh_session", token, path="/api/v1")
    client.headers.update({"Origin": cfg.public_origin, "X-CSRF-Token": "test-csrf"})
    base = f"/api/v1/projects/{project}"
    p = client.post(
        base + "/patients",
        json={"patient_key": str(uuid4()), "source": "synthetic", "source_key": str(uuid4())},
    )
    assert p.status_code == 201, p.text
    patient = p.json()["id"]
    e = client.post(
        base + "/encounters",
        json={
            "patient_id": patient,
            "kind": "outpatient",
            "source": "synthetic",
            "source_key": str(uuid4()),
        },
    )
    assert e.status_code == 201, e.text
    yield {
        "cfg": cfg,
        "engine": engine,
        "client": client,
        "base": base,
        "tenant": tenant,
        "project": project,
        "actor": actor,
        "patient": patient,
        "encounter": e.json()["id"],
        "other_project": other_project,
        "other_tenant": other_tenant,
        "other_actor": other_actor,
    }
    client.close()
    engine.dispose()


def upload(env, data=None, filename="synthetic.txt", key=None, **fields):
    return env["client"].post(
        env["base"] + "/documents",
        files={
            "file": (
                filename,
                data if data is not None else ("合成门诊记录 😀 剂量未知 " + str(uuid4())).encode(),
            )
        },
        data={
            "patient_id": env["patient"],
            "encounter_id": env["encounter"],
            "document_type": "outpatient",
            "source": "synthetic",
            "authorization_reference": "synthetic-only-v1",
            **fields,
        },
        headers={"Idempotency-Key": key or str(uuid4())},
    )


def run(env, accepted):
    execute(str(env["tenant"]), str(env["project"]), accepted["job_id"], env["cfg"])
    return env["client"].get(env["base"] + "/documents/" + accepted["document_id"])


def test_upload_parse_refresh_idempotency_and_audit(env):
    data = "合成病例😀\r\n剂量不详\n".encode()
    key = str(uuid4())
    first = upload(env, data, key=key)
    assert first.status_code == 202, first.text
    original = env["client"].get(
        env["base"] + "/documents/" + first.json()["document_id"] + "/original"
    )
    assert original.status_code == 409
    assert upload(env, data, key=key).json() == first.json()
    assert upload(env, b"changed", key=key).status_code == 409
    assert upload(env, data).json()["duplicate"] is True
    parsed = run(env, first.json())
    assert parsed.status_code == 200, parsed.text
    assert parsed.json()["processing_status"] == "parsed"
    artifact = env["client"].get(env["base"] + "/parse-artifacts/" + parsed.json()["artifact_id"])
    assert artifact.json()["text"] == data.decode()
    assert artifact.json()["pages"][0]["blocks"][0]["end"] == len(data.decode())
    original = env["client"].get(
        env["base"] + "/documents/" + first.json()["document_id"] + "/original"
    )
    assert original.content == data
    assert original.headers["cache-control"] == "no-store"
    # Duplicate broker delivery cannot create another attempt or artifact.
    run(env, first.json())
    detail = env["client"].get(first.json()["status_url"]).json()
    assert len(detail["attempts"]) == 1
    events = env["client"].get(env["base"] + "/audit-events").json()
    assert "document.original.read" in [e["action"] for e in events]
    assert "document.upload" in [e["action"] for e in events]
    # A new application instance reads persisted state.
    fresh = TestClient(create_app(env["cfg"]))
    fresh.cookies.update(env["client"].cookies)
    assert (
        fresh.get(env["base"] + "/documents").json()["items"][0]["id"]
        == first.json()["document_id"]
    )


def test_cross_project_capabilities_csrf_and_logout(env):
    accepted = upload(env).json()
    other = f"/api/v1/projects/{env['other_project']}"
    for path in (
        "/documents",
        "/documents/" + accepted["document_id"],
        "/jobs/" + accepted["job_id"],
    ):
        assert env["client"].get(other + path).status_code == 404
    assert upload(env, encounter_id=str(uuid4())).status_code == 404
    assert (
        env["client"]
        .post(accepted["status_url"] + "/cancel", headers={"X-CSRF-Token": "wrong"})
        .status_code
        == 403
    )
    assert (
        env["client"]
        .post(accepted["status_url"] + "/cancel", headers={"Origin": "https://evil.invalid"})
        .status_code
        == 403
    )
    run(env, accepted)
    with env["engine"].begin() as conn:
        conn.execute(
            memberships.update()
            .where(memberships.c.user_id == env["actor"])
            .values(roles=["admin"], capabilities=["documents.read", "audit.read"])
        )
    assert (
        env["client"]
        .get(env["base"] + "/documents/" + accepted["document_id"] + "/original")
        .status_code
        == 403
    )
    assert upload(env).status_code == 403
    token = env["client"].cookies.get("bljgh_session")
    assert env["client"].post("/api/v1/auth/logout").status_code == 204
    env["client"].cookies.set("bljgh_session", token, path="/api/v1")
    assert env["client"].get("/api/v1/me").status_code == 401


def test_cancel_retry_failure_and_stale_worker(env):
    accepted = upload(env).json()
    assert env["client"].post(accepted["status_url"] + "/cancel").json()["status"] == "cancelled"
    run(env, accepted)
    assert env["client"].get(accepted["status_url"]).json()["attempt"] == 0
    assert env["client"].post(accepted["status_url"] + "/retry").status_code == 200
    assert run(env, accepted).json()["processing_status"] == "parsed"
    assert env["client"].post(accepted["status_url"] + "/cancel").status_code == 409
    damaged = upload(env, b"%PDF-1.7\ninvalid", "bad.pdf").json()
    doc = run(env, damaged).json()
    assert doc["processing_status"] == "parse_failed"
    assert doc["error"]["retryable"] is False
    assert env["client"].post(damaged["status_url"] + "/retry").status_code == 409
    # Simulate a crashed worker and verify attempt fencing.
    pending = upload(env).json()
    with env["engine"].begin() as conn:
        conn.execute(
            jobs.update()
            .where(jobs.c.id == pending["job_id"])
            .values(
                status="running", generation=1, attempt=1, lease_until=now() - timedelta(seconds=1)
            )
        )
        conn.execute(
            attempts.insert().values(
                id=uuid4(),
                tenant_id=env["tenant"],
                project_id=env["project"],
                job_id=pending["job_id"],
                generation=1,
                status="running",
                started_at=now(),
            )
        )
    assert recover_once(env["cfg"]) >= 1
    restored = env["client"].get(pending["status_url"]).json()
    assert restored["status"] == "queued"
    assert restored["attempts"][0]["error_code"] == "WORKER_LEASE_EXPIRED"
    assert run(env, pending).json()["processing_status"] == "parsed"
    assert env["client"].get(pending["status_url"]).json()["attempt"] == 2


def test_pdf_mixed_pages_and_image(env):
    from PIL import Image

    with pymupdf.open() as pdf:
        page = pdf.new_page()
        page.insert_text((50, 80), "SYNTHETIC outpatient document text layer")
        page.set_rotation(90)
        pdf.new_page()
        data = pdf.tobytes()
    response = upload(env, data, "mixed.pdf")
    assert response.status_code == 202, response.text
    doc = run(env, response.json()).json()
    assert doc["processing_status"] == "needs_ocr"
    artifact = env["client"].get(env["base"] + "/parse-artifacts/" + doc["artifact_id"]).json()
    assert artifact["pages"][0]["needs_ocr"] is False
    assert artifact["pages"][1]["needs_ocr"] is True
    assert artifact["pages"][0]["rotation"] == 90
    assert all(0 <= c <= 1 for c in artifact["pages"][0]["blocks"][0]["bbox"])
    page = env["client"].get(env["base"] + "/parse-artifacts/" + doc["artifact_id"] + "/pages/1")
    assert page.content.startswith(b"\x89PNG")
    stream = io.BytesIO()
    Image.new("RGB", (40, 60), "white").save(stream, format="PNG")
    image = upload(env, stream.getvalue(), "image.png")
    assert run(env, image.json()).json()["processing_status"] == "needs_ocr"


def test_upload_limits_pagination_and_association_conflict(env):
    assert upload(env, b"", "empty.txt").status_code == 422
    assert upload(env, b"\x89PNG\r\n\x1a\ninvalid", "fake.txt").status_code == 415
    env["cfg"].max_upload_bytes = 256
    assert upload(env, b"x" * 257).status_code == 413
    assert upload(env, b"x" * 70000).status_code == 413
    env["cfg"].max_upload_bytes = 20 * 1024 * 1024
    first, second = upload(env).json(), upload(env).json()
    page = env["client"].get(env["base"] + "/documents", params={"limit": 1}).json()
    next_page = (
        env["client"]
        .get(env["base"] + "/documents", params={"limit": 1, "cursor": page["next_cursor"]})
        .json()
    )
    assert page["items"][0]["id"] != next_page["items"][0]["id"]
    assert next_page["next_cursor"] is None
    assert (
        env["client"].get(env["base"] + "/documents", params={"cursor": "bad"}).status_code == 400
    )
    path = env["base"] + "/documents/" + first["document_id"] + "/association"
    body = {
        "patient_id": env["patient"],
        "encounter_id": env["encounter"],
        "reason": "合成关联核对",
        "expected_revision": 1,
    }
    assert env["client"].patch(path, json=body).json()["revision"] == 2
    assert env["client"].patch(path, json=body).status_code == 409
    assert second["document_id"]


def test_rls_immutable_rows_and_connection_context(env):
    accepted = upload(env).json()
    run(env, accepted)
    cfg = env["cfg"]
    with transaction(cfg) as conn:
        assert conn.execute(select(documents.c.id)).all() == []
        assert conn.execute(select(users.c.id)).all() == []
    with transaction(cfg, tenant=env["other_tenant"], project=env["other_project"]) as conn:
        assert (
            conn.execute(
                select(documents.c.id).where(documents.c.id == accepted["document_id"])
            ).first()
            is None
        )
    for table in (versions, artifacts, audit):
        with (
            pytest.raises(DBAPIError),
            transaction(cfg, tenant=env["tenant"], project=env["project"]) as conn,
        ):
            conn.execute(table.delete())
    with transaction(cfg, tenant=env["tenant"], project=env["project"]) as conn:
        assert conn.execute(select(documents.c.id)).first()
    with transaction(cfg) as conn:
        assert (
            conn.execute(
                text("SELECT nullif(current_setting('app.project_id', true), '')")
            ).scalar_one()
            is None
        )


def test_concurrent_idempotency(env):
    data = ("same concurrent synthetic " + str(uuid4())).encode()
    key = str(uuid4())
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: upload(env, data, key=key), range(2)))
    assert [r.status_code for r in responses] == [202, 202]
    assert responses[0].json() == responses[1].json()


def test_running_cancel_wins_over_late_result(env, monkeypatch):
    accepted = upload(env).json()
    from app.integrations.storage.s3 import Storage

    original_put = Storage.put

    def cancel_at_publish(storage, key, stream, mime):
        original_put(storage, key, stream, mime)
        if key.endswith("/result.json"):
            response = env["client"].post(accepted["status_url"] + "/cancel")
            assert response.status_code == 200

    monkeypatch.setattr(Storage, "put", cancel_at_publish)
    doc = run(env, accepted).json()
    assert doc["processing_status"] == "cancelled"
    assert doc["artifact_id"] is None
    assert env["client"].get(accepted["status_url"]).json()["attempts"][0]["status"] == "cancelled"


def test_reclaimed_worker_cannot_overwrite_new_result(env, monkeypatch):
    accepted = upload(env).json()
    from app.integrations.storage.s3 import Storage

    original_read = Storage.read
    reclaim = True

    def reclaim_on_read(storage, key):
        nonlocal reclaim
        value = original_read(storage, key)
        if reclaim and key.endswith("/original"):
            reclaim = False
            with env["engine"].begin() as conn:
                conn.execute(
                    jobs.update()
                    .where(jobs.c.id == accepted["job_id"])
                    .values(lease_until=now() - timedelta(seconds=1))
                )
            recover_once(env["cfg"])
            execute(str(env["tenant"]), str(env["project"]), accepted["job_id"], env["cfg"])
        return value

    monkeypatch.setattr(Storage, "read", reclaim_on_read)
    doc = run(env, accepted).json()
    assert doc["processing_status"] == "parsed"
    history = env["client"].get(accepted["status_url"]).json()["attempts"]
    assert [item["status"] for item in history] == ["failed", "succeeded"]
    with env["engine"].connect() as conn:
        assert (
            len(
                conn.execute(
                    select(artifacts.c.id).where(artifacts.c.job_id == accepted["job_id"])
                ).all()
            )
            == 1
        )


def test_same_tenant_other_authorized_project_still_cannot_read_document(env):
    accepted = upload(env).json()
    other = uuid4()
    with env["engine"].begin() as conn:
        conn.execute(
            projects.insert().values(
                id=other, tenant_id=env["tenant"], name="Same tenant, other project"
            )
        )
        conn.execute(
            memberships.insert().values(
                id=uuid4(),
                tenant_id=env["tenant"],
                project_id=other,
                user_id=env["actor"],
                project_name="Other",
                roles=["operator"],
                capabilities=["import", "documents.read", "original.read"],
            )
        )
    base = f"/api/v1/projects/{other}"
    assert env["client"].get(base + "/documents").json()["items"] == []
    assert env["client"].get(base + "/documents/" + accepted["document_id"]).status_code == 404
    assert env["client"].get(base + "/jobs/" + accepted["job_id"]).status_code == 404
