"""Replay an authorized evaluation snapshot offline; performs no API/model/network calls."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
from app.modules.extractions.pipeline import digest
from app.modules.quality.metrics import PROTOCOL, evaluate


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "snapshot",
        type=Path,
        help="JSON response from /quality/evaluations/{id}/errors",
    )
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    payload = json.loads(args.snapshot.read_text())["payload"]
    if (
        payload["protocol"] != PROTOCOL
        or digest(payload["records"]) != payload["records_digest"]
    ):
        raise SystemExit("Protocol or frozen input digest mismatch")
    report = evaluate(payload["records"])
    if report != payload["report"]:
        raise SystemExit("Replayed report differs from the saved report")
    args.output.write_text(
        json.dumps(
            {
                "dataset_digest": payload["dataset_digest"],
                "records_digest": payload["records_digest"],
                "report": report,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    print("Frozen inputs verified; replay matches the saved report.")


if __name__ == "__main__":
    main()
