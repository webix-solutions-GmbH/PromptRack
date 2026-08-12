# syntax=docker/dockerfile:1

# ---------------------------------------------------------------------------
# deps — no build toolchain needed: `pg` is pure JavaScript.
# ---------------------------------------------------------------------------
FROM node:22-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

# ---------------------------------------------------------------------------
# builder — next build (output: 'standalone') + freeze the schema into SQL.
# ---------------------------------------------------------------------------
FROM node:22-alpine AS builder
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# drizzle/ is committed, so the image ships the exact migrations that were reviewed.
# Generating here would re-derive a full-schema baseline and defeat incremental diffs.
RUN test -f drizzle/meta/_journal.json \
  || (echo 'drizzle/ missing — run `npm run db:generate` and commit it' && exit 1)
# `next build` imports every route module to collect page data, which reaches
# src/db/index.ts and its "DATABASE_URL is required in production" guard. No
# database is contacted during the build, so a placeholder is enough — the real
# URL comes from compose, and docker-entrypoint.sh refuses to start without it.
RUN DATABASE_URL=postgres://build:build@127.0.0.1:5432/build npm run build

# ---------------------------------------------------------------------------
# runner — standalone server + schema bootstrap, no node_modules install.
# ---------------------------------------------------------------------------
FROM node:22-alpine AS runner
WORKDIR /app

ENV NODE_ENV=production \
    PORT=3000 \
    HOSTNAME=0.0.0.0 \
    NEXT_TELEMETRY_DISABLED=1

COPY --from=builder /app/.next/standalone ./
# scripts/init-db.mjs runs outside Next, so it needs drizzle-orm resolvable from
# /app/node_modules. The standalone trace only carries the modules the app itself
# imports — `drizzle-orm/node-postgres/migrator` is not one of them (R3).
COPY --from=deps /app/node_modules/drizzle-orm ./node_modules/drizzle-orm
COPY --from=builder /app/.next/static ./.next/static
COPY --from=builder /app/public ./public
COPY --from=builder /app/drizzle ./drizzle
COPY --from=builder /app/scripts ./scripts
COPY docker-entrypoint.sh /app/docker-entrypoint.sh

# All state lives in Postgres now; the only writable path the image needs is
# Next's own cache, so it stays uid-agnostic.
RUN chmod +x /app/docker-entrypoint.sh \
  && mkdir -p /app/.next/cache \
  && chmod 777 /app/.next/cache

EXPOSE 3000

ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["node", "server.js"]
