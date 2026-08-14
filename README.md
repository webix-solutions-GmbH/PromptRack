# PromptRack

PromptRack answers two questions for a consultancy that sells AI solutions to businesses:
**which model is good enough** for a customer's actual job, and **what hardware** that
takes. Not a leaderboard score — the customer's real work, loaded in as test suites: an
invoice-processing agent, document and data extraction, structured extraction from
business correspondence, MCP tool calls against the company's own RAG. Every result names
the machine that produced it, because the second question is sizing: if a small model does
the job, a Mac Mini is enough — but that has to be measured, and TTFT/duration/tok-s per
machine is the evidence.

It is also **"git for your customers' prompts"**: the system prompts behind an agentic
tool are versioned assets, not just text fields. A prompt has a mutable draft and an
immutable commit history; one version is marked `deployed` (a claim about what runs at
the customer today); one run per version can be its `baseline` — the known-good measurement
that a Verify run compares a model swap against. Prompt content lives here so it survives
model upgrades and can be diffed, restored, and proven to still work.

PromptRack is also an **MCP server**: an agent can push prompts, test cases and runs in
from outside, start a run, and read the measurements back — because the interesting test
cases already exist in whatever repository defines the job. See `docs/example-suite/` for
a worked suite built entirely over MCP.

## Architecture

FastAPI + async SQLAlchemy 2.0 + Alembic on Postgres, a Vue 3 + PrimeVue SPA against that
API, and an MCP server (the official Python SDK, streamable HTTP, stateless) mounted at
`/api/mcp` in the same process. Every repository function takes a `Scope` — one customer
workspace — as its first argument, which is what keeps one engagement's machines, prompts
and runs out of another's. Run execution streams NDJSON so a live transcript (including
tool calls, for an agent under test) renders as the model answers.

See `docs/superpowers/plans/2026-08-13-rewrite-fastapi-vue.md` for the implementation plan
and `docs/superpowers/specs/2026-08-13-prompt-versioning-pivot-design.md` for the
versioning design this app is built around.

## Quick start

```bash
cp .env.example .env    # dev defaults work as-is; see the file for what to change

docker compose -f docker-compose.dev.yml up -d   # postgres:17-alpine on :5433
cd backend && uv run alembic upgrade head && uv run uvicorn app.main:app --reload
```

In a second terminal:

```bash
cd frontend && npm install && npm run dev
```

Or bring the database, migrations and both dev servers up together with `make run`
(`make` on its own lists the other targets: `test`, `lint`, `typecheck`, `check`).

- Backend: `http://localhost:8000` (`/api/health`, `/api/mcp`)
- Frontend: `http://localhost:5173` (vite dev server, proxies `/api` to the backend)

Open the frontend and create the first account — it becomes the administrator. Sign-up
closes once one account exists.

## Testing

```bash
cd backend && uv run pytest && uv run ruff check .
cd frontend && npm run build && npm run typecheck
```

Integration tests (`backend/tests/integration/`) run against a throwaway Postgres in
Docker (tmpfs data, port 55432) via `scripts/test-integration.sh` — nothing there depends
on the dev database above.

## Production

```bash
cp .env.example .env    # set POSTGRES_PASSWORD at minimum
docker compose up -d --build
```

A multi-stage `Dockerfile` builds the frontend and bakes it as static files into the
backend image, so one container on one port serves the API, the SPA and the MCP
endpoint; `docker-entrypoint.sh` applies migrations before the app starts. See
`CLAUDE.md`'s Deployment section for the compose service naming and network setup.
