"""Bounded authenticated read load; cookies stay in environment, never output."""

import argparse
import concurrent.futures
import json
import math
import os
import time
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

import httpx

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--origin", default="http://127.0.0.1:18000")
parser.add_argument("--project", required=True)
parser.add_argument("--requests", type=int, default=200)
parser.add_argument("--concurrency", type=int, default=8)
parser.add_argument("--p95-ms", type=float, required=True)
parser.add_argument("--max-failure-rate", type=float, default=0)
parser.add_argument("--output", type=Path, default=Path("test-results/s5/load.json"))
args = parser.parse_args()
if not 1 <= args.requests <= 10000 or not 1 <= args.concurrency <= 64:
    parser.error("Requests/concurrency outside bounded range")
if (
    not math.isfinite(args.p95_ms)
    or args.p95_ms <= 0
    or not 0 <= args.max_failure_rate <= 1
):
    parser.error("Invalid thresholds")
if urlsplit(args.origin).scheme not in ("http", "https") or not os.getenv(
    "LOAD_SESSION"
):
    parser.error("Origin and LOAD_SESSION are required")

project = str(UUID(args.project))
paths = [
    f"/api/v1/projects/{project}/documents",
    f"/api/v1/projects/{project}/review-sets",
    "/api/v1/me",
]
with httpx.Client(
    base_url=args.origin,
    cookies={"bljgh_session": os.environ["LOAD_SESSION"]},
    timeout=30,
    follow_redirects=False,
) as client:

    def request(index):
        start = time.perf_counter()
        try:
            response = client.get(paths[index % len(paths)])
            success = response.status_code == 200
        except httpx.HTTPError:
            success = False
        return (time.perf_counter() - start) * 1000, success

    start = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=args.concurrency
    ) as executor:
        results = list(executor.map(request, range(args.requests)))
    elapsed = time.perf_counter() - start
values = sorted(ms for ms, _ in results)
failure_rate = sum(not ok for _, ok in results) / len(results)
p95 = values[math.ceil(len(values) * 0.95) - 1]
report = {
    "workload": "authenticated documents/reviews/me reads; excludes OCR/model and review writes",
    "requests": args.requests,
    "concurrency": args.concurrency,
    "duration_seconds": round(elapsed, 3),
    "p50_ms": round(values[math.ceil(len(values) * 0.5) - 1], 3),
    "p95_ms": round(p95, 3),
    "failure_rate": failure_rate,
    "requests_per_second": round(len(values) / elapsed, 3),
    "thresholds": {"p95_ms": args.p95_ms, "failure_rate": args.max_failure_rate},
    "passed": p95 <= args.p95_ms and failure_rate <= args.max_failure_rate,
}
args.output.parent.mkdir(parents=True, exist_ok=True)
args.output.write_text(json.dumps(report, indent=2))
print(json.dumps(report))
raise SystemExit(0 if report["passed"] else 1)
