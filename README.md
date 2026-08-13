# modelfit

modelfit answers two questions for a consultancy that sells AI solutions to businesses:
which model is good enough for a customer's actual job, and what hardware that takes.
It is also "git for your customers' prompts" — the system prompts behind an agentic
tool are versioned assets with immutable history, a deployed pointer, and a baseline run
that proves a version worked.

This is a rewrite in progress: FastAPI + SQLAlchemy backend, Vue 3 + PrimeVue frontend,
Postgres. See `docs/superpowers/plans/2026-08-13-rewrite-fastapi-vue.md` for the plan and
`docs/superpowers/specs/2026-08-13-prompt-versioning-pivot-design.md` for the design. Not
every phase has landed yet — the quickstart below covers what runs today.

## Quick start

```bash
cp .env.example .env    # dev defaults work as-is; see the file for what to change

docker compose -f docker-compose.dev.yml up -d   # postgres:17-alpine on :5433
cd backend && uv run alembic upgrade head && uv run uvicorn app.main:app --reload
```

In a second terminal:

```bash
cd frontend && npm run dev
```

Or bring both dev servers up together, after the database is up:

```bash
scripts/dev.sh
```

- Backend: `http://localhost:8000` (`/api/health`)
- Frontend: `http://localhost:5173` (vite dev server, proxies `/api` to the backend)

## Testing

```bash
cd backend && uv run pytest && uv run ruff check .
cd frontend && npm run build && npm run typecheck
```

Integration tests (`backend/tests/integration/`) run against a throwaway Postgres —
see `scripts/test-integration.sh` once Task 1.4 lands.

## Production

`docker compose up -d --build` once the production build (Task 6.3) lands.
