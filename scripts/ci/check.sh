#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
uv run --project backend --frozen ruff check backend scripts/generate_contracts.py evaluation/runners tests/fixtures/synthetic/s4_review.py
uv run --project backend --frozen ruff format --check backend scripts/generate_contracts.py evaluation/runners tests/fixtures/synthetic/s4_review.py
uv run --project backend --frozen mypy --config-file backend/pyproject.toml backend/app
uv run --project backend --frozen pytest backend/tests
pnpm format:check
pnpm lint
pnpm typecheck
pnpm test
pnpm contracts:check
pnpm build
