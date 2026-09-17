#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../../backend"
export SYNTHETIC_ACCESS_CODE=synthetic-e2e-only
uv run celery -A app.workers.runtime:celery worker -Q parsing --loglevel=ERROR --concurrency=2 --pool=prefork > /tmp/bljgh-s1-test-worker.log 2>&1 &
worker_pid=$!
uv run python -m app.workers.runtime > /tmp/bljgh-s1-test-dispatcher.log 2>&1 &
dispatcher_pid=$!
uv run uvicorn app.main:app --host 127.0.0.1 --port 18000 --no-access-log &
api_pid=$!
trap 'kill "$api_pid" "$dispatcher_pid" "$worker_pid" 2>/dev/null || true' EXIT INT TERM
wait "$api_pid"
