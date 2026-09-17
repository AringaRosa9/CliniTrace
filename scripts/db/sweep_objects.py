"""Inspect (default) or delete confirmed unreferenced S1 objects older than 24h."""

import argparse
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
from app.core.config import Settings
from app.db.s1 import artifacts, versions
from app.db.s2 import results, measurements
from app.db.session import transaction
from app.integrations.storage.s3 import Storage
from sqlalchemy import select

parser = argparse.ArgumentParser()
parser.add_argument("--apply", action="store_true")
args = parser.parse_args()
cfg = Settings(_env_file=Path(__file__).resolve().parents[2] / "backend" / ".env")
storage = Storage(cfg)
cutoff = datetime.now(UTC) - timedelta(hours=24)
candidates = deleted = 0
for page in storage.client.get_paginator("list_objects_v2").paginate(
    Bucket=cfg.s3_bucket
):
    for obj in page.get("Contents", []):
        key = obj["Key"]
        parts = key.split("/")
        if (
            len(parts) not in (5, 6)
            or parts[0] not in ("quarantine", "parsed", "extracted")
            or obj["LastModified"] >= cutoff
        ):
            continue
        tenant, project = UUID(parts[1]), UUID(parts[2])
        with transaction(cfg, tenant=tenant, project=project, worker=True) as conn:
            if parts[0] == "quarantine":
                exists = conn.execute(
                    select(versions.c.id).where(versions.c.object_key == key)
                ).first()
            elif parts[0] == "extracted":
                manifest = key.rsplit("/", 1)[0] + "/raw.json"
                exists = conn.execute(select(results.c.id).where(results.c.raw_object_key == manifest)).first()
                if not exists and key.endswith("/raw.json"):
                    exists = conn.execute(select(measurements.c.id).where(measurements.c.raw_object_key == key)).first()
            else:
                manifest = key.rsplit("/", 1)[0] + "/result.json"
                exists = conn.execute(
                    select(artifacts.c.id).where(artifacts.c.object_key == manifest)
                ).first()
        if not exists:
            candidates += 1
            if args.apply:
                storage.delete(key)
                deleted += 1
# Never print object identifiers or original content to generic logs.
print({"orphan_candidates": candidates, "deleted": deleted})
