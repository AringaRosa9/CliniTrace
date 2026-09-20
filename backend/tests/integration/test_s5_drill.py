"""Destructive operations run ONLY in freshly-created synthetic databases/buckets."""

import io
import json
import os
import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url

from app.core.config import Settings
from app.db.s1 import memberships
from app.db.s3 import snapshots
from app.db.session import engine_for
from app.integrations.storage.s3 import Storage
from app.main import create_app
from app.operations.backup import backup, restore
from app.operations.retention import erase_project
from app.operations.telemetry import dependencies, heartbeat
from app.workers.runtime import execute
from tests.integration.test_s1 import env as base_env
from tests.integration.test_s1 import run, upload
from tests.integration.test_s3 import confirm, export, get, post, setup

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_OPS_DRILL") != "1", reason="Requires explicit isolated recovery drill"
)
ROOT = Path(__file__).resolve().parents[3]


def test_isolated_backup_restore_delete_and_application_rollback(tmp_path, monkeypatch):
    cfg = Settings(_env_file=None, app_env="test")
    if make_url(cfg.database_url).host not in ("127.0.0.1", "localhost"):
        pytest.fail("Drill must use local synthetic infrastructure")
    admin = create_engine(cfg.database_url, isolation_level="AUTOCOMMIT")
    names = ["s5_drill_" + uuid4().hex for _ in range(2)]
    buckets = ["s5-drill-" + uuid4().hex for _ in range(2)]
    configs = [
        cfg.model_copy(
            update={
                "database_url": make_url(cfg.database_url)
                .set(database=name)
                .render_as_string(hide_password=False),
                "s3_bucket": bucket,
            }
        )
        for name, bucket in zip(names, buckets, strict=True)
    ]
    storage = Storage(cfg)
    started = time.monotonic()
    try:
        with admin.connect() as conn:
            for name in names:
                conn.execute(text(f'CREATE DATABASE "{name}" TEMPLATE template0'))
        for bucket in buckets:
            storage.client.create_bucket(Bucket=bucket)
        monkeypatch.setenv("DATABASE_URL", configs[0].database_url)
        monkeypatch.setenv("S3_BUCKET", buckets[0])
        subprocess.run(
            [
                str(ROOT / "backend/.venv/bin/alembic"),
                "-c",
                "backend/alembic.ini",
                "upgrade",
                "head",
            ],
            cwd=ROOT,
            check=True,
        )
        engine = create_engine(configs[0].database_url)
        with engine.begin() as conn:
            conn.exec_driver_sql((ROOT / "infra/deploy/roles.sql").read_text())
        generator = base_env.__wrapped__()
        env = next(generator)
        sid, _ = setup(env)
        post(env, "/reviews", confirm(env, sid), 201)
        w = get(env, sid)
        accepted = export(env, w)
        execute(str(env["tenant"]), str(env["project"]), accepted["job_id"], env["cfg"])
        # Include a page image, so restore verifies DB -> manifest -> rendered page links.
        picture = io.BytesIO()
        Image.new("RGB", (30, 30), "white").save(picture, format="PNG")
        parsed = upload(env, picture.getvalue(), "synthetic.png").json()
        run(env, parsed)
        with engine.connect() as conn:
            original_snapshot = conn.execute(select(snapshots.c.payload)).scalar_one()
        container = os.getenv("OPS_PG_CONTAINER", "bljgh-dev-postgres-1") or None
        bundle = tmp_path / "bundle"
        saved = backup(configs[0], bundle, container)
        restored_at = time.monotonic()
        restored = restore(configs[1], bundle, saved["manifest_sha256"], container)
        rto = time.monotonic() - restored_at
        assert restored["consistent"]
        with pytest.raises(ValueError, match="empty"):
            restore(configs[1], bundle, saved["manifest_sha256"], container)
        target = create_engine(configs[1].database_url)
        with target.connect() as conn:
            assert conn.execute(select(snapshots.c.payload)).scalar_one() == original_snapshot
        # Same application reads restored reviews; maintenance deployment is reversible.
        client = TestClient(create_app(configs[1]))
        client.cookies.update(env["client"].cookies)
        assert client.get(env["base"] + f"/review-sets/{sid}").status_code == 200
        maintenance = TestClient(
            create_app(configs[1].model_copy(update={"maintenance_mode": True}))
        )
        assert (
            maintenance.post("/api/v1/auth/synthetic", json={"access_code": "x"}).status_code == 503
        )
        assert client.get("/api/v1/health").status_code == 200
        heartbeat(configs[1], "worker", "synthetic-drill")
        heartbeat(configs[1], "dispatcher", "synthetic-drill")
        measured = "\n".join(dependencies(configs[1]))
        assert "bljgh_jobs" in measured and str(env["project"]) not in measured
        storage.client.put_bucket_versioning(
            Bucket=buckets[1], VersioningConfiguration={"Status": "Enabled"}
        )
        shadow_key = f"quarantine/{env['tenant']}/{env['project']}/drill/shadow"
        storage.client.put_object(Bucket=buckets[1], Key=shadow_key, Body=b"old synthetic")
        storage.client.put_object(Bucket=buckets[1], Key=shadow_key, Body=b"new synthetic")
        storage.client.delete_object(Bucket=buckets[1], Key=shadow_key)
        receipt = tmp_path / "erase.json"
        plan = erase_project(configs[1], env["tenant"], env["project"], receipt, "synthetic-drill")
        assert plan["counts"]["review_snapshots"] == 1 and not receipt.exists()
        with pytest.raises(ValueError):
            erase_project(
                configs[1],
                env["tenant"],
                env["project"],
                receipt,
                "synthetic-drill",
                apply=True,
                backup_expiry=(datetime.now(UTC) + timedelta(days=1)).isoformat(),
            )
        erased = erase_project(
            configs[1],
            env["tenant"],
            env["project"],
            receipt,
            "synthetic-drill",
            apply=True,
            legal_hold=False,
            backup_expiry=(datetime.now(UTC) + timedelta(days=1)).isoformat(),
        )
        assert erased["status"] == "complete" and erased["objects_deleted"] > 0
        remnants = storage.client.list_object_versions(Bucket=buckets[1])
        assert not remnants.get("Versions") and not remnants.get("DeleteMarkers")
        with target.connect() as conn:
            assert conn.execute(select(snapshots)).first() is None
            assert (
                conn.execute(
                    select(memberships).where(memberships.c.project_id == env["other_project"])
                ).first()
                is not None
            )
        assert client.get(env["base"] + f"/review-sets/{sid}").status_code == 404
        report = {
            "data": "synthetic-only",
            "backup": saved,
            "restore": restored,
            "restore_seconds": round(rto, 3),
            "total_seconds": round(time.monotonic() - started, 3),
            "deletion": {"status": erased["status"], "objects": erased["objects_deleted"]},
            "snapshot_equal": True,
            "other_project_preserved": True,
        }
        output = ROOT / "test-results/s5/recovery.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2))
        client.close()
        maintenance.close()
        generator.close()
        target.dispose()
        engine.dispose()
    finally:
        # Only the random resources allocated above are ever cleaned up.
        engine_for.cache_clear()
        with admin.connect() as conn:
            for name in names:
                conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        for bucket in buckets:
            for page in storage.client.get_paginator("list_object_versions").paginate(
                Bucket=bucket
            ):
                for item in page.get("Versions", []) + page.get("DeleteMarkers", []):
                    storage.client.delete_object(
                        Bucket=bucket, Key=item["Key"], VersionId=item["VersionId"]
                    )
            storage.client.delete_bucket(Bucket=bucket)
        admin.dispose()
