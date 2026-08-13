# Deployment

The app ships as a Docker image built from the multi-stage `Dockerfile` (`node:22-alpine`,
Next.js `output: 'standalone'`). `docker compose up -d --build` brings up two services:
`postgres` and `modelfit`, the latter waiting for the database to report healthy.

## Configuration

Put these in a `.env` file next to `docker-compose.yml`:

```dotenv
POSTGRES_PASSWORD=replace-with-a-strong-password
BETTER_AUTH_SECRET=replace-with-a-random-secret   # openssl rand -base64 32
BETTER_AUTH_URL=https://modelfit.example.com
```

Compose refuses to start without `POSTGRES_PASSWORD`. `BETTER_AUTH_URL` is the **public**
origin: every auth URL, the OIDC `redirect_uri` included, is built from it, so getting it
wrong breaks sign-in in ways that look like a provider problem. `DATABASE_URL` is optional
and overrides the bundled database with an external one.

OIDC variables are documented in `.env.example` and in [auth.md](auth.md).

## Data

State lives in the named volume `pgdata` — no bind mount, no uid matching to get right.

The Postgres role and database keep the historical name `agentval`, from before the app was
renamed to modelfit. That is deliberate: an existing volume holds a database under that name,
and renaming it would orphan the data for a cosmetic gain. The same applies to the `amv_`
API-token prefix. Back up with:

```bash
docker compose exec -T postgres pg_dump -U agentval agentval > backup.sql
```

Compose derives its project name from the **directory name**, so the volume is really
`<dirname>_pgdata`. Renaming the deployment directory therefore makes compose look for a
volume that does not exist, and Postgres starts on an empty database while the real one sits
there orphaned. Move the data first if you have to rename it.

## Reverse proxy

The `modelfit` service listens on port 3000 in the container and is published on
`127.0.0.1:3100`, i.e. localhost only — put a reverse proxy in front of it for external
access. `docker-compose.yml` joins no external network out of the box; a commented block
shows how to attach it to a proxy stack's network so the proxy reaches it as `modelfit:3000`.

If that proxy adds its own HTTP basic auth, an MCP client has to send both credentials in one
request: basic auth in `Authorization`, its API token in `x-api-key`. That is why `x-api-key`
is read first.

The app is built with `basePath: ''` (`src/lib/base-path.ts`), so it serves at the root
everywhere, dev included. Set a non-empty `BASE_PATH` there and rebuild if it ever has to
share a hostname with other services; the proxy then forwards `/<prefix>*` to `modelfit:3000`.
Raw `fetch()` calls to the app's own API routes must go through `apiPath()` from that file —
`next/link` and the router add the (currently empty) prefix automatically.

## Schema bootstrap on start

The database can be completely empty on first start, so the schema is applied when the
container starts rather than when the image is built:

1. `src/db/schema.ts` is the source of truth. `npm run db:generate` writes an incremental SQL
   migration under `drizzle/`, which is committed and copied into the image as-is — the image
   ships the exact SQL that was reviewed. Files under `drizzle/` are never hand-edited; a
   migration is reviewed like any other code change.
2. `docker-entrypoint.sh` refuses to start without `DATABASE_URL`, then runs
   `scripts/init-db.mjs` before the server. It calls drizzle's `migrate()`, which applies
   whatever migrations the database has not seen and records them in `__drizzle_migrations`.
   Statements are applied verbatim, so a migration that cannot apply cleanly stops the
   container instead of silently no-opping.

This beats shipping drizzle-kit in the runtime image: the bootstrap needs nothing but `pg` and
`drizzle-orm`, both already in the runner image, so no dev dependencies and no TypeScript.

`npm run db:migrate` is the same script against your local database; `db:init` is
`db:generate` followed by it.
