#!/bin/sh
# Make sure the (possibly empty) ./data volume has the current schema before the
# Next.js server opens the database, then hand over to CMD. The schema is applied
# from the committed migrations under /app/drizzle; `set -e` means a failed
# migration stops the container instead of serving a half-migrated database,
# which is the intended behaviour now that failures are loud.
set -e

node /app/scripts/init-db.mjs

exec "$@"
