import os
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError

from app.db.s1 import memberships
from app.db.s2 import facts, results
from app.db.session import transaction
from app.workers.runtime import execute
from tests.integration.test_s1 import env as s1_env
from tests.integration.test_s1 import run, upload

env = s1_env

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_TESTS") != "1", reason="Requires migrated infrastructure"
)
SOURCE = (
    "SYNTHETIC-S2 😀\ndiagnoses:2型糖尿病\nhistory:否认冠心病\n"
    "duration:5年\nmedication:二甲双胍|剂量不详\nmedication:阿卡波糖\n"
)


def extract_doc(env, source=SOURCE, kind="outpatient", execute_job=True):
    env["cfg"].extraction_provider = "synthetic"
    env["cfg"].allow_synthetic_mock = True
    env["cfg"].terminology_version = "synthetic-terms-1.0.0"
    accepted = upload(env, (source + str(uuid4())).encode(), document_type=kind).json()
    document = run(env, accepted).json()
    url = env["base"] + "/documents/" + document["id"] + "/extractions"
    body = {"template_version": kind + "-1.0.0", "parse_artifact_id": document["artifact_id"]}
    key = str(uuid4())
    response = env["client"].post(url, json=body, headers={"Idempotency-Key": key})
    assert response.status_code == 202, response.text
    assert (
        env["client"].post(url, json=body, headers={"Idempotency-Key": key}).json()
        == response.json()
    )
    accepted = response.json()
    if execute_job:
        execute(str(env["tenant"]), str(env["project"]), accepted["job_id"], env["cfg"])
    return document, accepted


def test_two_documents_durable_facts_relations_scope_and_isolation(env):
    doc, accepted = extract_doc(env)
    result = env["client"].get(env["base"] + "/extractions/" + accepted["run_id"])
    assert result.status_code == 200, result.text
    result = result.json()
    assert result["status"] == "succeeded", result
    assert len(result["facts"]) == 6
    assert result["duration_ms"] > 0
    assert result["usage"]["measurement"] == "synthetic_no_model_call"
    assert len(result["relations"]) == 1
    other_doc, other = extract_doc(
        env, "SYNTHETIC-S2\nobservation:FPG|<7.00|mmol/L\nobservation:HbA1c|6.80|%\n", "laboratory"
    )
    scope = (
        env["client"].get(env["base"] + "/encounters/" + env["encounter"] + "/review-set").json()
    )
    assert len(scope["members"]) == 2
    assert scope["status"] == "pending_review"
    assert scope["scope_revision"] == 3  # S3: upload expands scope before extraction publishes
    execute(str(env["tenant"]), str(env["project"]), accepted["job_id"], env["cfg"])
    assert len(env["client"].get(accepted["status_url"]).json()["attempts"]) == 1
    assert (
        env["client"]
        .get(f"/api/v1/projects/{env['other_project']}/extractions/{accepted['run_id']}")
        .status_code
        == 404
    )
    assert len(env["client"].get(env["base"] + "/documents").json()["items"]) == 2
    with transaction(env["cfg"]) as conn:
        assert not conn.execute(select(facts.c.id)).all()
    with (
        pytest.raises(DBAPIError),
        transaction(env["cfg"], tenant=env["tenant"], project=env["project"]) as conn,
    ):
        conn.execute(results.delete())
    path = env["base"] + "/documents/" + doc["id"] + "/association"
    assert (
        env["client"]
        .patch(
            path,
            json={
                "patient_id": env["patient"],
                "encounter_id": env["encounter"],
                "reason": "test",
                "expected_revision": 1,
            },
        )
        .status_code
        == 409
    )


def test_reextract_requires_explicit_activation_and_conflict(env):
    doc, first = extract_doc(env)
    path = env["base"] + "/documents/" + doc["id"]
    response = env["client"].post(
        path + "/extractions",
        json={"template_version": "outpatient-1.0.0", "parse_artifact_id": doc["artifact_id"]},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert response.status_code == 202, response.text
    second = response.json()
    execute(str(env["tenant"]), str(env["project"]), second["job_id"], env["cfg"])
    scope_url = env["base"] + "/encounters/" + env["encounter"] + "/review-set"
    scope = env["client"].get(scope_url).json()
    assert scope["members"][0]["extraction_run_id"] == first["run_id"]
    body = {"run_id": second["run_id"], "expected_scope_revision": 1, "reason": "核对新抽取版本"}
    assert env["client"].post(path + "/active-run", json=body).status_code == 200
    assert env["client"].post(path + "/active-run", json=body).status_code == 409
    extract_doc(env, "SYNTHETIC-S2\ndiagnoses:冠心病\n")
    assert "CONFLICT" in [i["rule"] for i in env["client"].get(scope_url).json()["issues"]]


def test_cancellation_invalid_output_and_permissions(env):
    doc, accepted = extract_doc(env, execute_job=False)
    assert env["client"].post(accepted["status_url"] + "/cancel").status_code == 200
    execute(str(env["tenant"]), str(env["project"]), accepted["job_id"], env["cfg"])
    result = env["client"].get(env["base"] + "/extractions/" + accepted["run_id"]).json()
    assert result["status"] == "cancelled" and not result["facts"]
    assert env["client"].post(accepted["status_url"] + "/retry").status_code == 200
    execute(str(env["tenant"]), str(env["project"]), accepted["job_id"], env["cfg"])
    _, failed = extract_doc(env, "UNAPPROVED fixture")
    result = env["client"].get(env["base"] + "/extractions/" + failed["run_id"]).json()
    assert result["status"] == "failed" and result["facts"] == []
    assert result["error"]["code"] == "SYNTHETIC_DOCUMENT_REQUIRED"
    assert result["error"]["details"]["stage"] == "extracting"
    with env["engine"].begin() as conn:
        conn.execute(
            memberships.update()
            .where(memberships.c.user_id == env["actor"])
            .values(capabilities=["documents.read"])
        )
    assert env["client"].get(env["base"] + "/extractions/" + accepted["run_id"]).status_code == 403


def test_cancelled_late_extraction_cannot_publish(env, monkeypatch):
    from app.integrations.storage.s3 import Storage

    _, accepted = extract_doc(env, execute_job=False)
    original_put = Storage.put

    def cancel(storage, key, stream, mime):
        original_put(storage, key, stream, mime)
        if key.startswith("extracted/") and key.endswith("/result.json"):
            assert env["client"].post(accepted["status_url"] + "/cancel").status_code == 200

    monkeypatch.setattr(Storage, "put", cancel)
    execute(str(env["tenant"]), str(env["project"]), accepted["job_id"], env["cfg"])
    result = env["client"].get(env["base"] + "/extractions/" + accepted["run_id"]).json()
    assert result["status"] == "cancelled"
    assert result["facts"] == []


def test_real_http_gateway_contract_and_failed_evidence_usage(env):
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from app.db.s2 import measurements
    from app.integrations.llm.synthetic import extract_synthetic
    from app.integrations.storage.s3 import Storage

    _, seed = extract_doc(env)
    doc = env["client"].get(env["base"] + "/documents").json()["items"][0]

    class Gateway(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            value = extract_synthetic(
                {**body["data"], "configuration": {"template_version": "outpatient-1.0.0"}}
            )
            value["diagnoses"][0]["evidence"][0]["quote"] = "invented evidence"
            response = {
                "output": value,
                "model": "fixture-v1",
                "finish_reason": "stop",
                "request_id": "local-http",
                "usage": {
                    "input_tokens": 33,
                    "output_tokens": 44,
                    "cost_amount": "0",
                    "cost_currency": "CNY",
                },
            }
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(response).encode())

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Gateway)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        cfg = env["cfg"]
        cfg.extraction_provider = "gateway"
        cfg.extraction_model = "fixture-v1"
        cfg.extraction_authorization = "synthetic-local-http-test"
        cfg.extraction_gateway_url = f"http://127.0.0.1:{server.server_port}/extract"
        response = env["client"].post(
            env["base"] + "/documents/" + doc["id"] + "/extractions",
            json={"template_version": "outpatient-1.0.0", "parse_artifact_id": doc["artifact_id"]},
            headers={"Idempotency-Key": str(uuid4())},
        )
        assert response.status_code == 202, response.text
        accepted = response.json()
        execute(str(env["tenant"]), str(env["project"]), accepted["job_id"], cfg)
        value = env["client"].get(env["base"] + "/extractions/" + accepted["run_id"]).json()
        assert value["status"] == "failed", value
        assert value["error"]["code"] == "EVIDENCE_QUOTE_MISMATCH"
        assert value["facts"] == []
        assert value["usage"]["input_tokens"] == 33
        with env["engine"].connect() as conn:
            key = conn.execute(
                select(measurements.c.raw_object_key).where(
                    measurements.c.run_id == accepted["run_id"]
                )
            ).scalar_one()
        assert json.loads(Storage(cfg).read(key))["responses"][0]["request_id"] == "local-http"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_s1_scan_can_start_ocr_without_duplicate_document_rows(env):
    import io
    import shutil

    from PIL import Image

    if not shutil.which("tesseract"):
        pytest.skip("Tesseract unavailable")
    image = io.BytesIO()
    Image.new("RGB", (120, 80), "white").save(image, format="PNG")
    accepted = upload(env, image.getvalue(), "scan.png").json()
    doc = run(env, accepted).json()
    assert doc["processing_status"] == "needs_ocr"
    env["cfg"].ocr_provider = "tesseract"
    env["cfg"].ocr_language = "eng"
    url = env["base"] + "/documents/" + doc["id"] + "/ocr"
    headers = {"Idempotency-Key": str(uuid4())}
    response = env["client"].post(url, headers=headers)
    assert response.status_code == 202, response.text
    assert env["client"].post(url, headers=headers).json() == response.json()
    execute(str(env["tenant"]), str(env["project"]), response.json()["job_id"], env["cfg"])
    listing = env["client"].get(env["base"] + "/documents").json()["items"]
    assert len(listing) == 1
    assert listing[0]["artifact_id"] != doc["artifact_id"]
    assert listing[0]["job_id"] == response.json()["job_id"]
    assert listing[0]["processing_status"] == "needs_ocr"


def test_reclaimed_extraction_cannot_replace_new_facts(env, monkeypatch):
    from datetime import timedelta

    from app.db.s1 import jobs
    from app.db.s2 import measurements
    from app.integrations.storage.s3 import Storage
    from app.modules.documents.service import now
    from app.workers.runtime import recover_once

    _, accepted = extract_doc(env, execute_job=False)
    original_put = Storage.put
    reclaim = True

    def lose_lease(storage, key, stream, mime):
        nonlocal reclaim
        original_put(storage, key, stream, mime)
        if reclaim and key.startswith("extracted/") and key.endswith("/raw.json"):
            reclaim = False
            with env["engine"].begin() as conn:
                conn.execute(
                    jobs.update()
                    .where(jobs.c.id == accepted["job_id"])
                    .values(lease_until=now() - timedelta(seconds=1))
                )
            recover_once(env["cfg"])
            execute(str(env["tenant"]), str(env["project"]), accepted["job_id"], env["cfg"])

    monkeypatch.setattr(Storage, "put", lose_lease)
    execute(str(env["tenant"]), str(env["project"]), accepted["job_id"], env["cfg"])
    value = env["client"].get(env["base"] + "/extractions/" + accepted["run_id"]).json()
    assert value["status"] == "succeeded"
    assert len(value["facts"]) == 6
    assert {m["outcome"] for m in value["attempt_measurements"]} == {"succeeded", "superseded"}
    with env["engine"].connect() as conn:
        assert (
            len(
                conn.execute(
                    select(results.c.id).where(results.c.run_id == accepted["run_id"])
                ).all()
            )
            == 1
        )
        keys = (
            conn.execute(
                select(measurements.c.raw_object_key).where(
                    measurements.c.run_id == accepted["run_id"]
                )
            )
            .scalars()
            .all()
        )
    assert all(Storage(env["cfg"]).read(key) for key in keys)
