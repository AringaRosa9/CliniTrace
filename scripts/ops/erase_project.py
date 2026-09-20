"""Offline project erasure. Default is a count-only plan; no clinical content is printed."""

import argparse
import json
import sys
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
from app.core.config import Settings
from app.operations.retention import erase_project

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("plan", type=Path, help="Protected institution-approved JSON plan")
parser.add_argument("--apply", action="store_true")
parser.add_argument("--writers-stopped", action="store_true")
args = parser.parse_args()
plan = json.loads(args.plan.read_text())
if args.apply and (not args.writers_stopped or plan.get("approved") is not True):
    parser.error("Apply requires an approved plan and stopped writers")
result = erase_project(
    Settings(),
    UUID(plan["tenant_id"]),
    UUID(plan["project_id"]),
    args.plan.parent / "deletion-receipt.json",
    plan["authorization"],
    apply=args.apply,
    backup_expiry=plan.get("backup_expiry", ""),
    legal_hold=plan.get("legal_hold", True),
)
print(json.dumps(result, ensure_ascii=False))
