#!/usr/bin/env bash
# Switch only to a previously verified, schema-compatible application dossier.
set -euo pipefail
cd "$(dirname "$0")/../.."
if [[ $# != 3 ]]; then
  echo 'Usage: rollback.sh previous-manifest.json previous-compose.env compatibility-evidence.json' >&2
  exit 2
fi
uv run --project backend python scripts/ops/release_gate.py "$1"
uv run --project backend python scripts/ops/verify_rollback.py "$1" "$2" "$3"
# Prior env must retain current DB/object endpoints, secrets and approved version assets.
docker compose --env-file "$2" -f infra/deploy/compose.yaml stop -t 240 worker dispatcher api
docker compose --env-file "$2" -f infra/deploy/compose.yaml up -d --wait api frontend gateway
# Worker/dispatcher remain paused until operator has checked ready, audit and review/export.
echo 'Application switched. Validate readiness and snapshots, then resume worker/dispatcher explicitly.'
