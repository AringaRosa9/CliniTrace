#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../../backend"
uv run celery -A app.workers.runtime:celery worker -Q parsing --loglevel=WARNING --concurrency=2 --max-tasks-per-child=20
