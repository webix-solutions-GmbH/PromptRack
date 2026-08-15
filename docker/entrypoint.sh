#!/bin/sh
# Applies the committed Alembic migrations before the API starts serving,
# then hands over to CMD (uvicorn). `set -e` means a failed migration stops
# the container rather than serving a half-migrated database — statements
# are applied verbatim, so a broken migration is loud rather than a silent
# no-op.
set -e

if [ -z "$DATABASE_URL" ]; then
  echo "DATABASE_URL is not set" >&2
  exit 1
fi

alembic upgrade head

exec "$@"
