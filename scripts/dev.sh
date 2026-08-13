#!/usr/bin/env bash
# Brings up the dev environment: dockerized postgres, migrations, backend
# (uvicorn --reload on :8000) and frontend (vite on :5173, proxying /api to
# the backend) together. Ctrl-C stops both dev servers; the database is left
# running (see docker-compose.dev.yml) so the next `dev.sh` skips straight to
# "already running".
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"

echo "[dev] starting postgres (docker-compose.dev.yml)"
docker compose -f docker-compose.dev.yml up -d postgres

echo "[dev] waiting for postgres to accept connections"
deadline=$((SECONDS + 60))
until docker compose -f docker-compose.dev.yml exec -T postgres pg_isready -U promptrack -d promptrack >/dev/null 2>&1; do
  if [ "$SECONDS" -ge "$deadline" ]; then
    echo "[dev] postgres did not become ready within 60s" >&2
    exit 1
  fi
  sleep 0.5
done
echo "[dev] postgres ready"

echo "[dev] applying migrations"
(cd backend && uv run alembic upgrade head)

pids=()
cleanup() {
  echo "[dev] stopping"
  for pid in "${pids[@]}"; do
    kill "$pid" 2>/dev/null || true
  done
  wait "${pids[@]}" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "[dev] starting backend (http://localhost:8000)"
(cd backend && uv run uvicorn app.main:app --reload) &
pids+=("$!")

echo "[dev] starting frontend (http://localhost:5173)"
(cd frontend && npm run dev) &
pids+=("$!")

wait -n "${pids[@]}"
