#!/bin/sh
# Make sure the database has the current schema before the Next.js server opens
# it, then hand over to CMD. The schema is applied from the committed migrations
# under /app/drizzle; `set -e` means a failed migration stops the container
# instead of serving a half-migrated database, which is the intended behaviour
# now that failures are loud.
set -e

if [ -z "$DATABASE_URL" ]; then
  echo "DATABASE_URL is not set" >&2
  exit 1
fi

node /app/scripts/init-db.mjs

exec "$@"
