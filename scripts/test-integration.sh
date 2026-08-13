#!/usr/bin/env bash
# Runs the integration suite (backend/tests/integration/**) against a real
# Postgres.
#
# `backend/tests/integration/conftest.py` is what actually provisions the
# database: unless TEST_DATABASE_URL is set, it starts a throwaway
# postgres:17-alpine container (tmpfs data, port 55432), applies the
# committed migrations, and removes the container again once the suite
# finishes — this script is a thin, repo-root convenience around that, same
# role as scripts/dev.sh plays for the dev database.
#
# Set TEST_DATABASE_URL to point at a database you already manage (CI, a
# long-lived local instance) and the harness skips docker entirely.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root/backend"

uv run pytest tests/integration "$@"
