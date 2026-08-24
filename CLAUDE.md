# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Environment

Backend: Python 3.12+, dependencies and virtualenv managed by `uv` (`backend/pyproject.toml`).
Frontend: Node 22+, npm. Two stacks, two languages, one repo, and they share no
tooling, package manager, test runner or formatting rules — check which directory you're
in before assuming a convention from one carries over to the other.

Both are recent major versions whose API surface a training snapshot may get wrong:
SQLAlchemy 2.0's async API is not 1.x with `await` sprinkled on, Pydantic v2 is not v1
with new imports, and PrimeVue 4 / Vue 3's Composition API are not what an older doc
assumes. Read the neighboring code in the file you're editing before writing something
novel — it already establishes the pattern this codebase wants.

## Commands

```bash
docker compose -f docker/compose.dev.yml up -d           # postgres:17-alpine on 127.0.0.1:5433
cd backend && uv run alembic upgrade head                 # apply migrations
cd backend && uv run uvicorn app.main:app --reload --port 8077   # http://localhost:8077
cd frontend && npm install && npm run dev                  # http://localhost:5177, proxies /api

make run                             # db (waits for healthy) + migrations + both dev
                                     # servers via concurrently; ctrl-c stops both.
                                     # `make` alone lists every target

cd backend && uv run pytest                                 # pure suite, no database
cd backend && uv run pytest tests/test_llm.py                # single test file
cd backend && uv run pytest tests/integration                # throwaway postgres in docker, port 55432
cd backend && uv run ruff check .                            # lint

cd frontend && npm run build          # vue-tsc -b && vite build; catches type errors too
cd frontend && npm run typecheck      # vue-tsc -b --force, no bundle — see the note below

cd backend && uv run alembic revision --autogenerate -m "..."  # write a migration from model changes
```

`make check` runs lint + the pure suite + typecheck, which is everything a commit should
pass.

Everything reads `DATABASE_URL` (`backend/app/config.py`, `Settings`, pydantic-settings —
field names map case-insensitively to env vars). `docker/compose.dev.yml` brings up
Postgres on `127.0.0.1:5433`; `backend/tests/integration/conftest.py` provisions its own
throwaway Postgres on `55432` (docker, tmpfs data) unless `TEST_DATABASE_URL` is set, so
the integration suite never touches the dev database — it applies the committed migrations
to that database itself and tears the container down again once the suite finishes.
`.env.example` at the repo root documents every variable; every field has a working dev
default, so a fresh clone runs with no `.env` at all until you need to change something.
`DATABASE_POOL_MAX` (default 10) has to exceed the number of runs that can execute
concurrently plus normal request concurrency, because **an executing run holds one
connection for its whole duration**.

Migrations are committed under `backend/alembic/versions/`. `alembic revision
--autogenerate` compares the SQLAlchemy models to the database and writes a migration —
read it before committing: autogenerate does not reliably infer a *rename* (it will drop
and recreate a column instead), so a rename needs the generated file hand-edited into an
`op.alter_column`/`op.rename_table`. It renders `sa.Computed` and a GIN index no better,
which is why `0007_documents` is hand-written. Revision ids here are the sequential
`NNNN_slug` the filenames show, not alembic's default random hex, so pass `--rev-id
0008_whatever` — and whatever you write by hand, `uv run alembic check` is the confirmation
that the model and the migration actually agree ("No new upgrade operations detected"). It
answers that without writing a file, which also makes it the cheaper of the two.

Git: default branch is `main`. One remote: `origin` is GitHub
(`webix-solutions-GmbH/PromptRack`, private; note the capitalisation — the lowercase URL
only resolves through GitHub's redirect).

## What this is

**PromptRack** answers two questions for a consultancy that sells AI solutions to
businesses: **which model is good enough for this customer's actual job**, and **what
hardware that takes**. Not a leaderboard score — the customer's real work, loaded in as
test suites: an invoice-processing agent, document and data extraction, structured
extraction from business correspondence, MCP tool calls against the company's own RAG. The
suites are the specification of the job, and the app's answer is a fitness verdict per
model on *those* test cases, never a general ranking.

The second question is sizing, and it is why every result names the **endpoint** that
produced it (a base URL plus free-text hardware notes): if a small model does the job, a
DGX Spark or even a Mac Mini is enough — but that has to be measured, and
TTFT/duration/tok-s per endpoint is the evidence. Endpoints are anything
OpenAI-compatible, so this is **not local-only**: Ollama / LM Studio / vLLM on your own
boxes *and* hosted frontier APIs, side by side in one matrix. The common outcome is a
**mixed deployment** — most workloads self-hosted, the hard ones routed to a frontier
model — and the app exists to find where that line falls. Workspaces are per **customer
engagement**, which is the whole reason they exist: one engagement's prompts and runs stay
out of another's. Endpoints and toolsets are the deliberate exception — they are the two
things that hold credentials rather than an engagement's own work product, so they can be
registered once, in a shared workspace named "Base", and read into every engagement
instead of duplicated (and left stale) per customer; see "Customer workspaces" below.

Mechanically, multi-user and multi-workspace: author test cases (grouped, optionally with
expected output — the rubric), run them sequentially against an endpoint,
measure TTFT/duration/tokens/tok-s, rate results good/meh/bad manually, compare in a
matrix by model or by run. A test case can also be a **tool test**: offer the model a set
of functions and either record what it wanted to call, or really execute the calls
through an MCP server and loop until it answers — which is how an invoice agent or a
RAG-backed assistant gets evaluated as the agent it will actually be, not as a chat
completion. One of those tool sets is a **markdown corpus** the model searches and reads
through three real functions, which is how "answers questions from the customer's own
documentation" becomes a measurable workload rather than a claim: what is being measured
there is retrieval *behaviour* — whether the model searches well, opens the right document,
answers from what it read and recovers from a path it invented. See "Document corpora"
below.

**PromptRack is also "git for your customers' prompts."** The system prompt behind an
agentic tool is a versioned asset, not a text field on a test case: a mutable draft, an
explicit commit that freezes an immutable version with a message, a `deployed` pointer
(a human's bookkeeping claim about what runs at the customer today) and a `baseline` run
pointer per version (the known-good measurement that justified deploying it). A model
swap's regression check is then: open the baseline version's Verify link, run the same
test cases against the new model, and compare against the baseline run in `/results`.
See "Prompt versioning" below.

## Architecture

- **Backend**: FastAPI + SQLAlchemy 2.0 (async, `asyncpg`) + Alembic + Pydantic v2, on
  Postgres. `backend/app/db.py` exports the async engine, the `async_session` factory and
  the `get_session` FastAPI dependency (`DbSession` in `app/auth/guards.py`).
  `backend/app/models/` is the single source of truth for the schema (one module per
  domain area: `customers`, `endpoints`, `prompts`, `test_cases`, `toolsets`, `runs`,
  `auth`); `alembic revision --autogenerate` writes migrations from it.
  - Enum-ish columns are `Text` + a Python `Literal`, not a Postgres enum: adding a
    rating, status or kind value needs no migration.
  - `tokens_per_sec` is `Double` (float8) — a 4-byte float would round every value.
- **Frontend**: Vue 3 + Vite + PrimeVue 4 + Pinia, an SPA against the FastAPI API.
  `frontend/src/api/client.ts` is the thin `fetch` wrapper every `src/api/*.ts` module
  uses; it throws `ApiError { status, message }` from the envelope
  `app/main.py`'s exception handlers write (`{"message": ...}` on every error, so a
  guard's 403 and a validation 422 read the same to the client). `vite.config.ts` proxies
  `/api` to `http://localhost:8077` in dev.
- **Creation is a dialog or a page, and which one is deliberate.** A `Dialog` (the shared
  `.form-dialog` + `.dialog-form` markup) is right where the useful minimum is two or
  three fields and the full editor is a page reached afterwards — a prompt, a toolset, an
  endpoint, a test group. A full page is right where there is no meaningful minimal form:
  a test case needs a group, a title, content, both prompt slots, a tool mode, toolsets
  and a rubric, so a dialog would be a speed bump in front of the same page.
- **Auth is session-cookie-based**, checked by FastAPI dependencies
  (`app/auth/guards.py`): `CurrentUser`, `Writer` (`require_writer`), `Admin`
  (`require_admin`). There is no client-side route "protection" beyond a
  `router.beforeEach` guard in `frontend/src/router/index.ts` that keeps the SPA from
  rendering a page it cannot use — the API enforces the real boundary.
- **Where a preference lives is a decision, not a default.** The active workspace is a
  column on the user row (`users.active_customer_id`) because it must be unforgeable and
  survive a session refresh; the dark/light theme is `localStorage`
  (`frontend/src/stores/theme.ts`) because it is a per-*device* preference and the same
  person can reasonably want dark at home and light at work. Its `STORAGE_KEY` must stay
  byte-identical to the key the inline script in `frontend/index.html` reads, which sets
  the class before the bundle loads to avoid a flash of the wrong theme.
- **MCP is mounted in the same process**, not a separate service: `POST /mcp`
  (`backend/app/mcp/server.py`, the official `mcp` Python SDK, FastMCP, streamable HTTP,
  stateless). See "This app as an MCP server" below.

### Snapshot model (the core invariant)

Editing or deleting test cases, prompts, endpoints, or toolsets must never change how a
past run displays. `create_run_record` (`backend/app/services/run_create.py`) freezes
everything into `run_results` rows at creation time: title, group name, expected output,
`tools_snapshot`, **three texts** — `system_prompt_text`, `task_prompt_text` and
`test_case_text` (the case's own content) — and **two version ids**,
`system_prompt_version_id` / `task_prompt_version_id`, one per slot, each the version that
slot's draft happened to be byte-identical to, if any (see "Prompt versioning" below).
`test_case_id`/`endpoint_id` FKs are kept (`SET NULL` on delete) only for cross-run
comparison; rendering always uses the snapshots.

The three texts are frozen **separately and unassembled** — the executor concatenates them
into the two messages at execution time (see "Prompt kinds and message assembly"). Storing
the assembled user message instead would be smaller and would destroy the whole point: only
separate parts let `/results` say *the task prompt changed* rather than *the user message
changed*, which is the distinction this app exists to draw. It also means `transcript_json`,
which records the **assembled** strings, is never the thing a detail view reads — the three
columns are.

Validation (test-group ids, tool config, tool-name collisions) and the endpoint probe
happen *outside* `create_run_record`'s transaction, in that order: validation throws
before anything is written, and the probe is a network call that must never hold a
transaction open. Only the three writes — the run row, all of its `run_results` in one
multi-row insert, the `endpoint_models` upsert — are one unit
(`app.repos.scoped.transaction`, a `SAVEPOINT` if the caller is already inside one), so a
crash between them cannot leave a run with no test cases in it, which Resume would report
as finished.

The line between frozen and live is **content vs. credentials**: test-case text, tool
definitions and a manual tool's canned response travel with the run; an endpoint's
`base_url`/`api_key` and a toolset's `mcp_url`/headers are read live at execution time so
a moved endpoint doesn't break Resume.

A `documents` toolset's markdown corpus is the one piece of *content* that is read live, and
that is a v1 decision rather than an oversight — the same one an MCP toolset already forces,
since an MCP server's answers were never freezable either. Its tool *definitions* are frozen
like any others; the markdown is not, so a re-run after editing the documentation retrieves
from the edited corpus, which is exactly the measurement wanted when the documentation is
what changed. A frozen run's `response_text` and metrics are untouched by such an edit, so
the invariant above still holds — what is missing is drift *detection*, and the crumb for a
later version to light it up is in the snapshot already (`document_count`,
`corpus_updated_at`). See "Document corpora" below.

### Prompt versioning

`prompts.content` is the mutable **draft** — what the editor writes to on every save, no
version created. **Every prompt text in the app is one of these rows**: a test case holds
no prompt text of its own (see "Prompt kinds and message assembly"), so every instruction
a suite sends is a named, versioned asset rather than an anonymous free-text field.
`prompt_versions` is the immutable history: a child of `prompts`
(`prompt_id` **CASCADE** — history dies with the asset, but every past run keeps its own
snapshot regardless), never edited or deleted individually, `version` sequential per
prompt (`max + 1`, computed inside the commit transaction; a unique index on
`(prompt_id, version)` is the backstop).

`prompts.kind` (`Text` + `Literal["system","task"]`, `server_default "system"`) says which
channel the asset is sent on, and therefore which of a test case's two slots may hold it.
Kind is a property of the **asset**, not of a reference to it, because "v3 is deployed" has
to be able to say *deployed as what* — the prompt has a role in the customer's system
independent of PromptRack's test cases. `backend/app/repos/prompts.py` owns the two rules
that follow from that, both inside repository functions so no call site can forget them:

- **`assert_prompt_slot(scope, session, prompt_id, kind)`** does same-workspace and
  right-kind in **one** read, and returns the row (which is what lets the caller check
  "does this case have a user message at all" without a second query). A `None` id is a
  valid empty slot, so the call sites carry no `if`. The two refusals are deliberately
  distinct: a prompt from another workspace is `CrossCustomerError` → 404, a prompt of the
  wrong kind is `PromptSlotError` → 400, because "no longer exists in this workspace" would
  be a lie about a row sitting right there.
- **Changing a prompt's `kind` is refused while any test case references it**
  (`PromptKindChangeError` → 409, raised inside the shared `update_prompt` patcher before
  the UPDATE, so a refused request writes nothing at all). The alternative — silently
  relocating that text from the system message to the head of the user message for every
  case that uses it — is exactly the invisible wire-format change kinds exist to prevent.
  Unreferenced, the kind changes freely.

Versioning machinery is indifferent to kind: commit, restore, diff, deployed and baseline
work identically for both.

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
  run's results are actually **attributed** to this version — an OR across the two version
  columns, which is not a loosening: a prompt has exactly one kind, so its versions can only
  ever appear in the column that kind names, and checking either is checking the right one
  with no branch to get wrong.
- **Attribution, not selection.** `run_results.system_prompt_version_id` and
  `task_prompt_version_id` (both `SET NULL`) are set at run creation *only* when that
  slot's draft text is byte-equal to a committed version (a "clean working tree"); `None`
  means that slot tested a dirty draft or holds no prompt. The two are independent — a run
  can be attributed on its task prompt and dirty on its system prompt. There is no version
  picker at run creation — a run always tests the current drafts; the columns are
  attribution, computed after the fact by `match_version(draft_text, versions)` inside the
  existing scoped read, matching **newest first** so a revert (a new commit whose content
  equals an older version) attributes to the new commit, not the old one.
- **Attribution is exact.** The text sent *is* `prompt.content` verbatim, so
  `match_version` compares against what went on the wire. There is no derived prompt text
  anywhere for a version id to lie about, and nothing may reintroduce one.
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
directly outside `backend/app/db.py`, `app/services/run_lock.py` (needs the engine itself
for a Postgres advisory lock, on its own connection — see "Run execution pipeline"), and
`app/auth/sessions.py` / `tokens.py` / `users.py` / `invites.py`, which own the auth tables
a `Scope` is derived *from* and so cannot themselves be read through a scoped repository.
Every other query goes through a repository function in `backend/app/repos/*` whose
functions all take a `Scope` (`backend/app/scope.py`) as their first argument.

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

**`visible_where` is `scope_where`'s read-only twin**, because endpoints and toolsets being
shareable makes "what may I see" and "what may I write to" two different questions.
`scope_where` is exactly the ownership predicate — every `UPDATE`, `DELETE` and
`scope_values` insert asks it — and `visible_where` ORs in `is_global` on top of it, but
**only** for `Endpoint` and `Toolset` (the `_SHAREABLE` map in `app/scope.py`); for every
other root table it is `scope_where` verbatim. `scope_through_parent` takes the same opt-in
via a `visible: bool = False` keyword, so a global endpoint's `endpoint_models` and a global
toolset's `tools` come along with a parent a workspace can see but does not own — and a
*write* must never pass it, since a shared parent's children are still only editable where
the parent lives. `where_visible` is `where_scoped`'s counterpart, spelled as its own
function rather than a flag so a call site states which of the two questions it is asking
and the shared-row surface stays a grep away (`where_visible(` across `backend/app/repos/`).

**The failure direction is deliberate and load-bearing: forgetting to opt into
`visible_where` must only ever cost a feature, never leak a workspace.** A read path that
stays on `scope_where` simply doesn't show a shared endpoint in a picker — reported as a
missing feature. The alternative design, a permissive default that writes opt out of, would
turn that identical omission into cross-workspace disclosure instead, which is why
`visible_where` is opt-in everywhere and will never become the default — the same reasoning
that keeps `system_scope` a grep-able, explicit call rather than an implicit state.

A system scope still gets `None` from `visible_where`: "every workspace" already includes
the global rows, and narrowing it there would make the escape hatch see less than an
ordinary scope does.

`app.models` stays importable everywhere — API response models legitimately reference ORM
types. Only the session/engine handle is restricted.

### Customer workspaces

A workspace (`customers`) is a **label, not a tenant**: customers never log in, and every
signed-in user can switch into any of them. It is what keeps one engagement's
endpoints — i.e. base URLs with API keys — from mixing with another's.

- The five root tables (`endpoints`, `prompts`, `toolsets`, `test_groups`, `runs`) carry
  `customer_id NOT NULL`. The child tables (`endpoint_models`, `tools`, `documents`,
  `test_cases`, `test_case_toolsets`, `run_results`, `prompt_versions`) carry **nothing**:
  they inherit scope through their parent FK. Cross-root references can only be checked in app
  code — a test case's group, a test case's toolsets, a run's endpoint, a prompt's
  `deployed_version_id`, a version's `baseline_run_id` — via `assert_same_customer`
  (`backend/app/repos/customers.py`), called from inside the repository functions so no
  call site can forget it. A test case's prompt reference is **two** of them, one per
  slot (`system_prompt_id`, `task_prompt_id`), and both go through `assert_prompt_slot`
  (`backend/app/repos/prompts.py`) instead: the workspace check and the kind check are true
  of the same row, so they are one read and one refusal path rather than two that can drift.
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
- **Known limitation**: a deep link into a row another workspace owns (`/runs/42` while
  switched to a different customer) surfaces whatever the scoped 404 says. Nothing in
  `frontend/src/views` renders it as a "switch workspace to see this" notice.
- **MCP scope precedence**: `customer` argument → `X-Customer` header → the token's
  default → refusal naming both and listing the workspaces. Id-addressed calls (a run or
  result id — `get_run`, `get_run_result`, `set_rating`, `execute_run`) are the exception:
  the id is globally unique, so the row resolves its own workspace, and a named workspace
  that contradicts it is refused naming the actual one. See "This app as an MCP server"
  below.

### The Base workspace and global endpoints/toolsets

`customers.is_base` marks exactly one workspace, named "Base", as the one that may own
**global** rows — `endpoints.is_global` / `toolsets.is_global`, settable only there
(`assert_base_workspace`, called from inside `create_endpoint`/`update_endpoint` and
`create_toolset`/`update_toolset` so no route or MCP tool can forget it). **Only endpoints
and toolsets are shareable**, and that is a hard line, not a starting point for more: they
are exactly the two tables that hold credentials — a base URL plus an API key, an MCP URL
plus headers — so a consultancy registers its one DGX Spark and its handful of mock
toolsets once and reuses them across every engagement instead of re-registering the
credential per customer and guaranteeing that half the copies go stale. Prompts, test
groups, test cases and runs are never shareable: they are the engagement's own work
product, and keeping one customer's suite out of another's is the whole reason a
workspace exists.

A shared toolset brings **both** of its children with it, `tools` and `documents` alike,
since neither carries a `customer_id` of its own — which is what lets a shared reference
corpus (a product manual, a compliance handbook every engagement asks the same questions
of) be registered once in Base and retrieved from everywhere, while remaining editable only
there.

Sharing costs **no new permission layer** — it is a consequence of the read/write split
`visible_where` draws (see "Data access" above). A global endpoint or toolset is visible to
every workspace's read paths and selectable on a run or a test case from any of them, but
`UPDATE`/`DELETE` still ask `scope_where`, which only Base satisfies — so the repository
layer's refusal needs no role check, only the strict predicate already in place; the
`app/api/endpoints.py` and `app/api/toolsets.py` routes add one explicit check on top of
that (`_refuse_if_borrowed`, a 403) purely so a write against a borrowed row reads as a
named refusal rather than the silent no-op `scope_where` alone would produce.

`assert_same_customer` learns the same distinction through one keyword:
**`allow_global: bool = False`**, widening its check from ownership to `visible_where` when
passed. Every call site that passes it is a reference that may legitimately name a row
another workspace owns, and the list is short and greppable (`allow_global=True` across
`backend/app/`):

- `create_run`'s endpoint reference (`app/repos/runs.py`),
- a test case's toolset links (`replace_toolset_links`, `app/repos/test_cases.py`) and the
  validating half of the same reference (`assert_tool_config`,
  `app/services/tool_config.py`) — a stricter check in one would refuse at authoring time
  exactly what the other allows,
- the two `endpoint_models` sighting writes (`touch_endpoint_model` and
  `sync_discovered_models`, `app/repos/endpoints.py`) — a run recording its own model
  sighting, and the new-run page's page-load probe, both of which would otherwise make a
  shared endpoint unusable.

Every other call site keeps the default and keeps refusing globals. None of this touches
the snapshot invariant: a run's `endpoint_snapshot` and a result's `tools_snapshot` are
copies, so a global row appearing in a past run is already immune to Base editing it later.

Two hazards worth knowing about rather than being surprised by:

- **Deleting a global toolset is guarded, not cascaded.** `test_case_toolsets.toolset_id`
  is `ON DELETE CASCADE`, which is correct while a toolset and its test cases live in one
  workspace and destructive the moment they don't — an ungated delete would silently strip a
  shared toolset from every engagement's test cases. `_assert_not_borrowed_elsewhere`
  (`backend/app/repos/toolsets.py`) refuses and names the damage (which workspaces, how many
  test cases in each); both `delete_toolset` and `update_toolset` (before *un-sharing*) ask
  it, since they are two doors onto the same cascade.
- **`endpoint_models` on a global endpoint accumulates rows from every engagement that ran
  against it.** This is intended, not a leak: shared hardware has one shared history, and
  "this box has already served qwen3:32b" is exactly what the next engagement needs to know.

Base is not a privileged workspace in any other sense — it holds ordinary groups, prompts
and test cases too, and any user switches into it the normal way to author a global row,
since there is no separate admin surface for them. It is, however, **refused for both
deletion and archiving** (`app.api.customers`, a 409 regardless of role): archiving it would
hide the only place the shared rows can be edited, and every scope has to resolve to a
workspace.

Migration `0003_base_workspace_and_globals` **creates or adopts, never blindly inserts**:
`customers_name_lower_idx` is unique on `lower(name)`, so on an install that already has a
workspace called "Base" — matched case-insensitively, the same way an MCP caller resolves a
workspace by name — it flags that one and leaves everything it owns untouched, rather than
failing on a duplicate insert. Its id is whatever the data holds, never an assumed 1, and
it is not necessarily empty, which is why nothing in the app may assume Base is
infrastructure-only.

### Auth

`backend/app/auth/`: argon2id password hashing (`passwords.py`), DB-backed sessions
(`sessions.py` — random 32-byte token, SHA-256 stored, `HttpOnly` cookie, sliding expiry),
role semantics (`policy.py`: `can_write`, `can_administer`, `parse_role` — degrades any
unrecognised value to `viewer`, never to admin), and the FastAPI dependency guards
(`guards.py`: `CurrentUser`, `Writer`, `Admin`, `CurrentScope` — see above).

- **Roles**: `admin` / `member` / `viewer`, all semantics in `policy.py`'s two pure
  predicates, with the vocabulary derived from `app.models.auth.UserRole`'s `Literal` so a
  role added to the column cannot be missing from `ROLES`. Content vs. credentials is the
  line: toolset create/update/delete is admin (it holds `mcp_url` + headers), the tools
  *inside* it are member; endpoints are admin, `POST /api/endpoints/{id}/discover` is member
  (`/runs/new` posts it on page load for everyone), `POST /{id}/test` and
  `POST /endpoints/test-connection` stay admin (they exercise a stored or submitted API key).
- **First account is the administrator, then sign-up closes forever** — `app/auth/router.py`
  refuses `POST /api/auth/sign-up` once the `users` table is non-empty, and takes a lock so
  two simultaneous bootstrap sign-ups cannot both be stamped `admin`.
- **Invites are the way in afterwards** (`app/auth/invites.py`, `app/api/invites.py`, all
  `Admin`): a single-use link, structurally a sibling of `tokens.py` — 32 random bytes
  prefixed `pri_`, stored as SHA-256, a 12-char display prefix, shown exactly once. An
  invite holds **no email**; it names a role, and whoever opens the link first supplies
  their own address. Both user FKs are `SET NULL` so the audit row survives deleting the
  admin who sent it or the account that redeemed it.
- **Deactivation is `users.disabled_at`**, nullable with no default — its presence *is* the
  deactivation, with no boolean beside it to drift from it. Deliberately not `deleted_at`,
  because deleting a user here is a real `DELETE`. `app/api/users.py` (all `Admin`) is the
  Users page's surface: list, set role, deactivate, reactivate, delete. Two pure guards in
  `policy.py` sit in front of the destructive three: `is_self` (a 409 — an admin cannot
  demote, deactivate or delete their own account here) and `would_remove_last_admin`, one
  rule for all three, so an install can never lock itself out of having an administrator
  who can sign in.
- **API tokens** (`backend/app/auth/tokens.py`, `backend/app/api/tokens.py`): 32 random
  bytes prefixed `prk_`, stored as SHA-256, shown exactly once, a 12-char display prefix.
  A token acts as its owner and carries their role — `presented_token`
  (`app/auth/guards.py`) reads `x-api-key` **before** `Authorization: Bearer`, so a
  reverse-proxy basic-auth credential and an MCP client's token both fit in one request.
  There is deliberately no customer column on `api_tokens`: a call names its own workspace.
  Ownership is baked into every query; there is no admin override on another user's tokens.
- **OIDC is optional and generic** (`backend/app/auth/oidc.py`, Authlib): without
  `OIDC_ISSUER` **and** `OIDC_CLIENT_ID` the module mounts no `/api/auth/oidc/*` routes at
  all rather than 404ing per-route. One provider at a time, not a menu.
  `OIDC_DEFAULT_ROLE` (default `member`) is read through `parse_role`, never trusted
  verbatim. Authlib needs `request.session` to carry `state`/`nonce` across the provider
  round trip, so `app.main` adds Starlette's `SessionMiddleware` — only when OIDC is
  configured, and unrelated to `app.auth.sessions`.
- **Frontend**: `frontend/src/stores/auth.ts` (Pinia) holds `user`, `canWrite`,
  `canAdminister`, `setupRequired`; `router.beforeEach` in `frontend/src/router/index.ts`
  is the single enforcement point on the client (optimistic only — the API is the real
  boundary), redirecting to `/setup` when no user exists yet and `/login` otherwise.
  Controls a role cannot use are hidden by passing those booleans into components, never
  rendered-then-disabled.

### Prompt kinds and message assembly

**A test case holds no prompt text.** It references up to two prompt assets — one per
`kind` — and keeps only what makes it a *case*: `content` (the data that varies, nullable)
and `expected_output` (the rubric, never sent to the model).

- `system_prompt_id` → a `kind: "system"` prompt, sent as the **system message**.
- `task_prompt_id` → a `kind: "task"` prompt, sent at the **head of the user message**,
  ahead of `content`.

Two slots exist because real pipelines use all three shapes and one slot expresses only
one of them: an invoice agent's PO judge is a single instruction with no system prompt;
other calls have a framing system prompt *plus* a per-call task prompt; some have only the
framing. Anything a test case could splice in on its own would be a prompt with no name, no
version history, no deploy pointer and no diff, in an app whose thesis is "git for your
customers' prompts" — so there is no such field, and none may be added.

Assembly is `backend/app/services/message_assembly.py`, pure and database-free (the same
split `diff.py` and `attribution.py` draw — resolving an id into text is a scoped read, the
caller's job):

- `system_message(text)` → the text, or `None`. Whitespace-only counts as absent, and a
  blank system prompt means **no system message at all** rather than an empty one: several
  providers treat an empty system role as a real, differently-behaving turn.
- `user_message(task_text, content)` → `task + "\n\n" + content` when both are present,
  otherwise whichever is. **Concatenation, not templating** — no `{{variables}}`, no
  placeholder token, no template engine; the data lands at the end, which is where these
  pipelines put it.
- `assert_user_message(...)` refuses a case that would send nothing, expressed as "would
  `user_message` produce anything" so the rule and the assembly cannot disagree about what
  blank means. It is called at authoring time (inside `app/repos/test_cases.py`, on create
  and on the **merged post-patch** state) and again at run creation, exactly the way
  `assert_tool_config` is — so a case saved through the API or over MCP can never be one a
  run would later refuse. The executor checks a third time immediately before dispatch,
  because `delete_prompt` can `SET NULL` a slot on cases that were already valid; a row
  emptied that way is marked `error` with a readable message instead of the provider
  answering 400.

Assembly happens at **execution** time from the frozen columns, not at run creation — see
"Snapshot model" for why the parts stay separate. There is no server-side preview endpoint:
the editor has already fetched both prompts' text, so the preview is a client-side concat.

### Run execution pipeline

`backend/app/services/llm.py` (raw-`httpx` SSE client, no vendor SDK) →
`backend/app/services/tool_loop.py` (one to N turns) →
`backend/app/services/executor.py` (sequential loop over rows) →
`POST /api/runs/{id}/execute` (NDJSON, one event per line — the dataclasses in
`backend/app/services/run_events.py`) → the frontend's run-detail view drives it live.

- `llm.py` parses SSE tolerant of provider differences (usage in a final empty-choices
  chunk vs. on the last content chunk; chunks split across reads, including mid-JSON tool
  call fragments keyed by `index`/`id`/nothing). No usage received → estimate
  `ceil(chars/4)` over text **plus** serialized tool calls, `tokens_estimated=True` (UI
  shows `~`), reasoning text included in the character count. TTFT = the first output of
  **any** kind — a reasoning delta, a content delta, or a tool-call fragment — because a
  tool-call-only response streams no content and a thinking model streams no *visible*
  content for seconds. Only connection-level failures raise `LlmError`.
- **Reasoning models are measured on both clocks** (`0008_reasoning_metrics`). A chain of
  thought arrives one of two ways and the same model on two endpoints picks differently:
  inline in `content` wrapped in `<think>` tags (Ollama, vLLM without a reasoning parser),
  or split onto `delta.reasoning_content` (vLLM with `--reasoning-parser`, DeepSeek). The
  split shape used to be dropped on the floor, which cost three things at once — the
  thinking itself, the head of every answer, and the throughput number:
  - `run_results.reasoning_text` stores it (the final turn's; every turn's is in
    `transcript_json`), and `reasoning_tokens` breaks out
    `usage.completion_tokens_details.reasoning_tokens` — **part of `completion_tokens`,
    never additional to it**, which is what makes a 27-character answer costing 479 tokens
    visible instead of merely confusing. `lib/thinking.ts`'s `resolveThinking` folds both
    provider shapes into one answer/thinking pair so no view has to know which it got.
  - **The answer is stored with leading whitespace stripped.** A reasoning parser's
    `content` begins after `</think>`, which the chat template follows with a newline pair;
    several rubrics demand raw JSON with no fences, and `JSON.parse` tolerates the pair
    while `startswith("{")` and an exact-match diff do not.
  - `ttft_ms` counts reasoning; `ttft_content_ms` is the older reading — time to the first
    *visible* token, a real latency for a thinking model and a useless throughput
    denominator. Both are kept precisely because they answer different questions.
- **Executor invariants** (`app/services/executor.py`, `app/services/run_lock.py`): one
  execution per run via a Postgres **advisory lock** (`pg_try_advisory_lock`, namespaced
  under `LOCK_CLASS`, taken on its own connection in `AUTOCOMMIT` and held for the whole
  run — it dies with the connection, so a crashed process releases it, while more than one
  app process is still safe; a lock *table* would have needed expiry and heartbeats for the
  same crash semantics). `is_run_executing` reads `pg_locks` rather than taking the lock,
  so asking never accidentally answers. Every result row is persisted the moment it
  finishes; a row error marks it `error` and the loop continues; disconnect (an explicit
  `asyncio.Event`, not task cancellation — a cancelled scope cannot safely `await` the
  row-reset write) resets the in-flight row to `pending`; rows stuck `running` from a
  crashed process are reclaimed to `pending` at the next execution's start (safe precisely
  because the lock is already held by then). Run status `failed` is reserved for "every
  attempted result died at connection level"; partial errors still end `completed`.
- Execution runs as a **detached background task** (`run_in_background`), not tied to the
  request's own task group: Starlette cancels a streaming response's task group on client
  disconnect, and inside a cancelled scope every further `await` raises immediately — which
  is exactly when the executor still has to write the in-flight row back to `pending`. The
  route sets the cancellation flag and the executor notices it. Resume picks up remaining
  `pending` rows by calling execute again.
- `tokens_per_sec = completion_tokens / ((duration_ms - ttft_ms) / 1000)` — rate over the
  generation window, not total duration. For a multi-turn tool run the denominator is the
  **sum of each turn's own** generation window (`aggregate` in `tool_loop.py`), so later
  prefills aren't counted as generation; for a single turn it reduces to exactly the
  formula above. **Which is why `ttft_ms` has to count thinking**: with it measured at the
  first visible token instead, a reasoning model's whole chain of thought fell inside the
  excluded prefill while the numerator still counted every reasoning token, and one row of
  run 7 stored 3958 tok/s against a real ~65. A **plausibility guard** backstops the next
  provider whose thinking channel nobody here has seen yet — a five-digit rate out of a
  sub-quarter-second window is a mismeasured prefill, so the rate is `None` ("not
  measured") rather than fiction. Both halves of the condition are needed: a fast short
  answer has a tiny window at an ordinary rate, a batched server a high rate over a long
  window. `frontend/src/lib/format.ts` carries the same math for per-turn chips and has to
  stay in step — a chip disagreeing with its own row's rate is worse than either alone.
- **Nothing was backfilled.** Rows measured before `0008_reasoning_metrics` hold a `ttft_ms`
  from the old reading, so their generation window is unrecoverable and `tokens_per_sec`
  could only be guessed at — and overwriting a stored measurement with a number nobody
  measured is exactly what the snapshot model exists to prevent. Runs against a thinking
  model that predate the fix have to be **re-run**, not repaired.

### Tool / API calling

A test case has a `tool_mode`: `none` (classic one-shot), `definitions` (offer the tools,
record the calls, execute nothing), or `execute` (run each call, feed the result back,
loop to `max_turns`). It selects **any number** of toolsets — duplicate tool names across
selected toolsets are refused, both in the test-case editor and again in run creation, by
the one shared function `assert_tool_config`
(`backend/app/services/tool_config.py`).

- **Toolsets** come in three kinds. `manual` (tools authored in the UI, answering with
  `mock_response` verbatim — what keeps a multi-turn test deterministic), `mcp` (tools
  discovered from a streamable-HTTP MCP server and really executed against it,
  `backend/app/services/mcp_client.py`, the official `mcp` SDK, connections opened
  per-operation, never pooled), and `documents` (a markdown corpus the model retrieves
  from — see below). `tools` rows follow the `endpoint_models` rule: discovery upserts and
  **never deletes** — a tool absent from `tools/list` only flips `enabled` false.
- **A tool failure is never a failed row.** The error text is serialized back to the
  model as that tool's output — what a real agent sees, and itself worth measuring. Only
  connection-level `LlmError`s can fail a row.
- The loop stops **before** executing calls it has no turn budget left to use, so a real
  ERP never gets hit for results that could not reach the model. `stopped_reason` is
  `stop` / `definitions_only` / `max_turns`.
- Metric columns mean what they do for a one-shot run — `response_text` = final assistant
  text, `ttft_ms` = first turn's TTFT, `duration_ms`/token columns = sums over model turns
  only (tool wait time excluded, and living per call in the transcript). Tool detail is
  *added alongside* in `transcript_json` / `turns_json` / `turn_count` / `tool_call_count`,
  all null when `tool_mode = "none"`.

### Document corpora (the `documents` toolset kind)

"The agent answers from the customer's own documentation" is a workload a consultancy sells
and no other test-case shape can reach: a canned `mock_response` measures whether a model
*calls* a tool, and a corpus measures whether it **retrieves** — does it search well, open
the right document, answer from what it read, and recover from a path it invented. That is
the whole reason this kind exists, and it is why the corpus is markdown rather than a
key/value store: the customer's handbook already is markdown, in sections a reader opens
separately.

- **`documents` is a second child table of `toolsets`**, sitting beside `tools`
  (`backend/app/models/toolsets.py`, migration `0007_documents`): `toolset_id` CASCADE,
  `title`, `path`, `content`, a generated `content_tsv`, `UNIQUE (toolset_id, path)` and a
  GIN index. **No `customer_id`**, like `tools` and `endpoint_models` — scope is inherited
  through the parent and expressed once in `scope_through_parent` (`visible=True` on a read,
  never on a write). That is not just consistency: a second, independently-writable answer
  to "whose corpus is this" would break sharing, because a global toolset borrowed by
  another engagement has to bring its documents with it. `app/scope.py`'s `_SHAREABLE` map
  therefore needs no entry — only root tables appear there.
- **The three tools are real `tools` rows, synthesized rather than authored or discovered**:
  `list_documents`, `search_documents(query, limit)` and `read_document(path, offset,
  limit)`, `source: "documents"`, `mock_response` NULL, definitions fixed in
  `backend/app/services/documents.py` (`DOCUMENT_TOOLS`). Real rows are the point —
  `assert_tool_config`'s collision and `enabled` checks, `tools_snapshot`, the toolset
  detail UI and `run_create`'s definition builder all work on them untouched, with nothing
  learning a new case. `sync_document_tools` (`backend/app/repos/toolsets.py`, called from
  inside `create_toolset`/`update_toolset` so no route or MCP tool can forget it, and
  re-assertable through `POST /{id}/documents/sync` the way MCP tools are re-read through
  `/discover`) is idempotent and **never touches `enabled` on a row that already exists**:
  disabling `search_documents` to see whether a small model can navigate by list-and-read
  alone is one of the measurements, and a helpful re-enable would silently destroy it. For
  the same reason nothing disables the three rows when a toolset is converted back to
  `manual` — the flag belongs to the human. Hand-authoring a *fourth* tool on a documents
  toolset is refused (`_refuse_hand_authored_tool`, a 400), since the executor routes on
  `source` and a `manual` row there would be offered to the model as a canned-response tool
  with no corpus behind it.
- **Search is Postgres FTS under the `'simple'` configuration, never `'english'`.** These
  are a consultancy's customer documents and they are frequently German; English stemming
  over German text degrades retrieval in a way that surfaces in `/results` as a *model*
  failure, which is the one misattribution this app exists to prevent. `simple` folds case
  and stems nothing, which is the honest default across a mixed-language corpus — and the
  cost worth knowing before authoring one is that it does not stem at all, so "Rückgaben"
  does not match a query for "Rückgabe". Two mechanical traps: a **generated column needs an
  IMMUTABLE expression**, so it must be the two-argument `to_tsvector('simple', …)` (the
  one-argument form reads `default_text_search_config` at call time and Postgres rejects it
  outright); and every query has to name the same configuration, cast as
  `cast(DOCUMENT_SEARCH_CONFIG, REGCONFIG)` — a bare bound parameter arrives as `text` and
  leaves no matching `websearch_to_tsquery`/`ts_headline` overload at all. A hit is ranked
  with `ts_rank`, snippetted with `ts_headline`, and ordered `rank DESC, path ASC` so a
  measurement does not reshuffle between runs.
- **A snippet is reported under its nearest preceding heading**, because "somewhere in
  refunds.md" tells the model nothing while "under *## Refunds after 30 days*" tells it
  whether to open the document at all. `heading_for_snippet` resolves it from the **first
  highlighted word**, not from the fragment's start: `ts_headline`'s `MinWords` pads a
  fragment backwards until it is long enough, so a match near the top of a section routinely
  arrives inside a fragment that *begins* in the previous one. Read from the fragment's
  start, such a hit is cited under the wrong section — and a wrong citation reads in
  `/results` as the model misquoting the docs, which is worse than no citation at all.
  `nearest_heading` tracks code fences for the same reason: a shell transcript full of
  `# install the client` must not become the heading of everything below it.
- **Execution routes on the frozen `source`, not on the tool's name.**
  `_build_tool_executor` (`backend/app/services/executor.py`) is a dispatcher over two
  closures — the MCP one unchanged, plus a documents one — and `list_documents` is a name a
  manual toolset is free to use for something else, so only the frozen entry knows which it
  was. `tool_loop.py` stays database-free: the documents closure lives in the executor and
  reads through `app/repos/documents.py` on the session the executor already holds for the
  run's duration. **The corpus is fixed by the frozen `toolset_id` plus the run's `Scope`**,
  so the model's `path` argument can only select *within* that corpus — the scoping is a
  `WHERE` clause, not a sanitizer, and since nothing opens a file there is no traversal
  surface to defend.
- **A bad path, a missing document or an empty result set is a tool result, never a failed
  row** — the app's standing rule, and nothing in `app/services/documents.py` raises for
  anything a *model* passes. A bad or unknown path answers `is_error: true` with the corpus's
  real paths listed (which is the recovery the measurement is interested in); a search that
  simply matched nothing answers `is_error: false` with a note steering the model to
  `list_documents`, because a normal retrieval miss is not a malfunction and flagging it as
  one would mislabel the transcript.
- **Every corpus call runs inside a `SAVEPOINT`, and that is not boilerplate.** This is the
  only tool executor that runs a statement on the executor's *own* session, and a statement
  Postgres refuses aborts that session's transaction — after which the row's own `ok` write,
  which happens *after* `run_tool_loop` returns and therefore outside the per-row handler,
  fails and takes every remaining row down with it, leaving the run stuck `running`. One
  argument the model chose would cost the whole suite. The reachable way in is a `query` or
  `path` carrying a NUL byte: asyncpg refuses it as a bind parameter, so the call can never
  even be the miss it was always going to be. `session.rollback()` is the **wrong** repair —
  it clears the aborted state but also expires every instance in the identity map, so the
  next row's attribute read becomes lazy IO in a context that cannot await it
  (`greenlet_spawn has not been called`) and the suite fails from the second row on. Rolling
  back to a savepoint clears the abort and leaves the identity map alone. Pinned by
  `test_executor.py::test_an_argument_postgres_refuses_costs_one_row_not_the_run`, which
  needs **two** rows to mean anything.
- **The frozen/live line falls differently here, deliberately.** The tool *definitions* are
  frozen into `tools_snapshot` like any others; the markdown is read **live** at execution
  time, so a re-run after editing the documentation retrieves from the edited corpus — which
  is the point when the documentation is what changed. There is **no corpus versioning or
  freezing in v1**. A snapshot entry for a documents tool carries `document_count` and
  `corpus_updated_at` as a forward-compat crumb (two keys in a dict already being
  serialized, omitted when absent so a manual/MCP entry stays byte-identical); nothing reads
  them yet.
- **Roles follow the existing content-vs-credentials split with no new rule**: the toolset
  is `Admin` (it is the container that *can* hold credentials), the documents inside are
  `Writer`, because markdown never is. Sharing is allowed — `is_global` on a documents
  toolset works like any other, settable only in Base, and a borrowed corpus is fully
  readable and retrievable everywhere while every write is refused by name
  (`_refuse_if_borrowed` over HTTP, `_assert_own_corpus` over MCP) rather than left as
  `scope_where`'s silent no-op.
- **A corpus has two write doors and one set of rules for what a document is.**
  `app/api/toolsets.py` (a JSON body, and a multipart upload of `.md`/`.markdown` files
  whose *filename is the corpus path*) and `app/mcp/server.py` (an agent pushing another
  repo's `docs/` in) write the same table, `read_document` matches `path` exactly, and
  `UNIQUE (toolset_id, path)` is all that keeps one document from becoming two. So
  `clean_document_path`, `normalize_markdown` and `derive_document_title` live in
  `app/services/documents.py` and **both doors call them**: separators normalised and a
  leading `./` or `/` stripped (`..` refused as a second spelling of a key, not as a
  danger), case never folded, a leading BOM dropped and CRLF folded to LF — that last one
  because `read_document` windows by *characters* and reports those offsets back to the
  model, so a corpus mixing line endings would hand out windows whose length depends on
  which editor last saved the file. They raise plain `ValueError`; each door translates it
  into its own vocabulary (a 422, an `ok: false` upload row, an `isError` content block).
  Two refusals live in `normalize_markdown` for exactly that reason. **A NUL byte is
  refused**, because Postgres cannot hold one in a `text` column — so the alternative is not
  a document containing a NUL but an unhandled driver error, which in the multipart route
  would discard the other twenty-nine files in a request that promises per-file isolation.
  And **`MAX_DOCUMENT_CHARS` is one ceiling asked by all three write paths**: a per-door
  character limit is the same class of bug as two spellings of one path, since the JSON
  route, the upload route and MCP write the same table and a corpus must not end up holding
  a row only one of them could have written. The upload route's separate 1 MiB *byte* check
  is a different concern in a different unit — refusing before it decodes, which keeps a
  dropped video out of memory rather than merely out of the column — and coexists on purpose.

### Results (`/results`): two pivots

`GET /api/results/matrix` (`backend/app/api/results.py`, pure logic in
`backend/app/services/compare.py`, scoped reads in `backend/app/repos/results.py`) has a
`mode`: `models` (default) and `runs`. `?mode=` wins; without it a URL carrying `?runs=`
stays in run mode, so an existing link keeps its view. **One model is a valid selection**
(`MIN_COMPARE_MODELS = 1`) — the same matrix with a single column is "show me everything
this model answered", the cheapest review of a model across all of its runs. **One run is
likewise a valid selection** (`MIN_COMPARE_RUNS = 1`) — it is still the only view that shows
that run's rows against the live rubric, with its own params/comment in the header, none of
which the run detail page does.

- **By model** (`models=<endpointId>|<modelId>`, **repeated** rather than comma-joined,
  because a model id is free-form text that must never need escaping; plus `?group=`,
  also repeated, to narrow the rows) takes the **live test cases** as rows and fills each
  cell with that model's **most recent `ok` result**, whichever run produced it. Columns
  are keyed on endpoint
  *id* + model, so an endpoint rename doesn't split a column and one model on two boxes
  stays two columns. Archived runs are excluded outright.
- **By run** (`runs=1,5`) is the only pivot that can put two runs of the *same* model
  side by side (quantization swap, temperature A/B, a Verify comparison against a
  baseline) — in model mode they collapse into one column and the newest wins. Which is
  why a run-mode column header also carries the run's **params** and its **comment**
  (`CompareRunView.params` / `.comment`, the raw `runs.params` JSON and the note, rendered
  client-side by `formatParams`): two columns of one model differ only in what they were
  asked for and why, so without those the A/B reads as the same model twice. The params
  are one clipped line, the note a peek (hover to read, click to pin) like the row's
  prompts and rubric, and the run picker above the matrix carries the same note as a
  speech-bubble tooltip. A **model**-mode header has no run to name, so it reports only
  what its cells *on screen* agree on — the shared params, or "params vary across runs".

Falling back past a **newer failed attempt** must not blank a good older answer, so a
model-mode cell keeps the newest `ok` row and reports the skipped one
(`CompareCellView.superseded`). `describe_row_drift` (`app.services.compare`) names
whatever is *not* held constant across a row, since a difference between cells might be
config rather than model: the three frozen texts **separately** — "system prompt" / "task
prompt" / "test case text" — plus expected output, `tools_snapshot`, tool mode, tool choice,
run params, and max turns once any cell actually executes tools. In model mode each of the
three texts (and the rubric) is compared again against the live test case ("… edited
since"); a part already drifting across the row is not additionally reported. Expected
output sits outside the three because the model never saw it — rewriting the rubric changes
the standard past results were graded by rather than invalidating them. Splitting the
prompt parts out is the reading-side payoff of prompt kinds: a cell can say *the task
prompt changed* instead of merging prompt drift and data drift into one indistinguishable
"user message differs".

**The meh/bad quick filter is client-side and re-derives the tallies it invalidates.** A
"Show: All / meh + bad" `SelectButton` in the matrix heading (there rather than in a
`.filter-row` above it, so it survives fullscreen) keeps the rows where *any* cell was
rated meh or bad — the whole line, because the point is reading a weak answer against the
columns that did better on it. It filters `rows` in `ResultsView.vue` rather than
re-requesting: the pickers change the *selection* and are reconciled against the server,
this only narrows what is rendered, and a refetch would collapse every peek a reviewer has
open mid-rating-pass. Model mode's `column_tallies` are "the cells on screen" and its
header reads "n/<rows> answered", so filtering re-derives them client-side (`tallyColumn`,
the twin of `_tallies` in `backend/app/api/results.py`, `null` and not 0 for "nothing
measured"); run mode needs none of that, its header numbers being the run's own totals.
The control renders only while something is rated meh or bad, and rating the last one away
resets the filter — a table narrowed by a control no longer on screen would be stranded
empty.

### Ratings

Three manual verdicts plus unrated: `good` / `meh` / `bad`. `meh` means "not wrong, but
not good enough" — usually a signal the *test case* needs work rather than the model.
Named `meh` and not `ok` on purpose: `ok` already means "completed without error" as a
`run_results.status`, and one word meaning two things in the same table is exactly the
confusion this rating exists to remove. The column is `Text`, not an enum type, so adding
a rating value needs no migration.

### Endpoint/model history

`endpoint_models` records every model ever seen per endpoint and is never deleted from:
discovery upserts (`currently_loaded` flips false for models absent from `/v1/models`),
manual adds, and every run (`source: "run"`). `source` says how a model was *first*
learned about, so a later sighting bumps `last_seen_at` and nothing else. An endpoint is a
`base_url` + optional `api_key` + free-text hardware specs — anything that speaks the
OpenAI protocol, whether it's a box you own or a hosted API. Live probing is
`backend/app/services/discovery.py` (`POST /{id}/discover`, member — upserts into
`endpoint_models`; `POST /{id}/test`, admin — just reports reachability, since it exercises
the stored API key).

On a **global** endpoint (see "The Base workspace" above) `endpoint_models` accumulates
across every engagement that has run against it — deliberately: shared hardware has one
shared history, and that is exactly what makes "this box already served qwen3:32b" useful
to the next customer. The sighting write is an upsert for that reason too: two workspaces
discovering the same shared box at once (which the new-run page's page-load probe makes
routine) would otherwise both read no row and both insert.

### This app as an MCP server

`POST /mcp` (`backend/app/mcp/server.py`) lets an agent author the evaluation from
outside: push another project's real prompts and test cases in, start a run, read the
measurements back — the interesting test cases already exist in other repos.

- **The official `mcp` Python SDK**, FastMCP, streamable HTTP, **stateless** (no session
  id — every POST is independent, which is what lets the auth middleware and the
  workspace precedence chain both be per-request rather than per-connection state).
  Mounted as a route (`mount_mcp`, `MCP_PATH = "/mcp"`) rather than a sub-application,
  because a mount only matches the path with a trailing slash and would 307-redirect the
  documented one. The route is **POST-only**, and that is what lets the path be shared with
  the SPA's own `/mcp` settings page: Starlette answers a path match with the wrong method
  as `Match.PARTIAL` and keeps searching, so a browser's `GET /mcp` falls through to the
  SPA catch-all in `app/main.py` and gets the management page, while an MCP client POSTs
  the protocol to the same URL. In dev the vite proxy draws the same line the other way
  round (`frontend/vite.config.ts`: `/mcp` is proxied to the backend except for GET, which
  is bypassed to `index.html`).
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
  annotation and the gate a viewer's token is refused by in `_call`, so the two cannot
  drift apart — and since `_call` is the only route to a database session, "a viewer's
  token is refused everything that writes" is impossible to forget.
- **Everything relatable by name is** (`backend/app/mcp/refs.py`: `RowRef`,
  `parse_row_ref`, `resolve_row_ref`) — group, prompt, toolset, endpoint take a name or an
  id, a numeric string is always an id, and an ambiguous name is refused with a
  "Known: …" list of that workspace's rows.
- **Every call names a customer workspace** (`backend/app/mcp/customer.py`,
  `pick_customer_ref`): `customer` argument → `X-Customer` header → the token's default
  (always `None` today — `api_tokens` has no customer column, and the chain is written out
  anyway so adding one changes one line and no call site) → refusal listing the known
  workspaces. Nothing is guessed. `list_customers` is the one tool that needs no scope, and
  it's `readOnly` so a viewer's token can orient itself before being refused a write
  elsewhere. **Id-addressed calls resolve the workspace from the row instead**
  (`resolve_row_scope`, backed by the unscoped `customer_id_for_run` /
  `customer_id_for_result` lookups beside `scope_for_run`): a run or result id is globally
  unique, so `get_run`, `get_run_result`, `set_rating` and `execute_run` need no `customer`
  at all — and one that *is* named (argument or header) can only agree or contradict, a
  contradiction being refused naming the row's actual workspace rather than silently
  overridden or answered with a lying "not found". Workspaces are labels, not tenants, so
  naming the right one reveals nothing a `list_customers` + per-workspace probe wouldn't.
  This is the id-addressed tools' answer to the deep-link limitation noted under "Customer
  workspaces".
- **The 23 tools** (registered in `backend/app/mcp/server.py`):
  `list_customers`, `list_endpoints`, `list_prompts`, `create_prompt`, `update_prompt`,
  `commit_prompt`, `list_prompt_versions`, `get_prompt_version`, `set_baseline`,
  `list_test_groups`, `create_test_group` (name-idempotent — a second call returns the
  existing group, `created: false`), `list_test_cases`, `create_test_case`,
  `update_test_case` (patches only the keys present, and re-checks tool config as it will
  be *after* the patch), `list_documents` / `create_document` / `update_document` (a
  `documents` toolset's markdown corpus — `list_documents` doubles as the corpus discovery
  path, since toolsets themselves are not listable here, and it reports metadata only,
  never a document's text, which exists to be read by the *model* at execution time),
  `create_run`, `execute_run` (fire-and-forget — safe only because
  the executor already persists every row as it finishes), `list_runs`, `get_run`,
  `get_run_result`, `set_rating` (refuses a still-pending/running row; omitting `note`
  leaves an existing one untouched, `"unrated"` clears the rating — JSON-RPC cannot
  distinguish "absent" from "null" by the time an argument reaches the tool, so presence is
  read off the raw `tools/call` params via `raw_arguments`). `mark_deployed` and
  `delete_test_case`/`delete_prompt`/`delete_document` are **deliberately absent** —
  deploying is a UI-only human claim about a customer's production system, and there is no
  delete surface over MCP at all.
- **Prompt kinds on the wire.** `create_prompt` / `update_prompt` take `kind`
  (`"system"` default), `list_prompts` returns it, and `create_test_case` /
  `update_test_case` take `system_prompt` and `task_prompt` — both `RowRef`s resolving by
  name or id. `content` is optional on `create_test_case` (a task prompt can be the whole
  user message), which is why its JSON-Schema `required` is `{group, title}`. An
  unrecognised `kind` is **refused** by `_parse_kind`, never coerced — deliberately the
  opposite of `parse_role`, whose degrade-to-`viewer` is safe because the fallback is the
  least privileged value; here there is no safe fallback, since guessing would silently move
  the text between the system message and the user message. A test case reads back both
  slots twice over: as the referenced asset (`system_prompt` / `task_prompt`, id and name)
  and as the text those slots hold (`system_prompt_text` / `task_prompt_text`) — the same
  two key names `get_run_result` uses for the frozen copies, so a case and its result speak
  one vocabulary. `get_run` / `get_run_result` likewise carry both version ids.
- **Not writable over MCP**: endpoints, toolsets and tools (a base URL with an API key and
  an MCP server URL are credentials — the app's line is content over the API, credentials
  in the UI), customer workspaces (creating an engagement is a human decision with
  billing behind it), and a prompt's `deployed_version_id` (see "Prompt versioning").
  Versions themselves *are* writable over MCP (`commit_prompt`, `set_baseline`) because
  they are content, not credentials — and a `documents` toolset's **markdown corpus** is
  writable for exactly the same reason, which is where that line now falls one level lower
  than the table: the *toolset* stays uncreatable and uneditable here, the documents inside
  one are content, and an agent that already has another project's `docs/` on disk is the
  primary reason `create_document` exists. A corpus borrowed from Base is readable and
  retrievable from every workspace but explicitly refused for writing
  (`_assert_own_corpus`) — a named refusal rather than `scope_where`'s silent no-op, which
  an agent would otherwise read back as a successful edit. `list_endpoints` and
  `create_run`'s `endpoint` argument see and accept **global** endpoints for free, since
  both are read paths that already ask `visible_where` — nothing MCP-specific was needed to
  share them.
- **A judge model reading these results is itself injectable.** `get_run_result` returns
  `system_prompt_text`, `task_prompt_text` and `test_case_text`, and for the
  `Prompt Injection & Instruction Hierarchy` group any of the three can carry a live
  payload. Grade from `expected_output` + `response`, and never let a judge's output pick a
  tool call. Most of that group needs no judge at all — the rubrics are canary strings and
  "was this tool called."

Tests: `backend/tests/test_mcp.py` (pure — argument coercion via `refs.py`, workspace
precedence via `customer.py`, dispatch); `backend/tests/integration/test_mcp_api.py`
exercises the wired-up handler (real Postgres, no server process) via
`backend/tests/integration/conftest.py`.

### The example suite

`docs/example-suite/` is the standard suite — 3 manual toolsets with 12 tools, 16 prompts
and 38 test cases in 4 groups — written as **documentation an agent executes over MCP**,
not a script. The split inside it is forced by the app's own rule: a toolset is not
writable over MCP (the container is where an MCP URL and headers, i.e. credentials, live),
so `toolsets.md` is
instructions for a human in the UI and the four group files are for an agent to push in
with `create_test_group` / `create_prompt` / `create_test_case`. Do the toolsets first: six
test cases reference one by name, and `create_test_case` refuses a tool test whose toolsets
do not exist or hold no enabled tools.

**Every prompt in the suite is a named asset.** Since a test case holds no prompt text,
each of the 16 prompts is its own `create_prompt`, named after the test case it belongs to
and referenced by name. They are all `kind: "system"` on purpose: that is the channel those
texts are sent on today, and re-kinding one to `task` would move it into the user message
and shift every group-4 injection result. Re-kinding is a deliberate, per-prompt act.

Canned tool responses are written to stay correct *whatever arguments the model
passes* — `convert_currency` returns a rate rather than a converted amount, so the
response can never contradict the call and the model still has to do the arithmetic.

**The `Prompt Injection & Instruction Hierarchy` group inverts that principle on
purpose**: there the canned response *is* the attack, because `mock_response` is the
tool-result channel — the one place a real agent meets attacker-controlled text, and the
one with far less refusal training behind it than the user channel. Two invariants that
group depends on: every prompt scores task completion *and* injection resistance, and two
of its fifteen cases (13, 14) fail on **over-defense** instead — a model that refuses
everything instruction-shaped scores perfectly on an attack-only suite and is useless on
real order and invoice correspondence, where "please ignore my previous email" is what
customers actually write (13 tests data that legitimately contains an override, 14 the
*user* overriding themselves mid-message); and `expected_output` is **never sent to the
model** (the user message is built from the task prompt and `content` alone —
`message_assembly.user_message`, never touching `expected_output`), which is what lets the
rating aids state a payload or a canary outright.

## Testing

**The API contract between `backend/app/api/*.py` and `frontend/src/api/*.ts` is the
seam that breaks, and no type checker guards it.** Those TypeScript interfaces are
hand-written descriptions of a Pydantic model; when one drifts, TS still compiles — the
interface is simply a lie about the wire format, and the failure surfaces as a blank
table, a 404/405, or a `TypeError` inside a render that takes the whole dialog down with
it. Real examples of the drift this produces: a wrong field name, `diff: string` for a
`list[str]`, `PATCH` against a `PUT` route, a whole endpoint the frontend called that was
never written, and a `null` credential overwriting a stored API key. So: when changing a
response model, grep the matching `src/api/*.ts` in the same change, and prefer integration
tests that assert the **stored column** over ones that assert a flag derived from it.

**`npm run typecheck` must run `vue-tsc -b`.** Pointing `vue-tsc --noEmit` at
`frontend/tsconfig.json` checks nothing at all: that file is `{"files": [],
"references": [...]}`, so there are no files to check and it always exits 0. Only `-b` (or
`-p tsconfig.app.json`) checks anything in a project-references setup.

Two suites, split by whether they need a database.

`cd backend && uv run pytest` (`backend/tests/*.py`, excludes `tests/integration` via
`pyproject.toml`'s `addopts`) is the pure one and must stay database-free and fast:
`test_message_assembly.py` (both message parts present, each alone, whitespace-only on
either side, both blank), `test_llm.py` (SSE fixtures per provider style, including a
reasoning model's own channel and the throughput guard), `test_compare.py`,
`test_tool_config.py`, `test_tool_loop.py` (metric aggregation, and every value of
`ToolSource` surviving a `tools_snapshot` round trip), `test_documents.py` (which heading a
search hit sits under, `read_document`'s windowing, and the key/markdown/title rules both
write doors share), `test_diff.py`,
`test_attribution.py` (version matching, dirty detection), `test_mcp.py`, `test_scope.py`
(the branded `Scope`, `combine`, `resolve_active_customer_id` — written db-free
precisely so it can live here), `test_policy.py`, `test_passwords.py`, `test_tokens.py`
and `test_invites.py` (the token/invite crypto's pure half — resolving a raw secret needs
a database), `test_discovery.py`, `test_llm_info.py`, `test_mocks.py`, `test_health.py`,
`test_version.py`.

`cd backend && uv run pytest tests/integration` (or `make test-integration`) runs
against the scratch Postgres described above, with a single event loop for the whole
session (`asyncio_default_fixture_loop_scope = "session"` in `pyproject.toml`) because the
suites share one database and one engine bound to whichever loop was running at import
time. `tests/integration/conftest.py` truncates every table between tests. Covers what only
a real database can show: FK cascade/`SET NULL` actions and `Date`/`bool`/float8
round-tripping (`test_schema.py`), the snapshot invariant and `create_run_record`'s
rollback (`test_run_create.py`), the advisory-lock claim (`test_run_lock.py`), the
executor end-to-end against the mock LLM (`test_executor.py`), cross-workspace isolation
including the versioning cases and the Base/global-sharing cases
(`test_workspaces.py::TestGlobals`, `test_versioning.py`), the
login/session/sign-up-closes flow (`test_auth_flow.py`), and every domain router's CRUD
(`test_*_api.py`, including `test_endpoints_api.py`, `test_users_api.py`,
`test_invites_api.py` and `test_documents_api.py` — the last one is also the only place the
multipart upload and real `websearch_to_tsquery`/`ts_headline` retrieval are exercised, and
`test_executor.py` drives a document tool call end to end).

For checking the running app itself (dev server, `http://localhost:5177`) there is a
dedicated agent account in the local dev database: `claude-dev@example.com` /
`claude-dev-pw-1`, role `member`. Sign in with it (e.g. via Playwright) instead of touching
Phil's session in Chrome; sign-up is closed after the first account, so if the dev database
was recreated, re-insert it with `app.auth.passwords.hash_password` via `uv run python`
rather than through the UI. Local-only: the account exists in this machine's dev Postgres,
not in the repo or any deployment.

Everything else is verified against the dev server + the mocks (`backend/app/api/mocks.py`,
gated by `mocks_enabled()` — dev, or `ENABLE_MOCKS=true` in production; the refusal is a
**404, not a 403**, so these routes should not appear to exist in production):

- **Mock LLM** — register an endpoint with base_url `http://localhost:8077/api/mock-llm`.
  `TRIGGER_ERROR` in the user message → 500, `TRIGGER_SLOW` → 2s TTFT delay,
  `TRIGGER_REASONING` → answers like a reasoning model behind vLLM's `--reasoning-parser`
  (thinking on `delta.reasoning_content`, then the answer on `delta.content` with the
  template's newline pair at its head, and `reasoning_tokens` in usage),
  `TRIGGER_TOOL_LOOP` → never stops calling tools (exercises `max_turns`). When the
  request carries `tools` and no tool result yet, it streams a tool call for the first
  tool with arguments synthesized from its schema, split across chunks; once a tool
  result is present it answers in text quoting it.
- **Mock MCP** — an MCP toolset pointing at `http://localhost:8077/api/mock-mcp` serves
  `echo_upper` and `add_numbers`. `?hide=<tool>` drops one from `tools/list` (verifies
  discovery disables rather than deletes), `?fail=1` makes every call return `isError`.

## Deployment

Docker-related files live under `docker/`, but the **build context is the repo root**, not
`docker/`: `docker/Dockerfile`, `docker/entrypoint.sh`,
`docker/compose.yml` (production), `docker/compose.build.yml` (a local-build override) and
`docker/compose.dev.yml` (the dev Postgres already covered under Commands above).

Two-stage `docker/Dockerfile`: `node:22-alpine` builds `frontend/` and only the compiled
`dist/` crosses into the backend image (no `node`/`npm` in the final image); the backend
stage (`ghcr.io/astral-sh/uv:python3.12-bookworm-slim`) installs from the committed
`backend/uv.lock` (`uv sync --frozen --no-dev`), copies in `app/`, `alembic/` and the
built SPA as `static/` — the same directory `app/main.py`'s SPA-fallback route already
checks for and no-ops on when absent, so dev behaviour (vite's own dev server) is
unaffected. `ENVIRONMENT=production` is baked into the image, which is what makes the
mock routes 404 unless `ENABLE_MOCKS=true` is also set. `docker/entrypoint.sh` refuses to
start without `DATABASE_URL`, then runs `alembic upgrade head` before handing off to
`uvicorn` — `set -e` means a broken migration stops the container rather than serving a
half-migrated database, since statements are applied verbatim.

`docker/compose.yml` bundles a `postgres:17-alpine` service (`--encoding=UTF8
--lc-collate=C --lc-ctype=C`, since prompt/test-case content can carry Unicode Tags) the
app waits for via `depends_on: condition: service_healthy`; state lives in the named
volume `pgdata`. `POSTGRES_PASSWORD` is required in `.env`; `DATABASE_URL` optionally
overrides the bundled database with an external one. The app is published on
`127.0.0.1:8000` by default — change the port mapping or front it with a reverse proxy to
expose it further.

**All three compose files pin `name: promptrack`.** Without it Compose derives the project
name from the directory it is invoked from, which would rename the project and orphan the
existing `promptrack_pgdata` / `promptrack-dev-pgdata` volumes on the next `up`. **Every
`compose.yml` command also passes `--env-file .env` and is run from the repo root**:
Compose derives its project directory from the compose file's location, so from `docker/`
it would otherwise miss the repo-root `.env` entirely and fail on `POSTGRES_PASSWORD`.
`compose.dev.yml` needs no `--env-file` — its password is hardcoded `dev` and it reads
nothing from the environment.

`docker/compose.yml` only has an `image:` stanza — it pulls
`ghcr.io/webix-solutions-gmbh/promptrack:${PROMPTRACK_TAG:-main}` rather than building. Deploy is
`docker compose -f docker/compose.yml --env-file .env pull` then `... up -d`, or
`make docker-up`, which wraps both. `docker/compose.build.yml` is the override that adds
`build:` back for a local build instead: `-f docker/compose.yml -f
docker/compose.build.yml --env-file .env up -d --build`, or `make docker-build`. While
the repo is private the GHCR package is private too, so a deployment host needs
`docker login ghcr.io` with a `read:packages` PAT before either target's `pull`/`up` will
succeed.

`.github/workflows/docker.yml` builds and pushes the image on every push to `main`, on
`v*` tags, and on manual dispatch — `linux/amd64` only. It authenticates with the
built-in `GITHUB_TOKEN` (no configured secret) and tags the image with the branch name,
`sha-<short>`, and, on a version tag, the semver (full and `major.minor`) plus `latest`.
The image name is hardcoded lowercase (`ghcr.io/webix-solutions-gmbh/promptrack`) rather
than interpolated from `github.repository`, because that variable is mixed-case
(`webix-solutions-GmbH/PromptRack`) and GHCR rejects uppercase in image names outright. It passes
`PROMPTRACK_COMMIT=${{ github.sha }}` into the `ARG` the Dockerfile already declares, so
`GET /api/version` reports the exact commit a running container was built from. It
deliberately runs no tests before pushing — a known gap, not an oversight, so don't
assume a `main` push through this workflow was verified.

This project is developed against a self-hosted deployment, so a change that touches
Docker, compose or migrations should say what it was actually tested on.
