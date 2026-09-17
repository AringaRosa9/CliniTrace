#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../../backend"
exec uv run --frozen uvicorn app.main:app --host 127.0.0.1 --port 18000 --reload --no-access-log
