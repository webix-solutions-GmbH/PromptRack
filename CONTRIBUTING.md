# Contributing to PromptRack

PromptRack evaluates whether a given model is good enough for a specific
customer's job — invoice agents, document and data extraction, MCP tool calls
over a company's own RAG — and what hardware that job takes. It is also "git
for your customers' prompts": the system prompts behind an agentic tool are
versioned assets with immutable history, a deployed pointer, and a baseline
run that proves a version worked. It is not a benchmark suite, so changes are
welcome that make a real workload easier to express and its result easier to
judge.

## Setup

```bash
# Backend (Python 3.12+, uv)
docker compose -f docker-compose.dev.yml up -d   # postgres:17-alpine on :5433
cd backend && uv run alembic upgrade head && uv run uvicorn app.main:app --reload

# Frontend (Node 22+), in a second terminal
cd frontend && npm install && npm run dev
```

Or `make run`, which starts the database, applies migrations and runs both dev
servers under `concurrently` so one ctrl-c stops the pair.

Open `http://localhost:5173` and create the first account — it becomes the
administrator. `docs/example-suite/` is a worked evaluation suite an agent can
build into a workspace over MCP if you want something to run.

`CLAUDE.md` is the architecture document: it records the decisions and the
reasoning behind them, and is worth reading before a non-trivial change.
`AGENTS.md` is the standing warning that this stack differs from what most
tooling assumes.

## Before opening a PR

```bash
cd backend && uv run pytest && uv run ruff check .
cd frontend && npm run build && npm run typecheck
```

Integration tests (`backend/tests/integration/`, `scripts/test-integration.sh`)
spin up a throwaway Postgres in Docker and are worth running for anything that
touches a repository function, the run executor, or the MCP server.

All of the above must pass.

## Schema changes

Edit the SQLAlchemy models under `backend/app/models/`, then run
`cd backend && uv run alembic revision --autogenerate -m "..."` to write the
migration under `backend/alembic/versions/`. Read the generated migration
before committing it — autogenerate does not always get renames or
constraint-only changes right — and the migration file itself **must be
committed**.

## Notes for reviewers

Issues and PRs are welcome; the default branch is `master`. This project is
developed against a self-hosted deployment, so a PR that touches deployment
(Docker, compose, migrations) should describe the setup it was tested on.
