import hashlib
import json
import logging
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.config import Settings
from app.main import create_app
from app.operations.backup import sha, validate_bundle
from app.operations.release import METRICS, blank_manifest, check_manifest, evaluate


def dossier(tmp_path):
    value = blank_manifest()
    (tmp_path / "evidence.txt").write_text("Synthetic unit test evidence; not institution approval")
    evidence = {"path": "evidence.txt", "sha256": sha(tmp_path / "evidence.txt")}
    value.update(
        release_commit="a" * 40,
        configuration_sha256="b" * 64,
        dataset_sha256="c" * 64,
        blocker_defects=0,
        uat_passed=True,
        rollback_rehearsed=True,
    )
    value["images"] = {key: "image@sha256:" + "a" * 64 for key in value["images"]}
    value["versions"] = {key: "unit-test-1" for key in value["versions"]}
    value["evidence"] = {key: evidence for key in value["evidence"]}
    value["approvals"] = {
        key: {
            "reviewer": "unit-test-only",
            "signed_at": datetime.now(UTC).isoformat(),
            "evidence": "uat",
        }
        for key in value["approvals"]
    }
    for name, (_, unit, _) in METRICS.items():
        value["metrics"][name] = {
            "threshold": 1,
            "measured": 1,
            "unit": unit,
            "denominator": 10,
            "conditions": "unit-test",
            "evidence": "load",
        }
    value["retention_days"] = {key: 30 for key in value["retention_days"]}
    value["pilot"] = {
        "project_ids": [str(uuid4())],
        "user_count": 2,
        "observe_hours": 24,
        "oncall_primary": "test",
        "oncall_backup": "test2",
        "rollback_decider": "test",
    }
    return value


def test_gate_missing_evidence_and_thresholds_fail_closed(tmp_path):
    assert "metric_unmeasured:critical_field_f1" in check_manifest(blank_manifest(), tmp_path)
    value = dossier(tmp_path)
    assert check_manifest(value, tmp_path) == []
    value["metrics"]["critical_field_f1"]["measured"] = 0.9
    value["metrics"]["dose_errors"]["measured"] = 2
    value["metrics"]["api_p95_ms"]["denominator"] = 0
    errors = check_manifest(value, tmp_path)
    assert "threshold_failed:critical_field_f1" in errors
    assert "threshold_failed:dose_errors" in errors
    assert "denominator_required:api_p95_ms" in errors
    (tmp_path / "evidence.txt").write_text("tampered")
    assert "evidence_hash_mismatch:uat" in check_manifest(value, tmp_path)


@pytest.mark.parametrize("bad", [True, float("nan"), float("inf"), -1, None, "0"])
def test_gate_rejects_non_measurements(tmp_path, bad):
    value = dossier(tmp_path)
    value["metrics"]["rto_seconds"]["measured"] = bad
    assert "metric_unmeasured:rto_seconds" in check_manifest(value, tmp_path)


def test_gate_rejects_path_escape_and_malformed_json(tmp_path):
    value = dossier(tmp_path)
    value["evidence"]["uat"] = {"path": "../outside", "sha256": "a" * 64}
    assert "evidence_missing:uat" in check_manifest(value, tmp_path)
    path = tmp_path / "manifest.json"
    path.write_text('{"metrics": null}')
    assert evaluate(path)["ready"] is False


def test_monitoring_is_private_and_redacted(caplog):
    client = TestClient(create_app(Settings(_env_file=None, metrics_token="test-token")))
    assert client.get("/internal/metrics").status_code == 404
    assert (
        client.get("/internal/metrics", headers={"Authorization": "Bearer wrong"}).status_code
        == 404
    )
    with caplog.at_level(logging.INFO, logger="clinical.telemetry"):
        client.get("/unknown/patient-secret?name=private", headers={"X-Request-ID": "sensitive"})
    result = client.get("/internal/metrics", headers={"Authorization": "Bearer test-token"})
    assert result.status_code == 200
    assert 'route="unmatched"' in result.text
    for secret in ("patient-secret", "private", "sensitive"):
        assert secret not in result.text and secret not in caplog.text


def test_maintenance_blocks_writes_but_keeps_liveness():
    client = TestClient(create_app(Settings(_env_file=None, maintenance_mode=True)))
    assert client.get("/api/v1/health").status_code == 200
    assert client.post("/api/v1/auth/synthetic", json={"access_code": "x"}).status_code == 503


def test_bundle_tamper_detected_before_restore(tmp_path):
    (tmp_path / "database.dump").write_bytes(b"fake dump")
    manifest = {"format": 1, "objects": [], "database_sha256": sha(tmp_path / "database.dump")}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    digest = sha(tmp_path / "manifest.json")
    assert validate_bundle(tmp_path, digest) == manifest
    (tmp_path / "database.dump").write_bytes(b"changed")
    with pytest.raises(ValueError):
        validate_bundle(tmp_path, digest)
    with pytest.raises(ValueError):
        validate_bundle(tmp_path, hashlib.sha256(b"wrong").hexdigest())


def test_production_rejects_plaintext_identity_endpoints():
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            app_env="production",
            identity_provider="oidc",
            oidc_issuer="http://identity.example",
            session_secret="a" * 48,
            metrics_token="b" * 48,
            s3_access_key="independent",
            s3_secret_key="independent",
        )
