"""Bind a rollback to reviewed images, an unchanged schema and a compatibility record."""

import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
from sqlalchemy import create_engine, text

from app.core.config import Settings

manifest, env_path, record_path = map(Path, sys.argv[1:])
release = json.loads(manifest.read_text())
record = json.loads(record_path.read_text())
values = dict(
    line.strip().split("=", 1)
    for line in env_path.read_text().splitlines()
    if line.strip() and not line.lstrip().startswith("#")
)
if record.get("approved") is not True or not record.get("reviewer"):
    raise SystemExit("Reviewed compatibility evidence required")
if record.get("manifest_sha256") != hashlib.sha256(manifest.read_bytes()).hexdigest():
    raise SystemExit("Rollback manifest changed")
if record.get("compose_env_sha256") != hashlib.sha256(env_path.read_bytes()).hexdigest():
    raise SystemExit("Rollback environment changed")
if record.get("current_migration") != release["migration"] or record.get("compatible") is not True:
    raise SystemExit("Schema compatibility not established; do not downgrade a live database")
with create_engine(Settings().database_url).connect() as connection:
    current = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    if current != record["current_migration"]:
        raise SystemExit("Live database migration differs from the reviewed rollback record")
for key in ("backend", "frontend", "gateway"):
    if values.get(key.upper() + "_IMAGE") != release["images"][key]:
        raise SystemExit("Rollback image digest mismatch")
print("Rollback evidence and immutable image references match.")
