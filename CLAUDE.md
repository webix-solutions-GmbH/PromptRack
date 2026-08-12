# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

@AGENTS.md

## Environment

Node 22 is installed via nvm only and is NOT on the default PATH. Prepend to every shell command:

```bash
export PATH="$HOME/.nvm/versions/node/v22.23.1/bin:$PATH"
```

## Commands

```bash
npm run dev                          # starts the dev postgres, migrates, serves http://localhost:3000/agent-val (basePath!)
npm test                             # vitest run — the pure suite, no database
npx vitest run src/lib/llm.test.ts   # single test file
npm run test:integration             # throwaway postgres in docker + tests/integration/**
npx tsc --noEmit                     # typecheck
npm run lint                         # eslint
npm run build                        # production build (also catches route/RSC errors)
npm run db:init                      # generate pending migration SQL + apply drizzle/
npm run db:generate                  # drizzle-kit generate — write a migration under drizzle/ from schema.ts
npm run db:migrate                   # node scripts/init-db.mjs — apply pending migrations only
npm run db:reset                     # drop the dev postgres volume and re-migrate from empty
npm run db:seed                      # seed toolsets + prompt groups (additive, respects deletions)
```

Everything reads `DATABASE_URL`. `scripts/dev-db.mjs` (which `npm run dev` runs first) brings up `docker-compose.dev.yml`'s postgres on `127.0.0.1:5433`, waits for it, applies the migrations and writes `.env.local` on first run; it is idempotent and costs under a second once the container is up. **Setting `DATABASE_URL` to anything else makes it skip docker entirely** — the escape hatch for a managed database.

Migrations are committed under `drizzle/`; `drizzle-kit generate` can still prompt interactively when it suspects a *rename*, so run it in a real terminal when renaming a table or column.

Git: branch is `master` (not main); remote is Azure DevOps. Commits so far are milestone-sized with imperative messages.

## What this is

Single-user LLM benchmarking web app: define test prompts (grouped, optionally with expected output), run them sequentially against OpenAI-compatible endpoints (Ollama/LM Studio/vLLM) on registered machines, measure TTFT/duration/tokens/tok-s, rate results good/bad manually, compare runs in a matrix. A prompt can also be a **tool test**: offer the model a set of functions and either record what it wanted to call, or really execute the calls through an MCP server and loop until it answers.

## Architecture

- **Stack**: Next.js 16 App Router + TypeScript + Tailwind v4, Drizzle ORM + Postgres (`pg` — pure JS, so no build toolchain in the alpine image, and already on Next's built-in `serverExternalPackages` list, so it is never bundled). Connection string in `DATABASE_URL`; `src/db/index.ts` exports a `Pool` singleton next to `db` because `src/lib/run-lock.ts` needs a dedicated pooled connection (`max` from `DATABASE_POOL_MAX`, default 10 — it must exceed concurrent runs plus normal request concurrency, since an executing run holds one connection for its whole duration). Postgres enforces the FK cascades natively. `src/db/schema.ts` is the single source of truth; `drizzle-kit generate` writes incremental SQL migrations under `drizzle/` (committed), applied by `scripts/init-db.mjs`.
  - Timestamps are `timestamp({ withTimezone: true, mode: 'date' })`, so **server code holds `Date`**; everything crossing into a client component, into `src/lib/compare.ts`, or into an MCP JSON response converts with `.getTime()`. `withTimezone` keeps node-postgres from parsing naive timestamps in the process-local zone. The MCP wire format stays **epoch millis** on purpose: `get_run`/`list_runs` already emitted numbers and external agents parse them.
  - `tokens_per_sec` is `doublePrecision` (float8), not `real`: SQLite's `REAL` was an 8-byte double, and pg's `real` is float4, which would have silently rounded every imported value.
  - Enum-ish columns stay `text('x', { enum: [...] })` rather than `pgEnum` — that is what keeps "adding a rating or status value needs no migration" true, and lets `parseRating` still see a legacy value.
- **Mutations are Server Actions** (`src/actions/*.ts`); **Route Handlers exist only where streaming or a live network probe is needed**: run execution (`/api/runs/[id]/execute`, NDJSON progress stream), model discovery + connection test (`/api/machines/[id]/*`), MCP tool discovery (`/api/toolsets/[id]/discover`), and the dev mocks (`/api/mock-llm/*`, `/api/mock-mcp`).
- **basePath `/agent-val`** (`next.config.ts`, constant in `src/lib/base-path.ts`): `next/link` and the router prefix automatically, but raw client `fetch()` calls to our own API routes MUST go through `apiPath()` from `src/lib/base-path.ts`.

### Snapshot model (the core invariant)

Editing or deleting prompts, system prompts, machines, or toolsets must never change how a past run displays. `createRun` (`src/actions/runs.ts`) freezes everything into `run_results` rows at creation time: prompt text, title, group name, expected output, the **already-resolved** effective system prompt, and `tools_snapshot`. `prompt_id`/`machine_id` FKs are kept (SET NULL on delete) only for cross-run comparison; rendering always uses the snapshots. The compare page matches rows primarily by `prompt_id`, falling back to normalized `prompt_text` equality for deleted prompts (`src/lib/compare.ts`).

`createRunRecord` (`src/lib/run-create.ts`) writes the `runs` row, all of its `run_results` in one multi-row INSERT, and the `machine_models` upsert inside a single transaction, so a crash can no longer leave a run with no prompts in it — which Resume would have reported as finished. Validation (`groupIds`, "no enabled tools", tool-name collisions) and `probeLlmInfo` stay *outside* it: the first two throw before anything is written, and the third is a network call that must never hold a transaction open.

The line between frozen and live is **content vs. credentials**: prompt text, tool definitions and a manual tool's canned response travel with the run; a machine's `base_url`/`api_key` and a toolset's `mcp_url`/headers are read live at execution time so a moved endpoint doesn't break Resume.

### Data access

No page, action, route handler or MCP tool touches `db` directly — ESLint forbids importing `@/db` outside `src/db/**` (one exemption, `src/lib/run-lock.ts`, which needs the `pool` itself for a Postgres advisory lock; that is infrastructure, not data access). Every query goes through a repository in `src/db/repo/*` whose functions all take a `Scope` (`src/db/scope.ts`) as their first parameter. `Scope` is a branded type: it can only be produced by `currentScope()` (the request's workspace), `scopeFromCustomerId()` (derived from a row — how the background executor stays scoped, via `scopeForRun`) or `systemScope(reason)` (the grep-able escape hatch). Today there is one implicit workspace, so `scopeWhere` / `scopeValues` / `scopeThroughParent` are no-ops, each carrying the `// Phase 5:` comment that shows its future form; Phase 5 adds `customer_id` to the five root tables and fills those three in, and **no call site changes**. Child tables (`machine_models`, `tools`, `prompts`, `prompt_toolsets`, `run_results`) inherit scope through their FK, which is why child reads join their parent and child writes carry their parent key.

Two consequences worth knowing:

- **`scopeForRun(runId)` is the one deliberately unscoped lookup.** The executor runs outside any request (MCP `execute_run` is fire-and-forget), so it reads the run row and derives the scope *from* it. It is the only function Phase 4 has to put an authorization check in front of.
- **A transaction never leaves the layer.** `withTransaction` (`src/db/repo/scoped.ts`) hands a `DbHandle` to the repo functions that accept one (`createRun`, `insertRunResults`, `touchMachineModel`), which is how `createRunRecord` keeps the run row, its result rows and the model sighting atomic without any caller knowing.

`@/db/schema` stays importable everywhere — components legitimately use `typeof runResults.$inferSelect`. Only the `db` *handle* is restricted.

`/results` cells carry a `scopeKey` (`''` today, the customer id in Phase 5). `buildCompareMatrix` keys its deleted-prompt text fallback on `scopeKey + text`, so two workspaces' identical prompts can never collapse into one row; `promptId` matching is unaffected, ids being global.

### System prompt resolution

A prompt references an optional base system prompt plus a mode: `append` (base + "\n\n" + custom text) or `override` (custom text only); empty/whitespace result → no system message. Pure function `resolveEffectiveSystemPrompt` in `src/lib/system-prompt.ts` — used at run creation (snapshot) and for the live preview in the prompt editor.

### Run execution pipeline

`src/lib/llm.ts` (raw-fetch SSE client, no SDK) → `src/lib/tool-loop.ts` (one to N turns) → `src/lib/run-executor.ts` (sequential loop over rows) → execute route (NDJSON) → `src/components/runs/run-detail.tsx` (client driver).

- `llm.ts` parses SSE chunks tolerant of provider differences (usage in final empty-choices chunk vs. on last content chunk; chunks split across reads). No usage received → estimate `ceil(chars/4)` over text **plus** serialized tool calls, flag `tokensEstimated` (UI shows `~`). TTFT = first content delta **or** first tool-call fragment, whichever comes first — a tool-call-only response streams no content.
- Executor invariants: one execution per run via a Postgres **advisory lock** (`src/lib/run-lock.ts`, `pg_try_advisory_lock` on a dedicated pooled connection held for the whole run) + 409 from the route. It replaced a module-level in-memory `Set`, and the lock living on a connection is the point: it dies with the connection, so a crashed process releases it exactly the way the `Set` used to vanish — the stale-`running` reclaim below is still the recovery path — while more than one app process is now safe. A lock *table* would have needed expiry and heartbeats to get the same crash semantics. `isRunExecuting` reads `pg_locks` rather than taking the lock, and is therefore async now. every result row is persisted the moment it finishes; errors mark the row `error` and the loop continues; abort (client disconnect) resets the in-flight row to `pending`; rows stuck in `running` from a crashed process are reclaimed to `pending` at next execution start. Run status `failed` is reserved for "every attempted result died at connection level"; partial errors still end `completed`.
- Execution is tied to the HTTP request lifetime (`request.signal`) — closing the tab stops the run; Resume picks up remaining `pending` rows.
- `tokens_per_sec = completionTokens / ((durationMs - ttftMs) / 1000)` — rate over the generation window, not total duration. For a multi-turn tool run the denominator is the **sum of each turn's own** generation window (`aggregate` in `tool-loop.ts`), so later prefills aren't counted as generation; for a single turn it reduces to exactly the formula above.

### Tool / API calling

A prompt has a `tool_mode`: `none` (classic one-shot), `definitions` (offer the tools, record the calls, execute nothing), or `execute` (run each call, feed the result back, loop to `max_turns`). It selects **any number** of toolsets, so one test can combine e.g. Odoo + websearch; duplicate tool names across selected toolsets are refused in the editor and again in `createRun`.

- **Toolsets** are `manual` (tools authored in the UI, answering with `mock_response` verbatim — this is what keeps a multi-turn test deterministic) or `mcp` (tools discovered from a streamable-HTTP MCP server and really executed against it). `tools` rows follow the `machine_models` precedent: discovery upserts and **never deletes** — a tool absent from `tools/list` only flips `enabled` false.
- **MCP is HTTP-only on purpose** (`src/lib/mcp-client.ts`, `@modelcontextprotocol/sdk`): a server is just a URL + headers, so real integrations run as their own containers on `llm_default` and nothing extra is baked into this image. Connections are per-operation, not pooled.
- **A tool failure is never a failed row.** The error text is serialized back to the model as that tool's output — what a real agent sees, and itself worth measuring. Only connection-level `LlmError`s can fail a row, preserving `failed` = "the machine was never reachable".
- The loop stops **before** executing calls it has no turn budget left to use, so a real ERP never gets hit for results that could not reach the model. `stopped_reason` is `stop` / `definitions_only` / `max_turns`.
- Existing metric columns keep their meaning — `response_text` = final assistant text, `ttft_ms` = first turn's TTFT, `duration_ms`/token columns = sums over model turns only (tool wait time is excluded, and lives per call in the transcript). Tool detail is *added alongside* in `transcript_json` / `turns_json` / `turn_count` / `tool_call_count`, all null when `tool_mode = 'none'` — which is what keeps every pre-existing run rendering unchanged.
- `run-events.ts` gains `turnStart` / `toolCall` / `toolResult`, and `delta` carries a `turn` only on tool runs, so a plain prompt's wire format is byte-identical to before. `run-detail.tsx` assembles a live transcript from those events; the finished row replaces it on `resultDone`.

### Results (`/results`): two pivots

`/results` has a `mode`: `models` (the default) and `runs`. `?mode=` wins; without it a URL carrying `?runs=` stays in run mode, so old links keep their view. The page was called *Compare* and lived at `/compare`, which is now a 307 redirect in `next.config.ts` (query values pass through, so bookmarked selections survive) — it was renamed because **one model is a valid selection** (`MIN_COMPARE_MODELS = 1`): the same matrix with a single column is "show me everything this model answered", which is the cheapest review of a model across all of its runs. Run mode still needs two, since a single run is already its own detail page. The page carries no explanatory blurb; the pickers and column headers are the explanation, and the only sentence left is the one thing the UI cannot show — how many archived runs the picker is hiding.

- **By model** (`?mode=models&model=<machineId>|<modelId>&…`, repeated params, plus `?group=` to narrow the rows) takes the **live prompts** as the rows and fills each cell with that model's **most recent `ok` result**, whichever run produced it. Columns are model × machine, keyed on the machine *id* so a rename does not split a column and one model on two boxes stays two columns — `tokens_per_sec` belongs to the hardware. Archived runs are excluded outright (no per-run selection could ask one back). Header tallies are computed over the cells **on screen**, not whole runs.
- **By run** (`?mode=runs&runs=1,5`) is unchanged and still the only pivot that can put two runs of the *same* model side by side (quantization swap, temperature A/B, prompt rewrite) — in model mode they collapse into one column and the newest wins.

Two consequences of "latest result" replacing "one run", both made visible rather than hidden:

- Falling back past a **newer failed attempt** (endpoint down) must not blank a good older answer, so the cell keeps the newest `ok` row and reports the skipped one (`CompareCellView.superseded`). Every model-mode cell therefore also names its run and date — the column header no longer does.
- A column's cells can come from runs with **different conditions** (system prompt, tools, temperature), so a difference between cells might be config rather than model. `describeRowDrift` compares prompt text / system prompt / `tools_snapshot` / tool mode / tool choice / `runs.params` (and `max_turns` only when `tool_mode = 'execute'`) across a row and the row header names whatever is not held constant. In model mode it also compares against the live prompt → `prompt edited since`. It runs in both modes; with a single cell in the row the "differs across cells:" prefix is dropped, because `prompt edited since` is then the only thing it can report.

Model mode is anchored to live prompts, so a deleted prompt cannot appear in it at all — the `prompt_text` fallback matching stays a run-mode concern. Prompts in scope that no selected model has answered are counted, not rendered as an all-empty row.

### Ratings

Three manual verdicts plus unrated: `good` / `meh` / `bad`, all defined in `src/lib/rating.ts` (type, order, labels, colours, `countRatings`, `ratingScore`) and drawn by the single `src/components/runs/rating-badge.tsx`. `meh` means "not wrong, but not good enough" — usually a signal the *prompt* needs work rather than the model.

Named `meh` and not `ok` on purpose: `ok` already means "completed without error" as a `run_results.status`, and one word meaning two things in the same table is exactly the confusion this rating exists to remove.

The column is `text` with a drizzle enum rather than a `pgEnum`, so the constraint is type-level only — adding `meh` needed **no migration**, and pre-existing `good`/`bad` rows are untouched. `parseRating` treats any unrecognised stored value as unrated, so a legacy value can never disappear from the totals. `ratingScore` (the runs-list sort) is `good - bad`: `meh` is deliberately neutral, so an all-meh run sorts level with an unrated one.

### Model detection on the new-run page

Opening `/runs/new` (and switching machine) POSTs `/api/machines/[id]/discover` from the client, then `router.refresh()`. That reuses machine discovery rather than adding a read-only probe, because the point is to flip `currently_loaded` — otherwise the "Currently loaded" optgroup shows whatever the last manual Discover found. It also keeps the machine's model history fresh as a side effect. Exactly one detected model is auto-selected (the usual one-model-per-endpoint vLLM case); with several it only groups them. An unreachable endpoint degrades to a warning with previously-seen models still selectable, and a `probeSeq` ref discards a slow answer for a machine the user has since switched away from.

### Archiving runs

`runs.archived_at` (nullable timestamp) — deliberately **not** a `status` value, because `status` is the execution state machine Resume depends on: an archived run with pending rows has to stay `pending`, so it can be unarchived and finished. The UI still presents it as a state (amber `archived` badge next to the status badge).

Archived runs are hidden from the runs list (`?archived=only` / `?archived=all` to see them), from the dashboard's stats and recent list, and from the results page's run picker — except when already named in `?runs=`, so a bookmarked comparison keeps working and stays deselectable. Model mode has no such exception; see above. `setRunArchived` refuses while a run is executing, like delete.

### This app as an MCP server

`POST /api/mcp` lets an agent author the benchmark from outside: push another project's real system prompts and prompts in, start a run, read the measurements back. The point is that the interesting test cases already exist in other repos.

- **Hand-rolled JSON-RPC**, not the SDK's server transport (`StreamableHTTPServerTransport` needs Node's `ServerResponse`, a route handler has Web `Request`/`Response`). `src/lib/mcp/protocol.ts` implements `initialize` / `tools/list` / `tools/call` / `ping` and answers with plain JSON — the same shape `api/mock-mcp` already uses for the client side, and verified against the real SDK client. Stateless: no session id, so nothing survives (or needs to survive) a restart. `resources/list` and `prompts/list` return empty lists rather than errors because clients probe them regardless of advertised capabilities.
- **Auth** (`src/lib/mcp/auth.ts`) is one key in `MCP_API_KEY`, read from `x-api-key` **before** `Authorization: Bearer` — in production Caddy also demands basic auth for `/agent-val*`, so both credentials have to fit in one request. No key configured → every request is refused (503), because an unauthenticated *write* API is a worse default than a disabled one.
- **A tool's refusal is `isError` content, not a JSON-RPC error** (`McpToolError`), so the calling model reads the message and fixes its arguments — the same reasoning as `tool-loop.ts` feeding tool failures back to the model. Only an unexpected throw becomes `-32603`.
- **Everything relatable by name is** (`RowRef` in `src/lib/mcp/args.ts`): group, system prompt, toolset, machine take a name or an id, a numeric string is always an id, and an ambiguous name is refused rather than guessed. `create_prompt_group` is name-idempotent (returns the existing group, `created: false`) so pushing a set twice cannot duplicate it; `create_system_prompt` is not, because two versions of one name would silently disagree.
- **Validation mirrors the prompt editor** (`assertToolConfig`): a tool test with no enabled tools, or two toolsets defining the same tool name, is refused at authoring time, so a prompt written over MCP can never be one `createRun` would later reject. `update_prompt` patches — only keys actually present change — and re-checks the tool config as it will be *after* the patch, so switching mode without naming toolsets keeps the existing links.
- **Run creation shares one implementation** with the form: `createRun` (`src/actions/runs.ts`) is now just FormData parsing + redirect around `createRunRecord` (`src/lib/run-create.ts`), which holds the snapshot invariant. An MCP-created run is indistinguishable from a UI one.
- **Execution is fire-and-forget**: `execute_run` starts `executeRun` without a request signal and returns immediately, because a run of a dozen prompts outlives any tool-call timeout. Safe only because the executor already persists every row as it finishes and leaves the rest `pending` — polling `get_run` is the progress channel, and the same call resumes.
- **`set_rating` writes the same `rating` column the UI writes, with no provenance flag** — a rating set over MCP is deliberately indistinguishable from a hand-clicked one, so the automation costs the ability to tell afterwards who judged what. That was the explicit choice over a `rated_by` flag or a separate `judge_rating` column; `note` is the only place a caller can record that a check (rather than a person) decided it, which is why the tool description pushes for it. Judging policy stays entirely outside the app: the rubric is already `expected_output`, and `get_run_result` already returns the response plus the whole transcript, so the loop is get_run → get_run_result → grade → set_rating with no new read surface.
  - Two guards: a row still `pending`/`running` is refused, because `execute_run` is fire-and-forget and a grading loop can trivially outrun it; and omitting `note` leaves an existing note untouched (`hasKey`, not `optionalText`-is-null), matching what the UI's rating buttons already do. `unrated` is the wire word for "clear it" — JSON-RPC cannot distinguish absent from null by the time it reaches `optionalEnum`.
  - **A judge model reading these results is itself injectable.** `get_run_result` returns `prompt_text`, which for the `Prompt Injection & Instruction Hierarchy` group carries live payloads — Injection 06's is invisible even in the judge's context. Grade from `expected_output` + `response`, and never let a judge's output pick the tool call. Most of that group needs no judge at all: the rubrics are canary strings and "was this tool called", both mechanically checkable.
- **Not writable over MCP**: machines, toolsets and tools (a base URL with an API key and an MCP server URL are credentials, and the app's own line is content vs. credentials).

Tests cover the pure halves — `src/lib/mcp/args.test.ts` (argument coercion, ref resolution) and `protocol.test.ts` (dispatch, error mapping, auth). The wired-up half was verified against the dev server with the MCP SDK client.

To exercise a wired handler (real `db`, real Postgres) without a server or the production database, use the integration harness: `npm run test:integration` starts `postgres:17-alpine` on port 55432 with its data in a tmpfs, applies the same migrations, runs `tests/integration/**` and removes the container afterwards. `DATABASE_URL` is what selects the database, so nothing depends on the working directory any more. `vi.mock('next/cache')` is still needed to stub `revalidatePath` for anything under `src/actions/**`.

### Seeding

`scripts/seed-prompts.mjs` (`npm run db:seed`) seeds manual toolsets and prompt groups. Idempotency is recorded in `__app_seeds`, now declared in `src/db/schema.ts` and created by the migrations — the script only writes rows and owns the idempotency semantics: every object is seeded at most once *ever*, so new seed entries land in groups an earlier version created while anything you deleted stays deleted. A pre-ledger database is backfilled from what is already present on the first run.

Seeded canned tool responses are written to stay correct *whatever arguments the model passes* — `convert_currency` returns a rate rather than a converted amount, so the response can never contradict the call and the model still has to do the arithmetic.

**The `Prompt Injection & Instruction Hierarchy` group inverts that principle on purpose**: there the canned response *is* the attack, because `mock_response` is the tool-result channel — the one place a real agent meets attacker-controlled text, and the one with far less refusal training behind it than the user channel. A manual toolset makes that channel byte-identical across every model compared, which no live MCP server can offer. Its two toolsets are split by *where the payload lives* — `Support Desk (mock, injected content)` carries it in tool **results**, `Poisoned Tool Metadata (mock)` in a tool **description** (the MCP tool-poisoning class, which matters doubly here since discovered tool descriptions are untrusted text) — because one shared toolset would put the poisoned description in context for every prompt and make a failure impossible to attribute.

Two invariants that group depends on:

- **Every prompt scores task completion *and* injection resistance, and two prompts (13, 14) fail on over-defense instead.** A model that refuses everything instruction-shaped scores perfectly on an attack-only suite and is useless on real order/invoice correspondence, where "please ignore my previous email" is what customers actually write. 13 tests data that legitimately contains an override; 14 tests the *user* overriding themselves mid-message — data can't retarget the model, the user always can.
- **`expected_output` is never sent to the model** (`run-executor.ts` builds the user message from `promptText` alone; the snapshot in `run-create.ts` is display-only). That is what lets the rating aids state the payload and the canary outright — and it is load-bearing for the ASCII-smuggling prompt, whose payload is encoded into the Unicode Tags block by `tagEncode` and is invisible in every UI, so the decoded text in `expected_output` is the only way to rate the result. Injection 15 reuses `RECONCILE_SYSTEM` and the invoice body of `Reconcile: quantity mismatch → ASK` verbatim, adding one attacker-controlled position, so the pair isolates injection from ordinary confusion.

### Machine/model history

`machine_models` records every model ever seen per machine and is never deleted from: discovery upserts (`currently_loaded` flips false for models absent from `/v1/models`), manual adds, and every run (`source: 'run'`). A machine IS an endpoint (`base_url` + optional `api_key` + free-text hardware specs).

### Testing

Two suites, split by whether they need a database.

`npm test` (`vitest.config.ts`, `src/**/*.test.ts`) is the pure one and must stay database-free and fast: `system-prompt.test.ts`, `llm.test.ts` (SSE fixtures per provider style, including index-keyed tool-call fragments split mid-JSON), `compare.test.ts`, `tools.test.ts`, `tool-loop.test.ts` (metric aggregation), `mcp/args.test.ts`, `mcp/protocol.test.ts`, `db/scope.test.ts` (the branded scope and `combine`, written db-free precisely so it can live here), `lib/form-data.test.ts` (the FormData readers the server actions share).

`npm run test:integration` (`vitest.integration.config.ts`, `tests/integration/**`) runs against the scratch Postgres described above, with `fileParallelism: false` because the suites share one database and `tests/integration/setup.ts` truncates every table between tests. It covers what only a real database can show: the FK cascade/set-null actions and `Date`/`boolean`/float8 round-tripping (`schema.test.ts`), the snapshot invariant and the `createRunRecord` rollback (`run-create.test.ts`), the advisory-lock claim (`run-lock.test.ts`), and that `seed-prompts.mjs` is idempotent and preserves the invisible Unicode-Tags payload code point for code point (`seed.test.ts`).

Everything else is verified against the dev server + the mocks:

- **Mock LLM** — register a machine with base_url `http://localhost:3000/agent-val/api/mock-llm`. User messages containing `TRIGGER_ERROR` → 500, `TRIGGER_SLOW` → 2s TTFT delay, `TRIGGER_TOOL_LOOP` → never stops calling tools (exercises `max_turns`). When the request carries `tools` and no tool result yet, it streams a tool call for the first tool with arguments synthesized from its schema, split across chunks; once a tool result is present it answers in text quoting it.
- **Mock MCP** — an MCP toolset pointing at `http://localhost:3000/agent-val/api/mock-mcp` serves `echo_upper` and `add_numbers`. `?hide=<tool>` drops one from `tools/list` (verifies discovery disables rather than deletes), `?fail=1` makes every call return `isError`.

## Deployment

Docker multi-stage build (`node:22-alpine`, standalone output). No build toolchain in the deps stage and no `drizzle-kit` in the image: `pg` is pure JS, and `drizzle/` is committed, so generating in the image would only re-derive a duplicate baseline. `next.config.ts` traces `pg*`/`drizzle-orm` into the standalone output explicitly, because `scripts/init-db.mjs` and `scripts/seed-prompts.mjs` run *inside* the image and import modules (drizzle's migrator) the app itself never does, which the tracer cannot see. Schema bootstrap at container start: `docker-entrypoint.sh` refuses to start without `DATABASE_URL`, then `scripts/init-db.mjs` applies the **committed** migrations with drizzle's `migrate()` (ledger `__drizzle_migrations`) — statements are applied verbatim, so a broken migration stops the container rather than silently no-opping.

`docker-compose.yml` bundles a `postgres:17-alpine` service (initdb'd `--encoding=UTF8 --lc-collate=C --lc-ctype=C`, since the seeded prompts carry Unicode Tags) which the app waits for via `depends_on: condition: service_healthy`. State lives in the named volume `pgdata`, so the old `./data` bind mount and its `user: "1001:1001"` uid matching are gone. `POSTGRES_PASSWORD` is required in `.env`; `DATABASE_URL` optionally overrides the bundled database with an external one. `scripts/migrate-sqlite-to-pg.mjs` is the one-time importer from the retired SQLite file (reads it with built-in `node:sqlite`, refuses a non-empty target without `--truncate`, fixes the sequences with `setval`, and asserts the Unicode-Tags payload survived code point for code point). The compose stack joins the **external** network `llm_default`; Caddy (config `/home/baum/llm/caddy/Caddyfile`, separate stack) serves `https://ki01.webix.de/agent-val` via a `handle /agent-val*` block with basic auth, everything else on that host goes to vLLM.

Caddy gotcha: the Caddyfile is bind-mounted as a single file into the caddy container — file-replacing edits (Edit tool, `sed -i`) change the inode, so `caddy reload` still reads the old content ("config is unchanged"). After editing it, `docker restart caddy` is required. Production container actions (`docker compose up`, `docker restart`) must be run by the user.
