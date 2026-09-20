"""Run only in an approved maintenance window, with protected backup storage."""

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
from app.core.config import Settings
from app.operations.backup import backup, restore

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("action", choices=["backup", "restore"])
parser.add_argument("directory", type=Path)
parser.add_argument("--writers-stopped", action="store_true", required=True)
parser.add_argument("--manifest-sha256", help="Digest retained separately when backup completed")
parser.add_argument("--pg-container", help="Local synthetic drill shortcut, never production")
args = parser.parse_args()
os.umask(0o077)
cfg = Settings()
if args.action == "restore" and not args.manifest_sha256:
    parser.error("Restore requires --manifest-sha256")
try:
    value = (
        backup(cfg, args.directory, args.pg_container)
        if args.action == "backup"
        else restore(cfg, args.directory, args.manifest_sha256, args.pg_container)
    )
except Exception:
    raise SystemExit(
        "Backup/restore failed. Keep ingress closed; inspect in protected DBA session."
    ) from None
print(json.dumps(value))
