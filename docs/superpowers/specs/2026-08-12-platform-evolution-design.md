# Platform Evolution: Multi-User, Customer-Scoped Benchmarking — Design

Date: 2026-08-12
Status: approved pending user review

## Goal

Evolve the single-user LLM benchmarking prototype into a shareable platform for
evaluating business use cases (invoice agents, document extraction, RAG/MCP
integrations) — used both to find the right model for a customer's workload and
to size the hardware to sell with it. Published on GitHub as open source (MIT).

## Decisions (settled with the user)

| Topic | Decision |
|---|---|
| Database | Postgres. Compose bundles a postgres service; `DATABASE_URL` overrides for external/managed DBs. SQLite is dropped, not dual-supported. |
| Existing data | One-time SQLite→Postgres migration script; ki01 production history (runs, ratings, prompts) survives. |
| Migrations | `drizzle/` committed to git; real incremental diffs; no `IF NOT EXISTS` rewriting; switch to drizzle's own migrator (`migrate()` from a startup script), which owns its `__drizzle_migrations` ledger — `__app_migrations` retired, `__app_seeds` added to `schema.ts` so `push`-style tooling can never offer to drop it. |
| Auth | better-auth. Local email/password **plus** generic OIDC (issuer/client via env — works with Entra ID, Keycloak, Authentik, …). Auto-provision on first OIDC login. First account created becomes admin. |
| Roles | `admin` / `member` / `viewer`. Admin: user management + infrastructure credentials (machines, toolset URLs/headers). Member: all content work — prompts, runs, ratings, customers. Viewer: read-only. |
| API auth | Per-user API tokens replace the global `MCP_API_KEY`; MCP writes become attributable and individually revocable. |
| Customer model | A customer is a **workspace label**, not a hard tenant. Customers never log in. Team members switch between customer workspaces. |
| Scoping | **Everything** is customer-scoped, including machines — each engagement registers its own endpoints with its own API keys (confirmed: per-customer boxes get per-customer LLM API keys). No shared pool for now; can be added later if duplication hurts. |
| GitHub | Public repo, MIT license. Scrub internal hostnames (`ki01.webix.de`), host paths (`/home/baum/…`), add `.env.example` + working quickstart. |

## Phases

Each phase gets its own implementation plan (written by a subagent, reviewed
before execution). Order is dependency-driven; 1–5 land before feature work.

### Phase 1 — Foundation: migrations that work

- Commit `drizzle/` (un-ignore in `.gitignore` and `.dockerignore`) so
  `drizzle-kit generate` produces incremental diffs against its own journal.
- Replace `scripts/init-db.mjs`'s hand-rolled applier (and its unsafe
  `IF NOT EXISTS` rewriting) with drizzle's `migrate()`; `__app_migrations` is
  retired, `__app_seeds` moves into `schema.ts`.
- Add `db:init` npm script (`generate` + migrate); fix README setup order.
- Fixes two verified bugs: fresh clone cannot create the DB (`data/` gitignored,
  `drizzle-kit push` can't mkdir), and schema changes silently no-op against
  existing production databases (regenerated full-schema `0000_*.sql` files,
  every statement rewritten to a no-op, exit 0).

### Phase 2 — Postgres

- Schema rewrite: `sqliteTable` → `pgTable`; booleans and epoch-millis
  timestamps get native types; unique constraints and composite PKs port as-is.
- Driver swap in `src/db/index.ts` (currently the only file with SQLite
  imports; app code has zero raw SQL and zero sync driver calls).
- Rewrite `scripts/seed-prompts.mjs` (10 hand-written SQLite prepared
  statements) and `scripts/init-db.mjs` for Postgres.
- Wrap `createRunRecord` in a transaction (today a crash mid-loop leaves a
  partial run).
- Replace the in-memory `Set` execution guard in `src/lib/run-executor.ts` with
  a DB-backed claim (status column or advisory lock) — multi-process safe.
- compose: postgres service + volume; `DATABASE_URL` env; drop better-sqlite3
  native-module tracing from `next.config.ts`.
- One-time migration script: SQLite `app.db` → Postgres, verified row counts.

### Phase 3 — Data-access layer

- One scoped data-access module; the 22 files importing `db` directly go
  through it. Queries take the caller's session + active customer scope, so an
  unscoped query is a type error, not a review problem.
- Fix the "select whole table, filter in JS" pages (`runs/page.tsx`,
  `results/page.tsx` is O(runs × results) per load) with real WHERE clauses.
- Fix known leak paths as scoping lands: `createRunRecord` loads *all* system
  prompts for its snapshot map; compare's `prompt_text` fallback matching must
  not match across customers.
- Add missing index on `run_results.run_id`.

### Phase 4 — Auth

- better-auth: local credentials + generic OIDC provider from env config.
- `users` table + session storage (Postgres); `role` column with
  admin/member/viewer; first account bootstraps as admin; in-app role
  management UI (admin only).
- `middleware.ts` for route-level gating; session + role check at the top of
  all 13 server actions and all route handlers (today: zero checks anywhere;
  every id-taking action is IDOR-shaped).
- Per-user API tokens (hashed at rest) for the MCP endpoint; `MCP_API_KEY`
  removed. Token management UI under the user's profile.
- Gate `/api/mock-llm/*` and `/api/mock-mcp` behind `NODE_ENV !== 'production'`
  (or auth) — they currently ship open in the production image.
- Caddy basic auth becomes optional once app-level auth exists.

### Phase 5 — Customer workspaces

- `customers` table; `customer_id` FK on the five root tables (`machines`,
  `system_prompts`, `toolsets`, `prompt_groups`, `runs`); child tables inherit
  scope via FK (machine_models, tools, prompts, prompt_toolsets, run_results).
- Workspace switcher in the UI; active customer held in session; all queries
  scoped through the Phase-3 layer.
- Migration assigns existing data to a default customer.

### Phase 6 — Features (each brainstormed separately when reached)

- Image attachments on prompts: `attachments` table + bytes under
  `data/attachments/`, serving route, `ChatMessage.content` becomes
  content-part array (touches SSE client, token estimator, snapshots, compare
  matching).
- Structured extraction scoring: JSON-field `expected_output` with mechanical
  field-by-field accuracy — auto-scoring for invoice/extraction cases.
- Matrix runs: one prompt group × N machines/models queued automatically.
- Customer report export: cases tested, models compared, hardware
  recommendation.
- In-app LLM-as-judge configuration (MCP loop already supports it externally).

## Non-goals

- Hard multi-tenant isolation and customer logins (customer = workspace label).
- OIDC *server* functionality (API tokens suffice for MCP).
- SQLite compatibility after Phase 2.

## Testing

- Keep the existing pure-logic unit suite (210 tests) green through every phase.
- Phase 2+ adds integration tests against a scratch Postgres (compose service),
  seeded per-suite — replacing the CLAUDE.md scratch-copy-of-app.db recipe.
- Auth phase adds tests for role gating and ownership checks on server actions.
- The snapshot invariant (editing/deleting never changes past runs' display)
  and the executor state machine (abort/reclaim/resume) are the two critical
  untested paths today; both get tests as they are touched.

## Execution model

Per-phase implementation plans are written by dedicated subagents and reviewed
by the session model before execution; implementation is dispatched to
implementor subagents with review after each task.
