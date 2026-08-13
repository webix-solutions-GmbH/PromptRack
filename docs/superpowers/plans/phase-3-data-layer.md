# Phase 3 — Scoped data-access layer

*Historical implementation plan, kept as a record of how the app was built. It describes the app under its former name and may not match the current code.*

Implementation plan. Source spec: `docs/superpowers/specs/2026-08-12-platform-evolution-design.md`
(§ "Phase 3 — Data-access layer"). Architecture context: `CLAUDE.md` (read it — the
**snapshot invariant** and the **content vs. credentials** line are the two rules this
phase must not break).

Executor: an implementor agent with no prior context. Every task names its files, the
exact change, and a verification command. Do the tasks in order; each one ends green
(`npx tsc --noEmit` + `npm test`).

**Every shell command in this plan must be prefixed with**
`export PATH="$HOME/.nvm/versions/node/v22.23.1/bin:$PATH"` — Node 22 is nvm-only and
not on the default PATH.

---

## Risks & open questions (read first)

1. **Dialect.** The spec puts Postgres in Phase 2, so this phase *probably* runs on
   Postgres. It is written **dialect-agnostic** anyway. Before starting, run
   `grep -c sqliteTable src/db/schema.ts`. If > 0 you are on SQLite: import `alias`/
   `index` from `drizzle-orm/sqlite-core`; otherwise from `drizzle-orm/pg-core`. No
   dialect-only SQL is used anywhere in this plan (no `FILTER (WHERE …)`, no
   `GROUP_CONCAT`, no `json_extract`).
2. **`count()` returns bigint on Postgres, number on SQLite.** Always finish a count
   expression with `.mapWith(Number)`. **Never** use `.mapWith(Number)` on `avg()` or
   `sum()` — `Number(null)` is `0`, which would turn "no measurements" into "0 tok/s".
   Use the `num()` helper (Task 12) for nullable aggregates, and
   `coalesce(sum(x), 0)` in SQL where 0 is genuinely right.
3. **Type-level scoping is only *partly* a type error in this phase**, because there is
   no `customer_id` column yet. What this phase buys, and what Phase 5 completes:
   - **Now:** `Scope` is an unforgeable branded type; every repo function takes it as
     its first parameter; `db` becomes un-importable outside `src/db/**` (ESLint,
     Task 20). An unscoped query is therefore a *lint* error now.
   - **Phase 5:** adding `customerId: integer().notNull()` to the five root tables makes
     every insert that omits it a **compile error** via `$inferInsert`, and
     `scopeWhere`/`scopeValues`/`scopeThroughParent` (three functions, one file) turn
     from no-ops into the real predicates. No call site changes.
   Do not try to invent stronger typing now; it would need the column to exist.
4. **No behaviour changes.** This is a refactor. Every page must render byte-identical
   HTML afterwards (Tasks 12/13/14 include before/after diffs). If a query rewrite
   changes a count, a sort order or a tie-break, it is a bug in the rewrite — not an
   improvement.
5. **Sorting stays in JS** on `/runs`. `sortValue()` sorts `machine` by
   `snapshotMachineName(run.machineSnapshot)` (JSON parse), and `speed` by
   `avgRate ?? -1` with a `b.run.id - a.run.id` tie-break. Reproducing that in SQL
   invites silent ordering drift. SQL does the filtering and the aggregation; JS keeps
   the sort over the already-filtered rows.
6. **`revalidatePath` never moves into the repo.** Cache invalidation stays in the
   server actions / route handlers / MCP tools that own the write. The repo is data
   access only — that is what keeps it importable from the background executor.
7. **Open question for the reviewer:** `run-executor.ts` runs *outside* a request
   (MCP `execute_run` is fire-and-forget), so it cannot use a session-derived scope.
   This plan gives it `scopeForRun(runId)` — the one deliberately unscoped lookup,
   which reads the run row and derives the scope *from* it (Task 10). Confirm that is
   the wanted shape before Phase 4 adds authorization on top.
8. **`@/db/schema` stays importable everywhere** — components use
   `typeof runResults.$inferSelect`. Only the `db` *handle* (`@/db`) is restricted.
9. **Vitest has no path aliases** (there is no `vitest.config.ts`; the 210 existing
   tests all use relative imports). Any test added by this plan must use relative
   imports and must not transitively import `src/db/index.ts` (it opens
   `data/app.db` via `better-sqlite3` at import time). `src/db/scope.ts` is written
   db-free precisely so it can be unit-tested.

---

## The contract (design, read before Task 3)

### `src/db/scope.ts` — pure, no `db` import, ever

```ts
import { and, type SQL } from 'drizzle-orm';
import type { machines, promptGroups, runs, systemPrompts, toolsets } from './schema';

declare const scopeBrand: unique symbol;

/**
 * Who a query is allowed to see. Unforgeable: the brand means no caller outside
 * this module can construct one, so "a query without a scope" cannot be written.
 * Phase 3 has one implicit workspace, so `customerId` is always null.
 */
export interface Scope {
  readonly [scopeBrand]: true;
  readonly customerId: number | null;
  /** Where it came from: 'session' (a user), 'row' (derived from a record), 'system'. */
  readonly origin: ScopeOrigin;
}
export type ScopeOrigin = 'session' | 'row' | 'system';

/** The scope of the current request. Async already, so Phase 4's `await cookies()`
 *  changes no call site. Phase 3: the single implicit workspace. */
export function currentScope(): Promise<Scope>;

/** Derived from a row that already carries its own scope (background work). */
export function scopeFromCustomerId(customerId: number | null): Scope;

/** Explicit, grep-able escape hatch for migrations/admin. `reason` is documentation. */
export function systemScope(reason: string): Scope;

/** The five root tables that will carry `customer_id` in Phase 5. */
export type ScopedRootTable =
  | typeof machines | typeof systemPrompts | typeof toolsets
  | typeof promptGroups | typeof runs;

/** Phase 3: undefined. Phase 5: eq(table.customerId, scope.customerId). */
export function scopeWhere(scope: Scope, table: ScopedRootTable): SQL | undefined;

/** Columns a new root row must carry. Phase 3: {}. Phase 5: { customerId }. */
export function scopeValues(scope: Scope): Record<string, never>;

/** `and()` that tolerates undefined and collapses to undefined when empty. */
export function combine(conditions: readonly (SQL | undefined)[]): SQL | undefined;

/** The scope predicate for `table` AND-ed with the caller's own conditions. */
export function whereScoped(
  scope: Scope, table: ScopedRootTable, ...conditions: (SQL | undefined)[]
): SQL | undefined;
```

`drizzle`'s `.where(undefined)` is legal and emits no `WHERE`, which is what makes the
no-op phase free.

### `src/db/repo/scoped.ts` — the child-table rule (may import `db`)

Child tables (`machine_models`, `tools`, `prompts`, `prompt_toolsets`, `run_results`)
inherit scope through their FK. Two mechanisms, both no-ops today:

```ts
/** Restricts a child row to parents in scope. Phase 3: undefined.
 *  Phase 5: inArray(childFk, db.select({id: parentId}).from(parent).where(scopeWhere(...))). */
export function scopeThroughParent(
  scope: Scope, childFk: AnyColumn, parentTable: ScopedRootTable, parentId: AnyColumn,
): SQL | undefined;
```

**Rules the repo must follow (they cost nothing now and are the whole point):**

- A **read** of a child row by a caller-supplied id joins its parent and puts the scope
  predicate on the parent:
  `.innerJoin(runs, eq(runResults.runId, runs.id)).where(whereScoped(scope, runs, eq(runResults.id, id)))`
- An **update/delete** of a child row either carries its parent key in the WHERE
  (`and(eq(runResults.id, id), eq(runResults.runId, runId))` — preferred, the caller
  usually knows the run) or uses `scopeThroughParent`.
- An **insert** into a root table spreads `...scopeValues(scope)`.

### `src/db/repo/*` — one module per subject

| File | Owns |
|---|---|
| `src/db/repo/machines.ts` | `machines`, `machine_models` |
| `src/db/repo/system-prompts.ts` | `system_prompts` |
| `src/db/repo/toolsets.ts` | `toolsets`, `tools` |
| `src/db/repo/prompts.ts` | `prompt_groups`, `prompts`, `prompt_toolsets` |
| `src/db/repo/runs.ts` | `runs`, `run_results` (incl. list aggregate) |
| `src/db/repo/results.ts` | cross-entity reads for `/results` |
| `src/db/repo/scoped.ts` | `scopeThroughParent`, shared SQL helpers (`num`) |

Only these files (plus `src/db/index.ts`) may `import { db } from '@/db'`.
Every exported function takes `scope: Scope` as its **first** parameter. No exceptions
except `scopeForRun` (Task 10), which is documented as the scope *entry point*.

---

## Call-site inventory (23 files, 132 `db.*` sites)

Counted with `\bdb\s*\.\s*(select|insert|update|delete|transaction|query)\b`. Use this
as the checklist; the count must reach 0 outside `src/db/**` at the end.

| Sites | File | Task |
|---:|---|---|
| 21 | `src/lib/mcp/tools-authoring.ts` | 16 |
| 14 | `src/lib/mcp/tools-runs.ts` | 17 |
| 14 | `src/lib/run-executor.ts` | 11 |
| 10 | `src/lib/run-create.ts` | 15 |
| 8 | `src/actions/prompts.ts` | 8 |
| 7 | `src/actions/toolsets.ts` | 7 |
| 6 | `src/app/prompts/page.tsx` | 8 |
| 6 | `src/actions/machines.ts` | 5 |
| 5 | `src/app/results/page.tsx` | 13 |
| 5 | `src/app/api/machines/[id]/discover/route.ts` | 5 |
| 5 | `src/app/api/toolsets/[id]/discover/route.ts` | 7 |
| 5 | `src/actions/runs.ts` | 10 |
| 4 | `src/app/page.tsx` | 14 |
| 4 | `src/app/runs/new/page.tsx` | 9 |
| 3 | `src/app/runs/page.tsx` | 12 |
| 3 | `src/actions/system-prompts.ts` | 6 |
| 2 | `src/app/machines/[id]/page.tsx` | 5 |
| 2 | `src/app/machines/page.tsx` | 5 |
| 2 | `src/app/api/runs/[id]/execute/route.ts` | 10 |
| 2 | `src/app/runs/[id]/page.tsx` | 10 |
| 2 | `src/app/toolsets/page.tsx` | 7 |
| 1 | `src/app/system-prompts/page.tsx` | 6 |
| 1 | `src/app/api/machines/[id]/test/route.ts` | 5 |

---

## Task 1 — Baseline

Record the starting state so later diffs mean something.

1. `npm test` → **210 passed**. `npx tsc --noEmit` → clean. `npm run build` → succeeds.
2. Start the dev server (`npm run dev`) and snapshot the two pages this phase rewrites:
   ```bash
   mkdir -p /tmp/phase3
   curl -s 'http://localhost:3000/agent-val/runs' > /tmp/phase3/runs.before.html
   curl -s 'http://localhost:3000/agent-val/runs?sort=speed&dir=asc' > /tmp/phase3/runs-speed.before.html
   curl -s 'http://localhost:3000/agent-val/results?mode=models' > /tmp/phase3/results-models.before.html
   curl -s 'http://localhost:3000/agent-val/results?mode=runs&runs=1,2' > /tmp/phase3/results-runs.before.html
   curl -s 'http://localhost:3000/agent-val/' > /tmp/phase3/home.before.html
   ```
   (Adjust `runs=1,2` to two run ids that actually exist —
   `sqlite3 data/app.db 'select id from runs limit 5'`, or the Postgres equivalent.)

**Verify:** all five files non-empty and contain a `<tbody`.

---

## Task 2 — Indexes on `run_results`

**File:** `src/db/schema.ts`

Add an index import from the dialect core in use (see Risk 1) and give `runResults` a
config callback — it currently has none:

```ts
export const runResults = sqliteTable('run_results', {
  /* …unchanged columns… */
}, (table) => [
  index('run_results_run_id_idx').on(table.runId),
  index('run_results_prompt_id_idx').on(table.promptId),
]);
```

`run_id` is required by the spec — every list/detail/compare query filters or groups by
it, and the `runs` cascade delete scans it. `prompt_id` is added in the same migration
because model-mode compare and the `ON DELETE SET NULL` from `prompts` both scan it.

Do **not** add an index on `runs.archived_at` — the runs table is small and the column is
low-cardinality; revisit only if a query plan says otherwise.

**Apply it** (which path exists depends on whether Phase 1/2 landed):

- Phase 1 landed: `npx drizzle-kit generate` then the migrate script (`npm run db:init`).
- Otherwise: `npx drizzle-kit generate && node scripts/init-db.mjs`.

**Verify:** the generated SQL file in `drizzle/` contains both
`CREATE INDEX … ON run_results (run_id)` and `… (prompt_id)`; after applying,
`sqlite3 data/app.db '.indexes run_results'` (or `\d run_results` in psql) lists them.
`npx tsc --noEmit` clean.

---

## Task 3 — `src/db/scope.ts` + unit test

**Create `src/db/scope.ts`** exactly as specified in "The contract" above. Implementation
notes:

```ts
function makeScope(customerId: number | null, origin: ScopeOrigin): Scope {
  return { customerId, origin } as unknown as Scope;   // the brand is phantom
}

/** Phase 3 has one implicit workspace; Phase 5 replaces this with a session read. */
const IMPLICIT_SCOPE = makeScope(null, 'session');

export async function currentScope(): Promise<Scope> {
  return IMPLICIT_SCOPE;
}

export function scopeWhere(scope: Scope, table: ScopedRootTable): SQL | undefined {
  void scope; void table;
  // Phase 5: return eq(table.customerId, requireCustomerId(scope));
  return undefined;
}
```

Every no-op body carries the `// Phase 5:` comment showing its future form — that comment
*is* the phase-5 change list.

`combine` must: drop `undefined`s, return `undefined` for none, return the single
condition unwrapped for one, `and(...)` for many.

**Create `src/db/scope.test.ts`** (relative import `./scope`, no db):
- `combine([])` → `undefined`; `combine([undefined, undefined])` → `undefined`.
- `combine([c])` returns the same object identity.
- `combine([a, b])` returns a defined `SQL`.
- `whereScoped(scope, runs)` → `undefined` in this phase (documents the no-op).
- `currentScope()` twice returns the same object (no per-call allocation).
- `systemScope('x').origin === 'system'`.

Use `eq(runs.id, 1)`-style conditions built from `@/db/schema` — but import schema
**relatively** (`./schema`) so vitest resolves it.

**Verify:** `npx vitest run src/db/scope.test.ts` → all pass; `npm test` → 210 + new.

---

## Task 4 — `src/lib/form-data.ts` (dedupe #1) + test

Four copies of the same FormData helpers exist:

| Helper | Copies |
|---|---|
| `optionalString(fd, key)` | `actions/prompts.ts:19`, `actions/runs.ts:12`, `actions/toolsets.ts:9`, `actions/machines.ts:13` |
| `requiredString(fd, key)` | `actions/prompts.ts:10`, `actions/system-prompts.ts:8` |
| `optionalId(fd, key)` | `actions/prompts.ts:26` |
| `optionalNumber(fd, key, label)` | `actions/runs.ts:19` |

**Create `src/lib/form-data.ts`** with all four, bodies copied verbatim (they are already
identical across copies — confirm before deleting):

```ts
/** Trimmed value, or null when absent/blank. */
export function optionalString(formData: FormData, key: string): string | null;
/** Trimmed value; throws `${key} is required.` when absent/blank. */
export function requiredString(formData: FormData, key: string): string;
/** Integer id, or null when absent or not an integer. */
export function optionalId(formData: FormData, key: string): number | null;
/** Finite number, or null when absent; throws `${label} must be a number.` otherwise. */
export function optionalNumber(formData: FormData, key: string, label: string): number | null;
```

Then delete the local copies in the five `src/actions/*.ts` files and import from
`@/lib/form-data`.

**Do not touch** `optionalString` / `requireString` in `src/lib/mcp/args.ts` — same names,
different domain (`ToolArgs`, not `FormData`). They stay separate.

**Create `src/lib/form-data.test.ts`** (relative import): blank → null, whitespace-only →
null, trimming, `requiredString` throws with the key in the message, `optionalId` rejects
`"1.5"` and `"abc"`, `optionalNumber` throws with the *label* (not the key).

**Verify:** `npm test` passes; `grep -rn "function optionalString" src/actions/` → no
matches; `npx tsc --noEmit` clean.

---

## Task 5 — machines repo + its call sites

**Create `src/db/repo/machines.ts`.** Signatures:

```ts
listMachines(scope, order?: 'name' | 'created'): Promise<Machine[]>   // default 'name'
getMachine(scope, id: number): Promise<Machine | null>
createMachine(scope, values: MachineFields & {createdAt; updatedAt}): Promise<{ id: number }>
updateMachine(scope, id, values): Promise<void>
deleteMachine(scope, id): Promise<void>
listMachineModels(scope, opts?: { machineId?: number }): Promise<MachineModel[]>   // desc lastSeenAt
listLoadedModels(scope, machineId): Promise<MachineModel[]>          // currentlyLoaded = true
machineModelCounts(scope): Promise<Map<number, { total: number; loaded: number }>>
touchMachineModel(scope, args: { machineId; modelId; source: 'manual' | 'run'; at: number }): Promise<void>
syncDiscoveredModels(scope, machineId, modelIds: string[]): Promise<{ discovered: number; retired: number }>
```

- `machineModelCounts` is a **SQL GROUP BY**, replacing the JS `models.filter(...)` per
  machine in `src/app/machines/page.tsx:20`:
  ```ts
  const total = sql<number>`count(*)`.mapWith(Number);
  const loaded = sql<number>`count(case when ${machineModels.currentlyLoaded} then 1 end)`.mapWith(Number);
  // group by machineModels.machineId; join machines for the scope predicate
  ```
  On SQLite `currently_loaded` is stored as 0/1, so use
  `case when ${machineModels.currentlyLoaded} = 1 then 1 end` there; on Postgres the
  boolean form is right. Pick per Risk 1.
- `touchMachineModel` is the shared upsert body currently duplicated in
  `actions/machines.ts:92-113` (`source: 'manual'`) and `run-create.ts:251-272`
  (`source: 'run'`): select by (machineId, modelId); if found bump `lastSeenAt` only
  (never touch `currentlyLoaded` or `source`); else insert with
  `currentlyLoaded: false, firstSeenAt: at, lastSeenAt: at`.
- `syncDiscoveredModels` is the whole upsert-and-retire body of
  `src/app/api/machines/[id]/discover/route.ts:66-104`, moved verbatim: discovered rows
  get `lastSeenAt` + `currentlyLoaded: true`; previously-seen rows absent from the list
  get `currentlyLoaded: false`; **nothing is ever deleted** (history invariant).
- `machine_models` is a child table → its reads join `machines` for the scope predicate
  (or filter by a machineId already validated through `getMachine`).

**Rewire (6 files):**

| File | Change |
|---|---|
| `src/actions/machines.ts` | `const scope = await currentScope();` at the top of each action; `createMachine/updateMachine/deleteMachine/touchMachineModel`. Keep `redirect()` and `revalidatePath()` in place. |
| `src/app/machines/page.tsx` | `getMachinesWithCounts()` → `listMachines(scope, 'created')` + `machineModelCounts(scope)`; keep the `{ machine, total, loaded }` shape the JSX destructures. |
| `src/app/machines/[id]/page.tsx` | `getMachine` (→ `notFound()` on null) + `listMachineModels(scope, { machineId: id })`. |
| `src/app/api/machines/[id]/test/route.ts` | `getMachine`. |
| `src/app/api/machines/[id]/discover/route.ts` | `getMachine` + `syncDiscoveredModels`; the route keeps the fetch, the parsing, the error responses and both `revalidatePath` calls. Response body stays `{ ok, discovered, models }`. |
| `src/app/runs/new/page.tsx` | `listMachines(scope, 'name')` + `listMachineModels(scope)` (Task 9 finishes this file). |

**Verify:**
- `grep -c "from '@/db'" src/actions/machines.ts src/app/machines/page.tsx "src/app/machines/[id]/page.tsx" "src/app/api/machines/[id]/test/route.ts" "src/app/api/machines/[id]/discover/route.ts"` → all 0.
- `npx tsc --noEmit` clean; `npm test` green.
- Dev server: `/agent-val/machines` still lists every machine with the same
  "N models (M loaded)" numbers as before; open a machine, press **Discover**, confirm
  the toast count matches and that a model removed from the endpoint flips to not-loaded
  rather than disappearing.

---

## Task 6 — system-prompts repo + its call sites

**Create `src/db/repo/system-prompts.ts`:**

```ts
listSystemPrompts(scope, order: 'name' | 'updated'): Promise<SystemPrompt[]>
getSystemPrompt(scope, id): Promise<SystemPrompt | null>
listSystemPromptsByIds(scope, ids: number[]): Promise<SystemPrompt[]>   // [] → [] without a query
createSystemPrompt(scope, { name, content, now }): Promise<{ id: number }>
updateSystemPrompt(scope, id, { name, content, now }): Promise<void>
deleteSystemPrompt(scope, id): Promise<void>
```

`listSystemPromptsByIds` exists for Task 15 (the `createRunRecord` fix). It must return
`[]` for an empty id list **without** issuing a query — `inArray(col, [])` is a
correctness and portability trap.

**Rewire:** `src/actions/system-prompts.ts` (all three actions),
`src/app/system-prompts/page.tsx` (`listSystemPrompts(scope, 'updated')`).

**Verify:** those two files have zero `from '@/db'`; `/agent-val/system-prompts` renders
the same list in the same order (most recently updated first); create/edit/delete still
work; `npx tsc --noEmit` clean.

---

## Task 7 — toolsets/tools repo + its call sites

**Create `src/db/repo/toolsets.ts`:**

```ts
listToolsets(scope): Promise<Toolset[]>                       // asc name
getToolset(scope, id): Promise<Toolset | null>
createToolset(scope, fields & { now }): Promise<{ id: number }>
updateToolset(scope, id, fields & { now }): Promise<void>
deleteToolset(scope, id): Promise<void>
listTools(scope, opts?: { toolsetIds?: number[] }): Promise<Tool[]>   // asc name
createTool(scope, toolsetId, fields & { now }): Promise<void>
updateTool(scope, id, fields & { now }): Promise<void>
deleteTool(scope, id): Promise<void>
setToolEnabled(scope, id, enabled: boolean): Promise<void>
syncDiscoveredTools(scope, toolsetId, discovered: McpToolDescriptor[]):
  Promise<{ discovered: number; retired: number }>
listMcpServers(scope, toolsetIds: number[]):
  Promise<{ id: number; mcpUrl: string | null; mcpHeaders: string | null }[]>
```

- **`createTool`/`updateTool` must let the driver's UNIQUE-violation error propagate
  unchanged.** `actions/toolsets.ts:116` (`describeToolWriteError`) matches on
  `/UNIQUE constraint failed/i` to produce a human message. Do not wrap or re-throw
  inside the repo. (Note for Phase 2 reviewers: the Postgres message differs — that is a
  Phase 2 concern, not this one.)
- `syncDiscoveredTools` moves the body of
  `src/app/api/toolsets/[id]/discover/route.ts:52-84` verbatim: upsert
  `description`/`parametersJson`/`enabled: true`/`lastSeenAt`, **never touch
  `mockResponse`** (a hand-written canned response survives discovery), and disable —
  never delete — `source: 'mcp'` rows absent from the response.
- `listMcpServers` serves `run-executor.ts:84-87`. It reads `mcp_url` / `mcp_headers`
  **live** — credentials, deliberately not snapshotted (CLAUDE.md, content vs.
  credentials). Keep that comment with the function.
- `tools` is a child table → reads by tool id join `toolsets`; writes carry
  `toolsetId` in the WHERE where the caller has it.

**Rewire:** `src/actions/toolsets.ts` (7 sites), `src/app/toolsets/page.tsx`
(`listToolsets` + `listTools`), `src/app/api/toolsets/[id]/discover/route.ts`
(`getToolset` + `syncDiscoveredTools`; the route keeps the MCP call, the
"not backed by an MCP server" refusal, both `revalidatePath`s and the response shape).

**Verify:** those three files have zero `from '@/db'`. Against the dev mock MCP server:
register/point a toolset at `http://localhost:3000/agent-val/api/mock-mcp`, press
Discover → `{ok:true, discovered:2}`; re-run with `?hide=add_numbers` → `retired:1` and
the tool is **disabled, still present** on `/agent-val/toolsets`. Creating a tool whose
name already exists in the toolset still shows *"This toolset already has a tool called
…"*, not a raw driver error.

---

## Task 8 — prompts repo + its call sites (dedupe #2)

**Create `src/db/repo/prompts.ts`:**

```ts
// groups
listGroups(scope, order: 'sort-name' | 'sort-id'): Promise<PromptGroup[]>
getGroup(scope, id): Promise<PromptGroup | null>
listGroupsByIds(scope, ids: number[]): Promise<PromptGroup[]>     // asc sortOrder, id
createGroup(scope, { name, description, now }): Promise<{ id: number }>
updateGroup(scope, id, { name, description }): Promise<void>
deleteGroup(scope, id): Promise<void>
promptCountsByGroup(scope): Promise<Map<number, number>>          // SQL GROUP BY
countPrompts(scope): Promise<number>                              // SQL count, dashboard

// prompts
listPrompts(scope, opts?: { groupId?: number; groupIds?: number[] }): Promise<Prompt[]>
getPrompt(scope, id): Promise<Prompt | null>
createPrompt(scope, values): Promise<{ id: number }>
updatePrompt(scope, id, values: Partial<NewPrompt>): Promise<void>
deletePrompt(scope, id): Promise<void>
comparePromptRows(scope): Promise<ComparePromptView[]>            // Task 13

// prompt_toolsets
replaceToolsetLinks(scope, promptId, toolsetIds: number[]): Promise<void>
listToolsetLinks(scope, promptIds?: number[]): Promise<PromptToolset[]>   // asc sortOrder
listPromptToolsetViews(scope, promptIds: number[]):
  Promise<{ promptId; toolsetId; name; kind; sortOrder }[]>       // Task 16
listSnapshotToolRows(scope, promptIds: number[]): Promise<SnapshotToolRow[]>  // Task 15
```

- **`replaceToolsetLinks` is dedupe #2** — the identical function currently lives at
  `src/actions/prompts.ts:73` *and* `src/lib/mcp/tools-authoring.ts:144`. Body: delete
  all links for the prompt, return early on an empty list, else insert with
  `sortOrder: index`. Delete both copies.
- `listPrompts` ordering: with `groupId` → `asc(sortOrder), asc(id)`; with `groupIds` →
  same (the caller groups); with neither → `asc(groupId), asc(sortOrder), asc(id)`
  (what `tools-authoring.ts:461` does today). Keep all three orderings — they are
  load-bearing for run creation order.
- `promptCountsByGroup` replaces the JS count loops in `prompts/page.tsx:31` and
  `runs/new/page.tsx:21`.
- `comparePromptRows` is the `prompts ⋈ prompt_groups` select from
  `results/page.tsx:208-224`, ordering unchanged
  (`groupGroups.sortOrder, groups.name, prompts.sortOrder, prompts.id`).
- `listSnapshotToolRows` is the join from `run-create.ts:66-87` — see Task 15; put it
  here because it is a prompt→toolset→tool traversal.

**Rewire:** `src/actions/prompts.ts` (8 sites; also switch to `@/lib/form-data` if Task 4
left anything), `src/app/prompts/page.tsx` (6 sites: `listGroups(scope,'sort-name')`,
`listPrompts(scope)`, `listSystemPrompts(scope,'name')`, `listToolsets(scope)`,
`listTools(scope)`, `listToolsetLinks(scope)`).

**Verify:** both files have zero `from '@/db'`;
`grep -rn "async function replaceToolsetLinks" src/` → exactly **one** hit, in
`src/db/repo/prompts.ts`. On the dev server: `/agent-val/prompts` shows the same groups,
the same per-group counts and the same prompt list; editing a prompt's toolsets still
persists and the "duplicate tool name" refusal still fires.

---

## Task 9 — `/runs/new`

**File:** `src/app/runs/new/page.tsx` (4 sites)

Replace with `listMachines(scope, 'name')`, `listMachineModels(scope)` (ordered
`desc(currentlyLoaded), desc(lastSeenAt)` — keep it, the "Currently loaded" optgroup
depends on it), `listGroups(scope, 'sort-name')`, `promptCountsByGroup(scope)`.

**Verify:** zero `from '@/db'`; `/agent-val/runs/new` shows the same machines, the same
grouped model list (loaded first) and the same per-group prompt counts. Switching machine
still triggers discovery and auto-selects a single loaded model.

---

## Task 10 — runs repo (core) + run detail, execute route, run actions

**Create `src/db/repo/runs.ts`** (the aggregate half comes in Task 12):

```ts
// runs
getRun(scope, id): Promise<Run | null>
listRuns(scope, opts?: { status?: string; archived?: 'exclude' | 'only' | 'all';
                         runIds?: number[]; limit?: number }): Promise<Run[]>  // desc createdAt, id
createRun(scope, values: NewRun): Promise<{ id: number }>
updateRunStatus(scope, id, values: Pick<NewRun,'status'|'startedAt'|'finishedAt'>): Promise<void>
updateRunComment(scope, id, comment: string | null): Promise<void>
setRunArchivedAt(scope, id, archivedAt: number | null): Promise<void>
deleteRun(scope, id): Promise<void>
countArchivedRuns(scope): Promise<number>

/** THE scope entry point for background work: reads the run row and derives the
 *  scope it belongs to. The only function here that is not itself scoped —
 *  documented, and the only one Phase 4 has to authorize. */
scopeForRun(runId: number): Promise<{ scope: Scope; run: Run } | null>

// run_results
insertRunResults(scope, runId, rows: NewRunResult[]): Promise<void>
listRunResults(scope, runId): Promise<RunResult[]>            // asc sortOrder, id
getRunResult(scope, id): Promise<RunResult | null>            // joins runs for scope
listResultStatuses(scope, runId): Promise<{ id: number; status: string }[]>
countPendingResults(scope, runId): Promise<number>
updateRunResult(scope, runId, resultId, values: Partial<NewRunResult>): Promise<void>
resetResultsInStatus(scope, runId, status: string, values: Partial<NewRunResult>): Promise<void>
rateResult(scope, resultId, values: { rating: Rating | null; ratingNote?: string | null }):
  Promise<{ runId: number } | null>
listResultRatings(scope, runId): Promise<{ rating: string | null }[]>
```

Notes:
- `updateRunResult` takes **both** `runId` and `resultId` and puts both in the WHERE.
  That is the child-scoping rule, and it costs nothing — the executor always knows the
  run.
- `rateResult` only has a result id (UI + MCP), so it uses `scopeThroughParent`
  (no-op now) and returns the row's `runId` via `.returning(...)` for revalidation, or
  `null` when nothing matched.
- `resetResultsInStatus` carries the `RESET_TO_PENDING` payload from
  `run-executor.ts:125` — leave that constant in the executor and pass it in; it is
  execution policy, not data access.

**Rewire:**

| File | Change |
|---|---|
| `src/app/runs/[id]/page.tsx` | `getRun` (→ `notFound()`), `listRunResults`. |
| `src/app/api/runs/[id]/execute/route.ts` | `getRun` + `countPendingResults`; keep the 400/404/409 responses, the NDJSON stream and `request.signal` wiring untouched. |
| `src/actions/runs.ts` | `updateRunComment`, `rateResult`, `updateResultNote` (a `rateResult` call with only `ratingNote`, or its own repo function — keep the existing semantics: omitting `note` must leave an existing note untouched), `setRunArchivedAt`, `deleteRun`. Keep both `isRunExecuting` guards and every `revalidatePath`. |

**Verify:** those three files have zero `from '@/db'`. On the dev server, against the
mock LLM machine (`http://localhost:3000/agent-val/api/mock-llm`): create a run, start
it, rate a result good/meh/bad, edit the note, archive and unarchive, delete. The rating
buttons must not wipe an existing note. `npm test` green.

---

## Task 11 — `run-executor.ts`

**File:** `src/lib/run-executor.ts` (14 sites — the highest-risk file in this phase; the
abort/reclaim/resume state machine is untested, per the spec's Testing section).

Change only the data access:

1. At the top of `executeRun`, replace the run lookup with the scope entry point:
   ```ts
   const found = await scopeForRun(runId);
   if (!found) throw new Error(`Run ${runId} not found.`);
   const { scope, run } = found;
   ```
   This is what makes the executor work outside a request (MCP `execute_run` is
   fire-and-forget) and still be scoped in Phase 5 — the scope comes from the row.
2. Map the remaining sites:
   - stale-`running` reclaim → `resetResultsInStatus(scope, runId, 'running', RESET_TO_PENDING)`
   - result list → `listResultStatuses(scope, runId)`
   - machine lookup → `getMachine(scope, run.machineId)` (keep the snapshot-URL
     fallback exactly as it is — a moved endpoint must not break Resume)
   - `runs` status writes → `updateRunStatus`
   - per-result select → `getRunResult(scope, resultId)`
   - all four per-result writes (claim, ok, abort-rollback, error) →
     `updateRunResult(scope, runId, resultId, …)`
   - remaining-pending check → `countPendingResults(scope, runId)`
   - `buildMcpExecutor` → `listMcpServers(scope, toolsetIds)`; thread `scope` in as a
     parameter (`buildMcpExecutor(scope, snapshot)`).

**Do not touch:** `RESET_TO_PENDING`'s contents, the `executing` Set guard, the abort
path, the `everythingUnreachable` / `failed` rule, event emission order, or the
`tokens_per_sec` maths.

**Verify:**
- zero `from '@/db'` in the file; `npx tsc --noEmit` clean; `npm test` green.
- Mock-LLM end-to-end, all four paths:
  1. A run of ≥3 prompts completes; results appear one by one; `status = completed`.
  2. A prompt containing `TRIGGER_ERROR` marks **that row** `error` and the run still
     ends `completed`.
  3. A run with `TRIGGER_SLOW`: close the tab mid-run → the in-flight row is back to
     `pending` (not `running`), run status `pending`, and **Resume** finishes it.
  4. A prompt with `TRIGGER_TOOL_LOOP` and `tool_mode: execute` stops at `max_turns`
     with `stopped_reason = max_turns`.
- A run against a deleted machine still executes from the snapshot base URL.

---

## Task 12 — `/runs` list: real WHERE clauses

**Files:** `src/db/repo/runs.ts` (add), `src/app/runs/page.tsx` (rewrite the data half).

Today the page selects **every** run, **every** `run_results` row and **every** machine,
then filters in JS and runs `resultRows.filter(...)` once per run — O(runs × results).

**Add to the repo:**

```ts
export interface RunListFilter {
  archived: 'exclude' | 'only' | 'all';
  machineId: number | null;
  modelId: string | null;
  groupName: string | null;
  status: string | null;
}

export interface RunSummaryRow {
  run: Run;
  groupNames: string[];
  ok: number; error: number; pending: number;
  good: number; meh: number; bad: number; unrated: number;
  avgRate: number | null;
  totalDurationMs: number;
}

listRunSummaries(scope, filter: RunListFilter): Promise<RunSummaryRow[]>
runFilterOptions(scope): Promise<{
  machines: { id: number; name: string }[];
  models: string[];
  groups: string[];
}>
```

`listRunSummaries` implementation:

1. **Group filter first, as its own query** (avoids a correlated subquery and a table
   alias, and is dialect-neutral):
   ```ts
   const groupRunIds = filter.groupName === null ? null
     : (await db.selectDistinct({ runId: runResults.runId }).from(runResults)
          .where(eq(runResults.groupName, filter.groupName))).map(r => r.runId);
   if (groupRunIds !== null && groupRunIds.length === 0) return [];
   ```
2. **One aggregate query**, `runs LEFT JOIN run_results ON run_results.run_id = runs.id`,
   `GROUP BY runs.id`:
   ```ts
   const ok      = sql<number>`count(case when ${runResults.status} = 'ok' then 1 end)`.mapWith(Number);
   const errored = sql<number>`count(case when ${runResults.status} = 'error' then 1 end)`.mapWith(Number);
   const pending = sql<number>`count(case when ${runResults.status} in ('pending','running') then 1 end)`.mapWith(Number);
   const good    = sql<number>`count(case when ${runResults.rating} = 'good' then 1 end)`.mapWith(Number);
   const meh     = sql<number>`count(case when ${runResults.rating} = 'meh'  then 1 end)`.mapWith(Number);
   const bad     = sql<number>`count(case when ${runResults.rating} = 'bad'  then 1 end)`.mapWith(Number);
   const total   = sql<number>`count(${runResults.id})`.mapWith(Number);
   const avgRate = sql<number | null>`avg(${runResults.tokensPerSec})`;          // NO mapWith
   const totalMs = sql<number>`coalesce(sum(coalesce(${runResults.durationMs}, 0)), 0)`.mapWith(Number);
   ```
   **`unrated = total - good - meh - bad`**, computed in TS. This — not
   `count(case when rating is null …)` — is what preserves `countRatings`' rule that any
   *unrecognised* stored rating reads as unrated (`parseRating`, `rating.ts:24`). A
   legacy value must never vanish from the totals.
   `GROUP BY runs.id` while selecting other `runs` columns is valid on both Postgres
   (functional dependency on the PK) and SQLite.
3. WHERE: `whereScoped(scope, runs, …)` with
   `isNull(runs.archivedAt)` / `isNotNull(runs.archivedAt)` / nothing (per `archived`),
   `eq(runs.machineId, …)`, `eq(runs.modelId, …)`, `eq(runs.status, …)`,
   `inArray(runs.id, groupRunIds)`.
   Note the machine filter's current semantics: `String(run.machineId ?? '') === filter`,
   i.e. a machine id of `''` never matches — so only a numeric filter maps to
   `eq(runs.machineId, n)`; a non-numeric value returns `[]`.
4. Order `desc(runs.createdAt), desc(runs.id)` (the page re-sorts in JS anyway; keeping
   it makes the default sort a no-op reorder).
5. `groupNames`: one follow-up
   `selectDistinct({ runId, groupName }).from(runResults).where(inArray(runResults.runId, ids))`
   over the rows just returned, bucketed into a Map. Preserve the current dedup + first-seen
   order.

`runFilterOptions`: `listMachines(scope,'name')` for the names, plus
`selectDistinct({ modelId })` from `runs` in scope and `selectDistinct({ groupName })`
from `run_results` joined to `runs` in scope. The current page derives these from **all**
runs (not the filtered set) — keep that, or the filter bar would erase its own options.

**Page rewrite:** keep `firstParam`, `excerpt`, `sortValue`, the sort call and every line
of JSX unchanged. Replace only the three `db` calls and the `allRows`/`filteredRows`
construction with `listRunSummaries(scope, filter)` + `runFilterOptions(scope)` +
`countArchivedRuns(scope)`. `archivedCount` must keep counting **all** archived runs, not
the filtered ones.

**Verify:**
```bash
curl -s 'http://localhost:3000/agent-val/runs' > /tmp/phase3/runs.after.html
diff <(sed -n '/<tbody/,/<\/tbody>/p' /tmp/phase3/runs.before.html) \
     <(sed -n '/<tbody/,/<\/tbody>/p' /tmp/phase3/runs.after.html)
```
→ **no differences**. Repeat for `?sort=speed&dir=asc` and for at least one of each
filter (`?status=completed`, `?archived=only`, `?archived=all`, `?model=<id>`,
`?group=<name>`, `?machineId=<id>`). Zero `from '@/db'` in the page.

---

## Task 13 — `/results`: real WHERE clauses + scope-safe text fallback

**Files:** `src/db/repo/results.ts` (new), `src/app/results/page.tsx`,
`src/lib/compare.ts`, `src/lib/compare.test.ts`.

Today the page loads **all** runs and **all** `run_results` (6 columns, every row in the
database) on every request, in both modes, and does `summaryRows.filter(r => r.runId === run.id)`
inside a `.map` over every run.

**Create `src/db/repo/results.ts`:**

```ts
/** Run-mode picker rows: one aggregate per run, no per-result shipping. */
listComparableRuns(scope): Promise<{
  run: Run; ok: number; error: number; good: number; meh: number; bad: number;
  avgRate: number | null;
}[]>                                   // same aggregate helpers as Task 12

/** Distinct result group names for the given runs. */
runGroupNames(scope, runIds: number[]): Promise<Map<number, string[]>>

/** Inputs for `buildModelColumns` — narrowed in SQL to what it actually uses:
 *  non-archived runs, and only their `ok` results. Output is identical because
 *  buildModelColumns already skips archived runs and non-ok results itself. */
modelColumnInputs(scope): Promise<{
  runs: { id; machineId; machineSnapshot; modelId; createdAt }[];
  results: ModelColumnResult[];
}>

/** Run-mode cells: results of the selected runs, joined to their run for
 *  `runs.created_at` / `runs.params` (kills the all-runs query). */
compareCellsForRuns(scope, runIds: number[]):
  Promise<{ result: RunResult; runCreatedAt: number; runParams: string | null }[]>

/** Model-mode cells: `ok`+`error` results of non-archived runs, filtered to the
 *  selected (machineId, modelId) PAIRS and, when a group filter is active, to the
 *  prompts in scope. */
compareCellsForModels(scope, columns: { machineId: number | null; modelId: string }[],
                      promptIds: number[] | null):
  Promise<{ result: RunResult; runCreatedAt; runParams; machineId; modelId }[]>
```

`compareCellsForModels` fixes a real over-fetch: the current query filters on
`inArray(runs.modelId, modelIds)` only, so the same model on *other* machines is loaded
and then discarded by `columnKey`. Build the pair predicate:

```ts
or(...columns.map(c => c.machineId === null
  ? and(isNull(runs.machineId), eq(runs.modelId, c.modelId))
  : and(eq(runs.machineId, c.machineId), eq(runs.modelId, c.modelId))))
```

**Page rewrite — branch by mode, so each mode issues only its own queries:**

- `mode === 'runs'`: `listComparableRuns` + `runGroupNames` → `comparableRuns`
  (keep the `ok > 0 || error > 0 || status === 'completed'` filter and the
  `parseGroupNames(run.groupNames) ∪ result group names` union, in JS, over the
  aggregate rows); then `compareCellsForRuns(scope, selectedRunIds)` when
  `selectedRunIds.length >= MIN_COMPARE_RUNS`. **The `runRows`/`runById` query
  disappears** — `toCell` takes `{createdAt, params}` straight off the joined row.
- `mode === 'models'`: `comparePromptRows(scope)` (Task 8) + `modelColumnInputs(scope)`
  → `buildModelColumns` (unchanged, still passing `archived: false` for every run) +
  `compareCellsForModels(scope, selectedPairs, scopedPromptIds)`. Pass
  `scopedPromptIds` only when a `?group=` filter narrowed the prompts; otherwise `null`.
- `hiddenArchivedCount` is only rendered in run mode — compute it only there.

**`compare.ts` — keep the text fallback inside one scope** (spec: "compare's
`prompt_text` fallback matching must not match across customers"):

1. Add to `CompareCellView`:
   ```ts
   /** Opaque workspace key. Phase 3: '' for every cell. Phase 5: the customer id.
    *  The deleted-prompt text fallback only matches within one key, so two
    *  customers' identical prompts can never collapse into one row. */
   scopeKey: string;
   ```
2. In `buildCompareMatrix`, key `byText` on `` `${result.scopeKey} ${textKey(result.promptText)}` ``
   instead of the bare text key. `promptId` matching is unaffected (ids are global).
3. `toCell` in `results/page.tsx` sets `scopeKey: ''` — from `scope.customerId ?? ''`
   in Phase 5, so write it as a value derived from the scope, not a literal.
4. `compare.test.ts`: add `scopeKey: ''` to the `cell()` factory defaults (one line), and
   add a test: two `promptId: null` cells with **identical** `promptText` and *different*
   `scopeKey` produce **two** rows, while the same pair with equal `scopeKey` produces one
   (the existing "deleted prompt matches by text" test already covers the second half).

**Verify:**
```bash
for q in 'mode=models' 'mode=runs&runs=1,2'; do
  curl -s "http://localhost:3000/agent-val/results?$q" > "/tmp/phase3/results-$q.after.html"
done
```
diff the `<tbody>` slices against the Task-1 snapshots → identical. Also check a model
selection (`?mode=models&model=<machineId>|<modelId>`) and a group filter render the same
matrix, the same "N prompts × M models" header, the same per-column good/meh/bad tallies
and the same "superseded" notes. `npm test` → 210 + new compare test. Zero `from '@/db'`
in the page.

---

## Task 14 — dashboard

**File:** `src/app/page.tsx` (4 sites)

Currently loads all runs, all machines, all prompts and all result rows.

Replace with:
- `listRunSummaries(scope, { archived: 'exclude', … all null })` — the recent-runs table
  needs exactly `ok`, `error`, `good/meh/bad` and `avgRate` per run, which that aggregate
  already returns; take the first 10.
- `countArchivedRuns(scope)`, `listMachines(scope)` (or a `countMachines`),
  `countPrompts(scope)`.
- The three rating stat cards are totals over **non-archived** runs' results: add
  `ratingTotals(scope, { archived: 'exclude' })` to `src/db/repo/runs.ts`, one aggregate
  query returning `{ good, meh, bad, unrated }` with the same
  `unrated = total - good - meh - bad` rule as Task 12.

**Verify:** `diff` the `<tbody>` slice of `/agent-val/` against `home.before.html` →
identical; the six stat cards show the same numbers as before. Zero `from '@/db'`.

---

## Task 15 — `run-create.ts` (snapshot invariant + the system-prompt leak)

**File:** `src/lib/run-create.ts` (10 sites). **This file holds the snapshot invariant.**
Change *where the data comes from*, never *what is frozen*.

1. `createRunRecord(input)` gains a scope. Preferred signature:
   `createRunRecord(scope: Scope, input: CreateRunInput)` — both callers
   (`src/actions/runs.ts:84`, `src/lib/mcp/tools-runs.ts:219`) already have or can get
   one via `await currentScope()`.
2. Map the sites: `getMachine`, `listGroupsByIds`, `listPrompts(scope, { groupIds })`,
   `listSnapshotToolRows`, `createRun`, `insertRunResults`, `touchMachineModel`.
3. **The fix:** line 153 currently does `db.select().from(systemPrompts)` — *every*
   system prompt in the database — to build a lookup map. Replace with:
   ```ts
   const systemPromptIds = [...new Set(
     promptRows.map(p => p.systemPromptId).filter((id): id is number => id !== null))];
   const systemPromptRows = await listSystemPromptsByIds(scope, systemPromptIds);
   ```
   (`listSystemPromptsByIds` returns `[]` for an empty list without querying.) The
   `systemPromptById` map and every downstream use stay exactly as they are.
4. **Batch the result inserts.** Line 227 inserts one row per prompt inside a nested
   loop. Build the array first and hand it to `insertRunResults(scope, runId, rows)` in
   one statement. Keep `sortOrder` assignment (`sortOrder++` across groups in group order,
   then prompt order) byte-identical, and keep `resultCount` = the number of rows.
   *(Phase 2 wraps this in a transaction; do not add one here.)*

**Everything else stays:** the "tool test with no enabled tools" refusal and the tool-name
collision refusal fire **before** anything is written; `probeLlmInfo` is still best-effort;
`machineSnapshot`, `groupNames`, `promptText`, `expectedOutput`, the resolved
`systemPromptText` and `toolsSnapshot` are frozen exactly as before.

**Verify:**
- Create a run from `/runs/new` over a group with prompts that use a base system prompt in
  both `append` and `override` mode. Then **edit that system prompt's content** and reload
  the run detail page: the frozen `systemPromptText` must be unchanged. Delete the prompt:
  the run still renders its text.
- A group containing a `tool_mode: execute` prompt whose toolset has no enabled tools is
  still refused, naming the prompt, and **creates no run row**
  (`select count(*) from runs` unchanged).
- The new run's `run_results` count equals the number of prompts, in the same order as
  before (`select id, sort_order, prompt_title from run_results where run_id = <new>`).

---

## Task 16 — `src/lib/mcp/tools-authoring.ts` (21 sites)

Every handler gets `const scope = await currentScope();` as its first line, then calls the
repo. Map:

| Current | Replacement |
|---|---|
| `allGroups()` | `listGroups(scope, 'sort-id')` |
| `allSystemPrompts()` | `listSystemPrompts(scope, 'name')` |
| `allToolsets()` | `listToolsets(scope)` |
| `resolveToolsets` tool fetch | `listTools(scope, { toolsetIds })` |
| `replaceToolsetLinks` (local copy, line 144) | **delete**; use the repo's (Task 8) |
| `promptViews` links join | `listPromptToolsetViews(scope, promptIds)` |
| `promptViewById` | `getPrompt(scope, id)` |
| `listPromptGroups` prompt count | `promptCountsByGroup(scope)` |
| `createPromptGroup` insert | `createGroup(scope, …)` |
| `createSystemPrompt` / `updateSystemPrompt` | the system-prompts repo |
| `listToolsets` tool fetch | `listTools(scope)` |
| `listPrompts` (both branches) | `listPrompts(scope, { groupId })` / `listPrompts(scope)` |
| `createPrompt` / `updatePrompt` / `deletePrompt` | the prompts repo |
| `updatePrompt` existing-links read | `listToolsetLinks(scope, [id])` |

Threading `scope` through the internal helpers (`resolveGroup`, `resolveSystemPrompt`,
`resolveToolsets`, `assertToolConfig`, `promptViews`, `promptViewById`) means adding it as
their first parameter. Do not change `McpToolSpec.handler`'s signature — each handler
fetches its own scope. (Phase 4 will replace `currentScope()` with the API token's scope;
one line per handler, which is why it stays local.)

**Unchanged:** every tool description string, `McpToolError` messages, `assertToolConfig`
validation order (it must still refuse *before* any write), `create_prompt_group`'s
name-idempotency, `revalidateAuthoring()` placement.

**Verify:** `npm test` (`src/lib/mcp/args.test.ts`, `protocol.test.ts` must stay green).
Then against the dev server with `MCP_API_KEY` set, using an MCP client or plain curl:
`tools/list` returns the same 18 tools with the same schemas; `create_prompt_group` twice
with one name returns `created: false` the second time; `create_prompt` with two toolsets
that share a tool name is refused with the collision message; `update_prompt` with only
`{prompt_id, title}` leaves toolsets and tool mode untouched.

---

## Task 17 — `src/lib/mcp/tools-runs.ts` (14 sites)

Same pattern. Map:

| Current | Replacement |
|---|---|
| `loadRun` | `getRun(scope, runId)` (keep the `No run with id` `McpToolError`) |
| `list_machines` | `listMachines(scope)` + `listMachineModels(scope)` |
| `create_run` machine/group resolution | `listMachines(scope)` / `listGroups(scope, 'sort-id')` |
| `create_run` loaded-model probe | `listLoadedModels(scope, machine.id)` |
| `create_run` | `createRunRecord(scope, {…})` (Task 15's signature) |
| `execute_run` pending check | `listResultStatuses(scope, runId)` |
| `list_runs` | `listRuns(scope, {status, archived})` + a per-run aggregate |
| `get_run` | `listRunResults(scope, runId)` |
| `get_run_result` | `getRunResult(scope, id)` |
| `set_rating` | `getRunResult(scope, id)` → guard → `rateResult(scope, …)` → `listResultRatings(scope, runId)` |

`list_runs` currently selects all runs, filters `archived === 'only'` and the model
substring in JS, then `.slice(0, limit)`, and fetches **all** `run_results` for the
tallies. Push what SQL can do: `status`, `archived` (all three values), and
`inArray(runs.id, …)`/limit; keep the `model` substring filter in JS **or** use `like` —
if you use `like`, escape `%` and `_` in the user value, and keep it case-insensitive
(`lower(model_id) like lower(…)`) to match today's `toLowerCase().includes()`. Then fetch
tallies only for the runs actually returned (`listRunSummaries`-style aggregate restricted
by `inArray(runs.id, ids)`).

**`set_rating`'s two guards must survive verbatim:** a `pending`/`running` row is refused
with the "still {status}, nothing to judge yet" message; omitting `note` leaves the
existing note untouched (`hasKey`, not "optionalText is null"); `unrated` clears.

**Verify:** `npm test`. Then the wired check from CLAUDE.md — copy `data/app.db` to a
scratch dir, run vitest from there with `resolve.alias` mapping `@` into this repo and
`vi.mock('next/cache')` — re-run the 9 `set_rating` cases (advertised schema, unknown id,
pending guard, bad/missing enum, note-preserve, note-clear, `unrated`). Plus live:
`create_run` → `execute_run` → poll `get_run` until `completed` → `get_run_result` →
`set_rating` with a note → the note shows in the UI.

---

## Task 18 — sweep: no `db` outside the repo

`grep -rn "from '@/db'" src --include=*.ts --include=*.tsx | grep -v "^src/db/"`
→ **no output**. If anything remains, it belongs to a task above; finish it there rather
than adding a one-off import.

`grep -rnE "\bdb\s*\.\s*(select|insert|update|delete|transaction)" src | grep -v "^src/db/"`
→ **no output**.

---

## Task 19 — repo hygiene pass

Read every file under `src/db/repo/` once, top to bottom, and check:

1. Every exported function takes `scope: Scope` first (only `scopeForRun` is exempt, and
   its doc comment says why).
2. Every root-table read goes through `whereScoped(scope, table, …)`; every root-table
   insert spreads `...scopeValues(scope)`.
3. Every child-table read by caller-supplied id joins its parent; every child write
   carries its parent key or `scopeThroughParent`.
4. No `revalidatePath`, no `redirect`, no `notFound` inside the repo.
5. Every `count()` has `.mapWith(Number)`; no `avg()`/`sum()` does.
6. Every `inArray(col, ids)` is guarded against `ids.length === 0`.
7. Each no-op scoping helper carries its `// Phase 5:` comment.

Fix what fails. This list is also the review checklist for the session model.

---

## Task 20 — lock the door (ESLint)

**File:** `eslint.config.mjs`

Append to the `defineConfig([...])` array:

```js
{
  files: ["src/**/*.ts", "src/**/*.tsx"],
  ignores: ["src/db/**"],
  rules: {
    "no-restricted-imports": ["error", {
      paths: [{
        name: "@/db",
        message:
          "Import a scoped repository from @/db/repo/* instead. Every query takes a Scope; see docs/superpowers/plans/phase-3-data-layer.md.",
      }],
      patterns: [{
        group: ["**/db/index", "**/db/index.ts"],
        message: "Import a scoped repository from @/db/repo/* instead.",
      }],
    }],
  },
},
```

`@/db/schema` stays allowed — components legitimately import table types.

**Verify:** `npm run lint` → clean. Then temporarily add `import { db } from '@/db';` to
`src/app/page.tsx`, re-run `npm run lint` → **one error** with the message above; remove
the line again.

---

## Task 21 — documentation

**File:** `CLAUDE.md`

Add a short subsection under "Architecture", after "Snapshot model":

> ### Data access
>
> No page, action, route handler or MCP tool touches `db` directly — ESLint forbids
> importing `@/db` outside `src/db/**`. Every query goes through a repository in
> `src/db/repo/*` whose functions all take a `Scope` (`src/db/scope.ts`) as their first
> parameter. `Scope` is a branded type: it can only be produced by `currentScope()` (the
> request's workspace), `scopeFromCustomerId()` (derived from a row — how the background
> executor stays scoped, via `scopeForRun`) or `systemScope(reason)` (the grep-able
> escape hatch). Today there is one implicit workspace, so `scopeWhere` / `scopeValues` /
> `scopeThroughParent` are no-ops; Phase 5 adds `customer_id` to the five root tables and
> fills those three functions in, and no call site changes. Child tables
> (`machine_models`, `tools`, `prompts`, `prompt_toolsets`, `run_results`) inherit scope
> through their FK, which is why child reads join their parent and child writes carry
> their parent key.

Also update the Testing section to mention `src/db/scope.test.ts` and
`src/lib/form-data.test.ts`.

**Verify:** the claims match the code (`grep` the function names).

---

## Phase verification

Run all of it, in order, from the repo root with Node 22 on PATH.

```bash
export PATH="$HOME/.nvm/versions/node/v22.23.1/bin:$PATH"
npm test                 # 210 pre-existing + scope.test.ts + form-data.test.ts + 1 compare test, 0 failures
npx tsc --noEmit         # clean
npm run lint             # clean
npm run build            # succeeds (also catches RSC/route errors the dev server hides)
```

Structural checks:

```bash
# no db handle outside the repo
grep -rn "from '@/db'" src --include=*.ts --include=*.tsx | grep -v "^src/db/"      # empty
grep -rnE "\bdb\s*\.\s*(select|insert|update|delete|transaction)" src | grep -v "^src/db/"  # empty
# dedupes landed
grep -rn "async function replaceToolsetLinks" src            # exactly 1, in src/db/repo/prompts.ts
grep -rn "function optionalString(formData" src              # exactly 1, in src/lib/form-data.ts
# index exists
grep -n "run_results_run_id_idx" src/db/schema.ts drizzle/*.sql
```

Behavioural checks (dev server, `npm run dev`, app at `/agent-val`):

1. **Byte-identical pages.** Diff the `<tbody>` slices of `/`, `/runs` (default,
   `?sort=speed&dir=asc`, `?archived=only`, `?status=completed`), `/results?mode=models`
   and `/results?mode=runs&runs=…` against the Task-1 snapshots. No differences.
2. **Snapshot invariant.** Create a run; edit the prompt text, the base system prompt and
   the toolset it used; delete one prompt. The run detail page and `/results` still show
   the frozen text, the frozen system prompt and the frozen tool definitions. `/results`
   row headers still report drift (`prompt edited since`, `system prompt`, `tools`).
3. **Executor state machine** (mock LLM machine): completed run; `TRIGGER_ERROR` row
   errors while the run still ends `completed`; `TRIGGER_SLOW` + tab close rolls the row
   back to `pending` and **Resume** finishes it; a run whose machine row was deleted still
   executes from the snapshot URL; a second Start on a live run is refused (409).
4. **MCP** (`MCP_API_KEY` set): `initialize` / `tools/list` unchanged; the
   `create_prompt_group → create_prompt → create_run → execute_run → get_run →
   get_run_result → set_rating` loop works end to end; `set_rating` still refuses a
   `pending` row and still preserves a note when `note` is omitted.
5. **Performance sanity** (the reason for Tasks 12/13). With the dev server logging
   queries, or by counting `db.` calls in the repo path: `/runs` issues a bounded number
   of queries (aggregate + distinct groups + filter options + archived count) instead of
   three full-table scans, and `/results` issues only its own mode's queries.

## Not in this phase

- `customer_id` columns, the workspace switcher, session-derived scopes (Phase 5).
- Any auth or role check (Phase 4). `currentScope()` returning a constant is *correct*
  for now — do not sneak an ownership check in.
- Replacing the in-memory `executing` Set with a DB claim (Phase 2).
- Transactions around `createRunRecord` (Phase 2).
- Rewriting `buildModelColumns` / `buildCompareMatrix` as SQL — they are pure, tested,
  and this phase only narrows what is fed to them.
