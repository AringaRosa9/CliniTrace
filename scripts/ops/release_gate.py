"""Validate a pilot dossier and fail closed when approval or measurements are missing."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
from app.operations.release import evaluate

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("manifest", type=Path)
args = parser.parse_args()
result = evaluate(args.manifest)
print(json.dumps(result, ensure_ascii=False, indent=2))
raise SystemExit(0 if result["ready"] else 1)
