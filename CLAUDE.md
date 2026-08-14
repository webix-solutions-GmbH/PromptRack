# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Environment

Backend: Python 3.12+, dependencies and virtualenv managed by `uv` (`backend/pyproject.toml`).
Frontend: Node 22+, npm.

## Commands

```bash
docker compose -f docker-compose.dev.yml up -d          # postgres:17-alpine on 127.0.0.1:5433
cd backend && uv run alembic upgrade head                 # apply migrations
cd backend && uv run uvicorn app.main:app --reload --port 8077   # http://localhost:8077
cd frontend && npm install && npm run dev                  # http://localhost:5177, proxies /api

make run                             # db (waits for healthy) + migrations + both dev
                                     # servers via concurrently; ctrl-c stops both.
                                     # `make` alone lists every target

cd backend && uv run pytest                                 # pure suite, no database
cd backend && uv run pytest tests/test_llm.py                # single test file
cd backend && uv run pytest tests/integration                # throwaway postgres in docker, port 55432
scripts/test-integration.sh                                  # same, from the repo root
cd backend && uv run ruff check .                            # lint

cd frontend && npm run build          # vue-tsc -b && vite build; catches type errors too
cd frontend && npm run typecheck      # vue-tsc --noEmit only

cd backend && uv run alembic revision --autogenerate -m "..."  # write a migration from model changes
```

Everything reads `DATABASE_URL` (`backend/app/config.py`, `Settings`, pydantic-settings —
field names map case-insensitively to env vars). `docker-compose.dev.yml` brings up
Postgres on `127.0.0.1:5433`; `backend/tests/integration/conftest.py` provisions its own
throwaway Postgres on `55432` (docker, tmpfs data) unless `TEST_DATABASE_URL` is set, so
the integration suite never touches the dev database. `.env.example` at the repo root
documents every variable (`app/config.py`'s `Settings`, pydantic-settings, reads a `.env`
file plus the process environment); every field has a working dev default, so a fresh
clone runs with no `.env` at all until you need to change something.

Migrations are committed under `backend/alembic/versions/`. `alembic revision
--autogenerate` compares the SQLAlchemy models to the database and writes a migration —
read it before committing: autogenerate does not reliably infer a *rename* (it will drop
and recreate a column instead), so a rename needs the generated file hand-edited into a
`op.alter_column`/`op.rename_table` the same way `drizzle-kit generate` needed a real
terminal for the equivalent case in the old stack.

Git: branch is `rewrite`, built on top of `master` (the retired Next.js/TypeScript
implementation, kept for reference — see "This is a rewrite" below). `origin` is GitHub
(`philphilphil/modelfit`).

## What this is

**PromptRack** (formerly modelfit) answers two questions for a consultancy that sells AI
solutions to businesses: **which model is good enough for this customer's actual job**,
and **what hardware that takes**. Not a leaderboard score — the customer's real work,
loaded in as test suites: an invoice-processing agent, document and data extraction,
structured extraction from business correspondence, MCP tool calls against the company's
own RAG. The suites are the specification of the job, and the app's answer is a fitness
verdict per model on *those* test cases, never a general ranking.

The second question is sizing, and it is why every result names the **machine** that
produced it (an endpoint plus free-text hardware notes): if a small model does the job, a
DGX Spark or even a Mac Mini is enough — but that has to be measured, and
TTFT/duration/tok-s per machine is the evidence. Endpoints are anything
OpenAI-compatible, so this is **not local-only**: Ollama / LM Studio / vLLM on your own
boxes *and* hosted frontier APIs, side by side in one matrix. The common outcome is a
**mixed deployment** — most workloads self-hosted, the hard ones routed to a frontier
model — and the app exists to find where that line falls. Workspaces are per **customer
engagement**, which is the whole reason they exist: one engagement's machines, prompts
and runs stay out of another's.

Mechanically, multi-user and multi-workspace: author test cases (grouped, optionally with
expected output — the rubric), run them sequentially against a machine's endpoint,
measure TTFT/duration/tokens/tok-s, rate results good/meh/bad manually, compare in a
matrix by model or by run. A test case can also be a **tool test**: offer the model a set
of functions and either record what it wanted to call, or really execute the calls
through an MCP server and loop until it answers — which is how an invoice agent or a
RAG-backed assistant gets evaluated as the agent it will actually be, not as a chat
completion.

**PromptRack is also "git for your customers' prompts."** The system prompt behind an
agentic tool is a versioned asset, not a text field on a test case: a mutable draft, an
explicit commit that freezes an immutable version with a message, a `deployed` pointer
(a human's bookkeeping claim about what runs at the customer today) and a `baseline` run
pointer per version (the known-good measurement that justified deploying it). A model
swap's regression check is then: open the baseline version's Verify link, run the same
test cases against the new model, and compare against the baseline run in `/results`.
See "Prompt versioning" below.

### This is a rewrite

The app used to be a Next.js/TypeScript/Drizzle monolith (still on `master`, referenced
below as "the old app"). This branch is a from-scratch rewrite to FastAPI + SQLAlchemy +
Vue, done alongside a domain-model pivot: `system_prompts` → `prompts` (the versioned
asset), the old `prompts` (input + expected output) → `test_cases`, `prompt_groups` →
`test_groups`, `prompt_toolsets` → `test_case_toolsets`. Old code is a *behavioral*
reference, not a structural one — `git show master:<path>` to read it, never check it out
into this tree. Every service module that ports old logic says so in its docstring with
the exact old path (e.g. `Port of git show master:src/lib/llm.ts`), which is the fastest
way to find the reference for anything below. See
`docs/superpowers/plans/2026-08-13-rewrite-fastapi-vue.md` for the phased implementation
plan and `docs/superpowers/specs/2026-08-13-prompt-versioning-pivot-design.md` for the
versioning design itself.

## Architecture

- **Backend**: FastAPI + SQLAlchemy 2.0 (async, `asyncpg`) + Alembic + Pydantic v2, on
  Postgres. `backend/app/db.py` exports the async engine, the `async_session` factory and
  the `get_session` FastAPI dependency (`DbSession` in `app/auth/guards.py`).
  `backend/app/models/` is the single source of truth for the schema (one module per
  domain area: `customers`, `machines`, `prompts`, `test_cases`, `toolsets`, `runs`,
  `auth`); `alembic revision --autogenerate` writes migrations from it.
  - Enum-ish columns are `Text` + a Python `Literal`, not a Postgres enum — same reasoning
    as the old app's `text('x', { enum: [...] })`: adding a rating or status value needs
    no migration.
  - `tokens_per_sec` is `Double` (float8), matching the old app's deliberate avoidance of
    a 4-byte float that would round every value.
- **Frontend**: Vue 3 + Vite + PrimeVue 4 + Pinia, an SPA against the FastAPI API.
  `frontend/src/api/client.ts` is the thin `fetch` wrapper every `src/api/*.ts` module
  uses; it throws `ApiError { status, message }` from the envelope
  `app/main.py`'s exception handlers write (`{"message": ...}` on every error, so a
  guard's 403 and a validation 422 read the same to the client). `vite.config.ts` proxies
  `/api` to `http://localhost:8077` in dev.
- **Auth is session-cookie-based**, checked by FastAPI dependencies
  (`app/auth/guards.py`): `CurrentUser`, `Writer` (`require_writer`), `Admin`
  (`require_admin`). There is no client-side route "protection" beyond a
  `router.beforeEach` guard in `frontend/src/router/index.ts` that keeps the SPA from
  rendering a page it cannot use — the API enforces the real boundary.
- **MCP is mounted in the same process**, not a separate service: `POST /api/mcp`
  (`backend/app/mcp/server.py`, the official `mcp` Python SDK, FastMCP, streamable HTTP,
  stateless). See "This app as an MCP server" below.

### Snapshot model (the core invariant)

Editing or deleting test cases, prompts, machines, or toolsets must never change how a
past run displays. `create_run_record` (`backend/app/services/run_create.py`) freezes
everything into `run_results` rows at creation time: test-case text, title, group name,
expected output, the **already-resolved** effective prompt, `tools_snapshot`, and now
`prompt_version_id` — the version that draft happened to be byte-identical to, if any (see
"Prompt versioning" below). `test_case_id`/`machine_id` FKs are kept (`SET NULL` on
delete) only for cross-run comparison; rendering always uses the snapshots.

Validation (test-group ids, tool config, tool-name collisions) and the endpoint probe
happen *outside* `create_run_record`'s transaction, in that order: validation throws
before anything is written, and the probe is a network call that must never hold a
transaction open. Only the three writes — the run row, all of its `run_results` in one
multi-row insert, the `machine_models` upsert — are one unit
(`app.repos.scoped.transaction`, a `SAVEPOINT` if the caller is already inside one), so a
crash between them can no longer leave a run with no test cases in it, which Resume would
have reported as finished.

The line between frozen and live is **content vs. credentials**: test-case text, tool
definitions and a manual tool's canned response travel with the run; a machine's
`base_url`/`api_key` and a toolset's `mcp_url`/headers are read live at execution time so
a moved endpoint doesn't break Resume.

### Prompt versioning (the pivot)

`prompts.content` is the mutable **draft** — what the editor writes to on every save, no
version created. `prompt_versions` is the immutable history: a child of `prompts`
(`prompt_id` **CASCADE** — history dies with the asset, but every past run keeps its own
snapshot regardless), never edited or deleted individually, `version` sequential per
prompt (`max + 1`, computed inside the commit transaction; a unique index on
`(prompt_id, version)` is the backstop).

`backend/app/repos/prompt_versions.py` owns all of this:

- **`commit_version`** freezes the draft as the next version with a message. Refused
  (`NoChangesError`) when the draft is byte-identical to the head version — a commit that
  records no change is history nobody can read. The pure rule lives in
  `backend/app/services/attribution.py`: `is_dirty` (the editor's dirty indicator, and
  this refusal inverted), `head_version`, `match_version`.
- **`deployed_version_id`** (on `prompts`, plus `deployed_at`/`deployed_by`) is a human's
  bookkeeping claim — "this version is live at the customer" — set from the UI only,
  **never over MCP**, the same reasoning that keeps customer workspaces unwritable there.
  Must belong to the same prompt (app-level check, `assert_same_customer` plus a
  same-prompt check).
- **`baseline_run_id`** (on `prompt_versions`, `SET NULL` on run delete) is the known-good
  run that justified deploying a version, and the reference point a regression check after
  a model swap compares against. `set_baseline` refuses (`NotAttributedError`) unless that
  run's results are actually **attributed** to this version — see below.
- **Attribution, not selection.** `run_results.prompt_version_id` (`SET NULL`) is set at
  run creation *only* when the draft text is byte-equal to a committed version (a "clean
  working tree"); `None` means the run tested a dirty draft or used no prompt. There is no
  version picker at run creation — a run always tests the current draft, exactly as
  before the pivot; the column is attribution, computed after the fact by
  `match_version(draft_text, versions)` inside the existing scoped read, matching **newest
  first** so a revert (a new commit whose content equals an older version) attributes to
  the new commit, not the old one.
- **Diff**: `backend/app/services/diff.py`'s `unified_diff` (stdlib `difflib`, no Monaco
  or `vue-diff` dependency) renders a version against the draft, the deployed version, or
  any other version. `GET /api/prompts/{id}/diff?from=&to=` accepts a version id or the
  literal `draft` on either side.
- **Restore** copies a version's content into the draft (`POST /{id}/restore`) — a
  rollback is then committed like any other change, so the history stays linear and
  truthful about what happened rather than gaining a branch.
- **Verify flow**: a version with a baseline run shows a Verify link into `/runs/new`
  prefilled with that run's test groups; the finished run's page links "compare against
  baseline" → `/results?mode=runs&runs=<baseline>,<new>`. No separate diff-viewing UI —
  the existing run-mode matrix is the behavior-diff viewer.
- **Deployed vs. head signal**: when `deployed_version_id` is not the newest version, the
  prompt list and editor show e.g. "deployed v3, head is v5" — the one-glance answer to
  "is what's live at the customer what we last verified."

### Data access — the Scope pattern

No page, action, route handler or MCP tool touches `app.db`'s session-independent engine
or the `Pool` directly outside `backend/app/db.py`, `app/services/run_lock.py` (needs the
engine itself for a Postgres advisory lock, on its own connection — see "Run execution"),
and `app/auth/sessions.py` / `tokens.py` / `users.py`, which own the auth tables a `Scope`
is derived *from* and so cannot themselves be read through a scoped repository. Every
other query goes through a repository function in `backend/app/repos/*` whose functions
all take a `Scope` (`backend/app/scope.py`) as their first argument.

`Scope` is a frozen dataclass that can only be constructed by one of three functions
(an `InitVar` key guard raises on any other construction path):

- **`scope_for_customer(id)`** — the signed-in user's active workspace. Only the request
  layer calls it (`app.auth.guards.current_scope`, which resolves it through
  `active_workspace`/`resolve_active_customer_id`).
- **`scope_from_row(id)`** — derived from a row that already carries its own workspace,
  which is how background work stays scoped: the executor runs outside any request (MCP
  `execute_run` is fire-and-forget), so it reads the run row and takes the scope *from*
  it; MCP tool calls derive their scope from the resolved customer row the same way (see
  "This app as an MCP server").
- **`system_scope(reason)`** — the grep-able escape hatch meaning "every workspace":
  `scope_where` returns no predicate for it (a read may deliberately span workspaces),
  `scope_values` raises (an insert has no defensible workspace to land in).

`scope_where` / `scope_values` / `where_scoped` (root tables) and
`scope_through_parent` (`app/repos/scoped.py`, child tables that inherit scope through a
foreign key — a read expresses it as a join, an `UPDATE`/`DELETE` as an `IN (SELECT ...)`
subquery) are the seams that turn a `Scope` into SQL. `app.repos.scoped.transaction` is
the one place a transaction leaves this layer: it hands a nested-safe context manager
(`SAVEPOINT` if already inside one — notably true under the integration test harness,
which wraps each test) to callers like `create_run_record` that need several writes to be
atomic without knowing where the request's own transaction boundary is.

`app.models` stays importable everywhere — API response models legitimately reference ORM
types. Only the session/engine handle is restricted.

### Customer workspaces

A workspace (`customers`) is a **label, not a tenant**: customers never log in, and every
signed-in user can switch into any of them. It is what keeps one engagement's
machines — i.e. base URLs with API keys — from mixing with another's.

- The five root tables (`machines`, `prompts`, `toolsets`, `test_groups`, `runs`) carry
  `customer_id NOT NULL`. The child tables (`machine_models`, `tools`, `test_cases`,
  `test_case_toolsets`, `run_results`, and now `prompt_versions`) carry **nothing**: they
  inherit scope through their parent FK. Three cross-root references can only be checked
  in app code — a test case's group, a test case's prompt, a test case's toolsets, a run's
  machine, plus the pivot's two new ones (a prompt's `deployed_version_id`, a version's
  `baseline_run_id`) — via `assert_same_customer` (`backend/app/repos/customers.py`),
  called from inside the repository functions so no call site can forget it.
- `ON DELETE RESTRICT` on all five root tables, deliberately: a cascade would silently
  destroy run history. `delete_customer` is admin-only, refuses a workspace that still
  holds anything (listing what it holds), and refuses the last workspace, because every
  scope has to resolve to one. **Archiving** (`customers.archived_at`) is the soft path,
  same pattern as `runs.archived_at`.
- **The active workspace lives on the user row** (`users.active_customer_id`,
  `ON DELETE SET NULL`), not a cookie — unforgeable from the client, survives a session
  refresh, one place that says it. `resolve_active_customer_id` (pure, in
  `backend/app/scope.py`) ignores a stale pointer and falls back to the oldest live
  workspace; `app.auth.guards.active_workspace` writes the resolution back, so a workspace
  archived under a user logs them into the fallback rather than into an empty app.
- `system_scope(reason)` means "every workspace" — see the Scope section above.
- **Not yet ported**: the old app rendered a deep link into another workspace (`/runs/42`
  from a different customer) as a switch notice rather than a 404, via two deliberately
  unscoped reads (`findRunWorkspace`, `findMachineWorkspace`) exposing nothing but a
  workspace name the switcher already lists. Nothing in `frontend/src/views` does this yet
  — a deep link into the wrong workspace currently surfaces whatever the scoped 404 says.
- **MCP scope precedence**: `customer` argument → `X-Customer` header → the token's
  default → refusal naming both and listing the workspaces. See "This app as an MCP
  server" below.

### Auth

`backend/app/auth/`: argon2id password hashing (`passwords.py`), DB-backed sessions
(`sessions.py` — random 32-byte token, SHA-256 stored, `HttpOnly` cookie, sliding expiry),
role semantics (`policy.py`: `can_write`, `can_administer`, `parse_role` — degrades any
unrecognised value to `viewer`, never to admin), and the FastAPI dependency guards
(`guards.py`: `CurrentUser`, `Writer`, `Admin`, `CurrentScope` — see above).

- **Roles**: `admin` / `member` / `viewer`, all semantics in `policy.py`'s two pure
  predicates. Content vs. credentials is the line: toolset create/update/delete is admin
  (it holds `mcp_url` + headers), the tools *inside* it are member; machines are admin,
  `POST /api/machines/{id}/discover` is member (`/runs/new` posts it on page load for
  everyone), `POST /{id}/test` stays admin (it exercises the stored API key).
- **First account is the administrator, then sign-up closes forever** — `app/auth/router.py`
  refuses `POST /api/auth/sign-up` once the `users` table is non-empty.
- **API tokens** (`backend/app/auth/tokens.py`, `backend/app/api/tokens.py`): 32 random
  bytes prefixed `prk_`, stored as SHA-256, shown exactly once, a 12-char display prefix.
  A token acts as its owner and carries their role — `presented_token`
  (`app/auth/guards.py`) reads `x-api-key` **before** `Authorization: Bearer`, so a
  reverse-proxy basic-auth credential and an MCP client's token both fit in one request
  (see `CLAUDE.local.md`'s production note, if present in your checkout). Ownership is
  baked into every query; there is no admin override on another user's tokens.
- **OIDC is optional and generic** (`backend/app/auth/oidc.py`, Authlib): an unset
  `OIDC_ISSUER` mounts no `/api/auth/oidc/*` routes at all rather than 404ing per-route.
  `OIDC_DEFAULT_ROLE` (default `member`) is read through `parse_role`, never trusted
  verbatim.
- **Frontend**: `frontend/src/stores/auth.ts` (Pinia) holds `user`, `canWrite`,
  `canAdminister`, `setupRequired`; `router.beforeEach` in `frontend/src/router/index.ts`
  is the single enforcement point on the client (optimistic only — the API is the real
  boundary), redirecting to `/setup` when no user exists yet and `/login` otherwise.
  Controls a role cannot use are hidden by passing those booleans into components, never
  rendered-then-disabled.

### Effective prompt resolution

A test case references an optional base prompt plus a mode: `append` (base + `"\n\n"` +
custom text) or `override` (custom text only); a whitespace-only or empty result means no
system message. Pure function `resolve_effective_prompt` in
`backend/app/services/effective_prompt.py` — used at run creation (the snapshot) and by
`POST /api/test-cases/effective-prompt` for the live preview in the editor.

### Run execution pipeline

`backend/app/services/llm.py` (raw-`httpx` SSE client, no vendor SDK) →
`backend/app/services/tool_loop.py` (one to N turns) →
`backend/app/services/executor.py` (sequential loop over rows) →
`POST /api/runs/{id}/execute` (NDJSON) → the frontend's run-detail view drives it live.

- `llm.py` parses SSE tolerant of provider differences (usage in a final empty-choices
  chunk vs. on the last content chunk; chunks split across reads, including mid-JSON tool
  call fragments keyed by `index`/`id`/nothing). No usage received → estimate
  `ceil(chars/4)` over text **plus** serialized tool calls, `tokens_estimated=True` (UI
  shows `~`). TTFT = first content delta **or** first tool-call fragment, whichever comes
  first — a tool-call-only response streams no content. Only connection-level failures
  raise `LlmError`.
- **Executor invariants** (`app/services/executor.py`, `app/services/run_lock.py`): one
  execution per run via a Postgres **advisory lock** (`pg_try_advisory_lock` on its own
  connection, `AUTOCOMMIT`, held for the whole run — it dies with the connection, so a
  crashed process releases it the same way an in-memory set used to vanish, while more
  than one app process is safe). `is_run_executing` reads `pg_locks` rather than taking
  the lock, so asking never accidentally answers. Every result row is persisted the
  moment it finishes; a row error marks it `error` and the loop continues; disconnect
  (an explicit `asyncio.Event`, not task cancellation — a cancelled scope cannot safely
  `await` the row-reset write) resets the in-flight row to `pending`; rows stuck
  `running` from a crashed process are reclaimed to `pending` at the next execution's
  start. Run status `failed` is reserved for "every attempted result died at connection
  level"; partial errors still end `completed`.
- Execution runs as a **detached background task**, not tied to the request's own task
  group (Starlette cancels a streaming response's task group on client disconnect, and a
  cancelled scope cannot safely finish writing the in-flight row back to `pending`) — the
  route sets a cancellation flag and the executor notices it. Resume picks up remaining
  `pending` rows by calling execute again.
- `tokens_per_sec = completion_tokens / ((duration_ms - ttft_ms) / 1000)` — rate over the
  generation window, not total duration. For a multi-turn tool run the denominator is the
  **sum of each turn's own** generation window (`aggregate` in `tool_loop.py`), so later
  prefills aren't counted as generation; for a single turn it reduces to exactly the
  formula above.

### Tool / API calling

A test case has a `tool_mode`: `none` (classic one-shot), `definitions` (offer the tools,
record the calls, execute nothing), or `execute` (run each call, feed the result back,
loop to `max_turns`). It selects **any number** of toolsets — duplicate tool names across
selected toolsets are refused, both in the test-case editor and again in run creation, by
the one shared function `assert_tool_config`
(`backend/app/services/tool_config.py`).

- **Toolsets** are `manual` (tools authored in the UI, answering with `mock_response`
  verbatim — what keeps a multi-turn test deterministic) or `mcp` (tools discovered from a
  streamable-HTTP MCP server and really executed against it,
  `backend/app/services/mcp_client.py`, the official `mcp` SDK, connections opened
  per-operation, never pooled). `tools` rows follow the `machine_models` precedent:
  discovery upserts and **never deletes** — a tool absent from `tools/list` only flips
  `enabled` false.
- **A tool failure is never a failed row.** The error text is serialized back to the
  model as that tool's output — what a real agent sees, and itself worth measuring. Only
  connection-level `LlmError`s can fail a row.
- The loop stops **before** executing calls it has no turn budget left to use, so a real
  ERP never gets hit for results that could not reach the model. `stopped_reason` is
  `stop` / `definitions_only` / `max_turns`.
- Metric columns keep the old meaning — `response_text` = final assistant text, `ttft_ms`
  = first turn's TTFT, `duration_ms`/token columns = sums over model turns only (tool wait
  time excluded, and lives per call in the transcript). Tool detail is *added alongside*
  in `transcript_json` / `turns_json` / `turn_count` / `tool_call_count`, all null when
  `tool_mode = "none"`.

### Results (`/results`): two pivots

`GET /api/results/matrix` (`backend/app/api/results.py`, pure logic in
`backend/app/services/compare.py`, scoped reads in `backend/app/repos/results.py`) has a
`mode`: `models` (default) and `runs`. `?mode=` wins; without it a URL carrying `?runs=`
stays in run mode, so old links keep their view. **One model is a valid selection**
(`MIN_COMPARE_MODELS = 1`) — the same matrix with a single column is "show me everything
this model answered", the cheapest review of a model across all of its runs. Run mode
still needs two, since a single run is already its own detail page.

- **By model** (`model=<machineId>|<modelId>`, repeated, plus `?group=` to narrow the
  rows) takes the **live test cases** as rows and fills each cell with that model's
  **most recent `ok` result**, whichever run produced it. Columns are keyed on machine
  *id* + model, so a machine rename doesn't split a column and one model on two boxes
  stays two columns. Archived runs are excluded outright.
- **By run** (`runs=1,5`) is the only pivot that can put two runs of the *same* model
  side by side (quantization swap, temperature A/B, a Verify comparison against a
  baseline) — in model mode they collapse into one column and the newest wins.

Falling back past a **newer failed attempt** must not blank a good older answer, so a
model-mode cell keeps the newest `ok` row and reports the skipped one
(`superseded` on the cell). `describe_row_drift` (`app.services.compare`) compares
test-case text / effective prompt / `tools_snapshot` / tool mode / tool choice / run
params across a row (and against the live test case, in model mode → "test case edited
since") and names whatever is not held constant, since a difference between cells might
be config rather than model.

### Ratings

Three manual verdicts plus unrated: `good` / `meh` / `bad`. `meh` means "not wrong, but
not good enough" — usually a signal the *test case* needs work rather than the model.
Named `meh` and not `ok` on purpose: `ok` already means "completed without error" as a
`run_results.status`, and one word meaning two things in the same table is exactly the
confusion this rating exists to remove. The column is `Text`, not an enum type, so adding
a rating value needs no migration.

### Machine/model history

`machine_models` records every model ever seen per machine and is never deleted from:
discovery upserts (`currently_loaded` flips false for models absent from `/v1/models`),
manual adds, and every run (`source: "run"`). A machine IS an endpoint (`base_url` +
optional `api_key` + free-text hardware specs). Live probing is
`backend/app/services/discovery.py` (`POST /{id}/discover`, member — upserts into
`machine_models`; `POST /{id}/test`, admin — just reports reachability, since it
exercises the stored API key).

### This app as an MCP server

`POST /api/mcp` (`backend/app/mcp/server.py`) lets an agent author the evaluation from
outside: push another project's real prompts and test cases in, start a run, read the
measurements back — the interesting test cases already exist in other repos.

- **The official `mcp` Python SDK**, FastMCP, streamable HTTP, **stateless** (no session
  id — every POST is independent, which is what lets the auth middleware and the
  workspace precedence chain both be per-request rather than per-connection state).
  Mounted as a route (`mount_mcp`, `MCP_PATH = "/api/mcp"`) rather than a sub-application,
  because a mount only matches the path with a trailing slash and would 307-redirect the
  documented one.
- **Auth** (`McpAuthMiddleware`, an ASGI middleware wrapping the SDK's own app) reads a
  per-user API token from `x-api-key` before `Authorization: Bearer` (see the Auth
  section above); a session cookie is accepted too. A refusal is a plain HTTP 401 with a
  `WWW-Authenticate` challenge, once per request, rather than a refusal shaped like a
  tool result. A tool's own refusal (bad arguments, a role gate) is different: `isError`
  content, not a JSON-RPC error, so the calling model reads the message and can act on
  it — same reasoning as a tool failure being fed back into the loop rather than failing
  the row.
- **`_WRITES`** (a module-level dict built by the `_tool` registration decorator) is the
  single declaration of whether each tool writes: it becomes both the `readOnlyHint`
  annotation and the gate a viewer's token is refused by, so the two cannot drift apart.
- **Everything relatable by name is** (`backend/app/mcp/refs.py`: `RowRef`,
  `parse_row_ref`, `resolve_row_ref`) — group, prompt, toolset, machine take a name or an
  id, a numeric string is always an id, and an ambiguous name is refused with a
  "Known: …" list of that workspace's rows.
- **Every call names a customer workspace** (`backend/app/mcp/customer.py`):
  `customer` argument → `X-Customer` header → the token's default (always `None` today —
  `api_tokens` has no customer column yet, chain written out anyway so adding it is one
  line) → refusal listing the known workspaces. `list_customers` is the one tool that
  needs no scope, and it's `readOnly` so a viewer's token can orient itself before being
  refused a write elsewhere.
- **The 20 tools** (registered in `backend/app/mcp/server.py`, renamed per the pivot):
  `list_customers`, `list_machines`, `list_prompts`, `create_prompt`, `update_prompt`,
  `commit_prompt`, `list_prompt_versions`, `get_prompt_version`, `set_baseline`,
  `list_test_groups`, `create_test_group` (name-idempotent — a second call returns the
  existing group, `created: false`), `list_test_cases`, `create_test_case`,
  `update_test_case` (patches only the keys present, and re-checks tool config as it will
  be *after* the patch), `create_run`, `execute_run` (fire-and-forget — safe only because
  the executor already persists every row as it finishes), `get_run`, `get_run_result`,
  `list_runs`, `set_rating` (refuses a still-pending/running row; omitting `note` leaves
  an existing one untouched, `"unrated"` clears the rating — JSON-RPC cannot distinguish
  "absent" from "null" by the time an argument reaches the tool). `mark_deployed` and
  `delete_test_case`/`delete_prompt` are **deliberately absent** — deploying is a UI-only
  human claim about a customer's production system, and there is currently no delete
  surface over MCP at all (see the example suite's note on this for the practical
  consequence).
- **Not writable over MCP**: machines, toolsets and tools (a base URL with an API key and
  an MCP server URL are credentials — the app's line is content over the API, credentials
  in the UI), customer workspaces (creating an engagement is a human decision with
  billing behind it), and a prompt's `deployed_version_id` (see "Prompt versioning").
  Versions themselves *are* writable over MCP (`commit_prompt`, `set_baseline`) because
  they are content, not credentials.
- **A judge model reading these results is itself injectable.** `get_run_result` returns
  `prompt_text`/`test_case_text`, which for the `Prompt Injection & Instruction
  Hierarchy` group carry live payloads. Grade from `expected_output` + `response`, and
  never let a judge's output pick a tool call. Most of that group needs no judge at all —
  the rubrics are canary strings and "was this tool called."

Tests: `backend/tests/test_mcp.py` (pure — argument coercion via `refs.py`, workspace
precedence via `customer.py`, dispatch); `backend/tests/integration/test_mcp_api.py`
exercises the wired-up handler (real Postgres, no server process) via
`backend/tests/integration/conftest.py`.

### The example suite

`docs/example-suite/` is the standard suite — 3 manual toolsets, 38 test cases in 4
groups — written as **documentation an agent executes over MCP**, not a script. The split
inside it is forced by the app's own rule: toolsets are not writable over MCP (they hold
an MCP URL and headers, i.e. credentials), so `toolsets.md` is instructions for a human
in the UI and the four group files are for an agent to push in with `create_test_group` /
`create_prompt` / `create_test_case`.

Canned tool responses are written to stay correct *whatever arguments the model
passes* — `convert_currency` returns a rate rather than a converted amount, so the
response can never contradict the call and the model still has to do the arithmetic.

**The `Prompt Injection & Instruction Hierarchy` group inverts that principle on
purpose**: there the canned response *is* the attack, because `mock_response` is the
tool-result channel — the one place a real agent meets attacker-controlled text, and the
one with far less refusal training behind it than the user channel. Two invariants that
group depends on: every prompt scores task completion *and* injection resistance, and two
test cases (13, 14) fail on **over-defense** instead — a model that refuses everything
instruction-shaped scores perfectly on an attack-only suite and is useless on real order
and invoice correspondence, where "please ignore my previous email" is what customers
actually write; and `expected_output` is **never sent to the model**
(`run_create.py`/`tool_loop.py` build the user message from `content` alone), which is
what lets the rating aids state a payload or a canary outright.

## Testing

Two suites, split by whether they need a database.

`cd backend && uv run pytest` (`backend/tests/*.py`, excludes `tests/integration` via
`pyproject.toml`'s `addopts`) is the pure one and must stay database-free and fast:
`test_effective_prompt.py`, `test_llm.py` (SSE fixtures per provider style), `test_compare.py`,
`test_tool_config.py`, `test_tool_loop.py` (metric aggregation), `test_diff.py`,
`test_attribution.py` (version matching, dirty detection), `test_mcp.py`, `test_scope.py`
(the branded `Scope`, `combine`, `resolve_active_customer_id` — written db-free
precisely so it can live here), `test_policy.py`, `test_passwords.py`, `test_tokens.py`
(the token crypto's pure half — resolving a raw token needs a database),
`test_discovery.py`, `test_llm_info.py`, `test_mocks.py`, `test_health.py`.

`cd backend && uv run pytest tests/integration` (or `scripts/test-integration.sh`) runs
against the scratch Postgres described above, with a single event loop for the whole
session (`asyncio_default_fixture_loop_scope = "session"` in `pyproject.toml`) because the
suites share one database and one engine bound to whichever loop was running at import
time — the async-framework equivalent of the old Node app's `fileParallelism: false`.
`tests/integration/conftest.py` truncates every table between tests. Covers what only a
real database can show: FK cascade/`SET NULL` actions and `Date`/`bool`/float8
round-tripping (`test_schema.py`), the snapshot invariant and `create_run_record`'s
rollback (`test_run_create.py`), the advisory-lock claim (`test_run_lock.py`), the
executor end-to-end against the mock LLM (`test_executor.py`), cross-workspace isolation
including the versioning cases (`test_workspaces.py`, `test_versioning.py`), the
login/session/sign-up-closes flow (`test_auth_flow.py`), and every domain router's CRUD
(`test_*_api.py`).

Everything else is verified against the dev server + the mocks (`backend/app/api/mocks.py`,
gated by `mocks_enabled()` — dev, or `ENABLE_MOCKS=true` in production; the refusal is a
**404, not a 403**, so these routes should not appear to exist in production):

- **Mock LLM** — register a machine with base_url `http://localhost:8077/api/mock-llm`.
  `TRIGGER_ERROR` in the user message → 500, `TRIGGER_SLOW` → 2s TTFT delay,
  `TRIGGER_TOOL_LOOP` → never stops calling tools (exercises `max_turns`). When the
  request carries `tools` and no tool result yet, it streams a tool call for the first
  tool with arguments synthesized from its schema, split across chunks; once a tool
  result is present it answers in text quoting it.
- **Mock MCP** — an MCP toolset pointing at `http://localhost:8077/api/mock-mcp` serves
  `echo_upper` and `add_numbers`. `?hide=<tool>` drops one from `tools/list` (verifies
  discovery disables rather than deletes), `?fail=1` makes every call return `isError`.

## Deployment

Two-stage `Dockerfile`: `node:22-alpine` builds `frontend/` and only the compiled
`dist/` crosses into the backend image (no `node`/`npm` in the final image); the backend
stage (`ghcr.io/astral-sh/uv:python3.12-bookworm-slim`) installs from the committed
`backend/uv.lock` (`uv sync --frozen --no-dev`), copies in `app/`, `alembic/` and the
built SPA as `static/` — the same directory `app/main.py`'s SPA-fallback route already
checks for and no-ops on when absent, so dev behaviour (vite's own dev server) is
unaffected. `ENVIRONMENT=production` is baked into the image, which is what makes the
mock routes 404 unless `ENABLE_MOCKS=true` is also set. `docker-entrypoint.sh` refuses to
start without `DATABASE_URL`, then runs `alembic upgrade head` before handing off to
`uvicorn` — a broken migration stops the container rather than serving a half-migrated
database, since statements are applied verbatim.

`docker-compose.yml` bundles a `postgres:17-alpine` service (`--encoding=UTF8
--lc-collate=C --lc-ctype=C`, since prompt/test-case content can carry Unicode Tags) the
app waits for via `depends_on: condition: service_healthy`; state lives in the named
volume `pgdata`. `POSTGRES_PASSWORD` is required in `.env`; `DATABASE_URL` optionally
overrides the bundled database with an external one. `docker compose up -d --build`
serves the SPA + API + MCP on one port (`127.0.0.1:8000` by default — change the port
mapping or front it with a reverse proxy to expose it further).

The compose **service and container are named `modelfit`**, not `promptrack`, on
purpose: a live deployment's reverse proxy already points at `modelfit:<port>` from the
old app (see `CLAUDE.local.md`, gitignored, for that deployment's specifics), and keeping
the name means the rewrite can replace the old container without touching proxy config.
Everything else — the Postgres role/database (`promptrack`), the volume (`pgdata`), the
compose network (`promptrack`) — uses the new name, since none of it carries continuity
constraints from the old `agentval`/`amv_` naming (this is a fresh database with no data
to preserve, unlike the rename the old app went through in place).
