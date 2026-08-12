# syntax=docker/dockerfile:1

# ---------------------------------------------------------------------------
# deps — install with the alpine/musl toolchain so better-sqlite3 picks up its
# linuxmusl prebuilt binding.
# ---------------------------------------------------------------------------
FROM node:22-alpine AS deps
WORKDIR /app
# node-gyp needs python3 to even evaluate better-sqlite3's binding.gyp (which
# then short-circuits because a linuxmusl prebuild ships with the package);
# make/g++ are the fallback if it ever has to compile for real.
RUN apk add --no-cache python3 make g++
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
RUN npm run build \
  && rm -rf .next/standalone/data

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
# imports — `drizzle-orm/better-sqlite3/migrator` is not one of them (R3).
COPY --from=deps /app/node_modules/drizzle-orm ./node_modules/drizzle-orm
COPY --from=builder /app/.next/static ./.next/static
COPY --from=builder /app/public ./public
COPY --from=builder /app/drizzle ./drizzle
COPY --from=builder /app/scripts ./scripts
COPY docker-entrypoint.sh /app/docker-entrypoint.sh

# The SQLite file lives on a bind mount whose owner is decided by the host, so
# the image stays uid-agnostic: `docker run --user`/compose `user:` can pin it.
RUN chmod +x /app/docker-entrypoint.sh \
  && mkdir -p /app/data /app/.next/cache \
  && chmod 777 /app/data /app/.next/cache

EXPOSE 3000

ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["node", "server.js"]
