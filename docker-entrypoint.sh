#!/bin/sh
# Make sure the (possibly empty) ./data volume has the current schema before the
# Next.js server opens the database, then hand over to CMD.
set -e

node /app/scripts/init-db.mjs

exec "$@"
