# Phase 5 — Customer workspaces (implementation plan)

*Historical implementation plan, kept as a record of how the app was built. It describes the app under its former name and may not match the current code.*

Source spec: `docs/superpowers/specs/2026-08-12-platform-evolution-design.md` (§ Phase 5).
Target repo: `<repo root>` (branch `master`).

Goal: a `customers` table; a **NOT NULL** `customer_id` on the five root tables
(`machines`, `system_prompts`, `toolsets`, `prompt_groups`, `runs`); child tables inherit
scope through their parent FK; every query goes through the Phase-3 data-access layer with a
**required** customer scope; a workspace switcher in the UI; existing data assigned to a
default workspace; customer CRUD; the MCP surface made workspace-aware.

Node is not on PATH. Prepend to **every** shell command:

```bash
export PATH="$HOME/.nvm/versions/node/v22.23.1/bin:$PATH"
```

---

## 0. Read this before writing code

### 0.1 Preconditions (this plan assumes Phases 1–4 have landed)

| Phase | What this plan depends on |
|---|---|
| 1 | `drizzle/` committed; migrations applied by drizzle's `migrate()` from a startup script; `__app_seeds` is in `src/db/schema.ts`. |
| 2 | Postgres. `src/db/schema.ts` uses `pgTable`; `DATABASE_URL`; `scripts/seed-prompts.mjs` rewritten for pg; `createRunRecord` wrapped in a transaction; integration tests run against a scratch Postgres. |
| 3 | One data-access module; the 22 files that imported `db` directly now call it; its query functions already take a `scope` argument that is currently a **pass-through**. |
| 4 | better-auth: `users` table with `role` (`admin`/`member`/`viewer`), session helper, per-user API tokens replacing `MCP_API_KEY`, role checks at the top of every server action and route handler. |

**The exact identifiers Phases 3 and 4 chose are not knowable from here.** Task 1 is a
preflight that discovers them and writes down a mapping; every later task refers to the
mapping, not to guessed names. Where this plan writes a name, treat it as *the role a symbol
plays*, and substitute the real one:

| This plan calls it | Role |
|---|---|
| `src/db/access/*` | the Phase-3 data-access layer |
| `Scope` | the object Phase-3 query functions take (`{ userId, role, … }`) |
| `getSession()` | Phase-4 helper returning the authenticated user + role |
| `requireRole('member')` | Phase-4 role gate used at the top of server actions |
| `McpContext` | whatever Phase 4 threads into MCP tool handlers for the token's user |
| `npm run db:init` | Phase-1 script that generates + applies migrations |

### 0.2 Risks and open questions — raise these with the reviewer before Task 2

1. **Member vs admin for `deleteCustomer`.** The spec's role table gives *members* customers.
   This plan lets members create/rename/archive but restricts **delete** to admin, because a
   workspace holds machines, i.e. base URLs and API keys, and deletion is irreversible. Flag
   for confirmation; it is a one-line change either way (Task 9).
2. **NOT NULL cannot be one migration.** Backfill has to sit between "add nullable column" and
   "set not null". Three migrations, in order (Tasks 3–5). Do not let `drizzle-kit generate`
   emit `SET NOT NULL` before the data migration has run.
3. **Cross-customer references are not enforced by the database.** `prompt_toolsets`
   (prompt in A ↔ toolset in B), `runs.machine_id`, `run_results.prompt_id` can all in
   principle point across workspaces. This plan enforces them in application code
   (`assertSameCustomer`, Task 8) plus integration tests (Task 17). The rigorous alternative —
   `UNIQUE (id, customer_id)` on parents and composite FKs from children — was rejected because
   it means denormalising `customer_id` onto every child table, contradicting the spec's
   "child tables inherit scope via FK". If review prefers the composite-FK version, it is an
   additive change later.
4. **Deleting a customer is `ON DELETE RESTRICT`, deliberately.** A cascade would silently
   destroy run history. Archiving (`customers.archived_at`) is the soft path.
5. **Existing production data.** Everything existing lands in one workspace named `Default`.
   Verify row counts before/after (Task 5 verification). Take a `pg_dump` first.
6. **The MCP surface gains a required workspace selection** — an existing external caller's
   scripts break until they pass a customer. That is intended (an unscoped write has no
   defined destination), but it is a breaking API change worth calling out in the commit
   message and README.
7. **Timestamp column style.** Phase 2 converted epoch-millis to native types, but this plan
   cannot see the result. Every SQL/TS sketch below marks where you must match the style
   already used in `src/db/schema.ts` (`timestamp` + `now()` vs `bigint` + `Date.now()`).

---

## Task 1 — Preflight: discover the Phase 3/4 surface, write the mapping down

**Files:** create `docs/superpowers/plans/phase-5-mapping.md` (scratch notes, deleted or kept at
the end at your discretion — it is not a report, it is your working reference).

Run and record:

```bash
export PATH="$HOME/.nvm/versions/node/v22.23.1/bin:$PATH"
cd <repo root>
grep -rn "from '@/db'" src | sort            # must be ONLY the data-access layer after phase 3
ls src/db src/lib | sort
grep -rn "export function\|export async function\|export interface\|export type" src/db/access/*.ts | head -80
grep -rn "getSession\|auth()\|requireRole\|Role" src/lib/*.ts src/lib/**/*.ts | head -40
grep -rn "customer" src | head                # expect nothing yet
sed -n '1,60p' src/db/schema.ts               # pgTable style, id type, timestamp style
cat drizzle/meta/_journal.json | tail -20     # last applied migration
grep -n "scripts" -A 20 package.json
```

Write into the mapping file: the real module path of the data-access layer, its `Scope` type
name and shape, the session helper, the role gate, the MCP context type, the id column type
(`serial` vs `integer generated always as identity`), the timestamp style, and the db:init /
migrate / test / integration-test commands.

**Verify:** `grep -rn "from '@/db'" src` lists only files inside the data-access layer (plus
`src/db/index.ts` itself). If it lists pages/actions, Phase 3 is incomplete — stop and report.

---

## Task 2 — `customers` table in the schema

**File:** `src/db/schema.ts` (edit).

Add at the top of the table definitions (customers is the root everything else points at):

```ts
/**
 * A customer workspace.
 *
 * Not a tenant: customers never log in, and every team member can switch into any
 * workspace. It is the label that keeps one engagement's machines, prompts and runs from
 * mixing with another's — which matters most for machines, since each engagement registers
 * its own endpoints with its own API keys.
 */
export const customers = pgTable(
  'customers',
  {
    id: /* match the id style of the other tables */,
    name: text('name').notNull(),
    description: text('description'),
    /** Hidden from the switcher without destroying anything it owns. */
    archivedAt: /* nullable timestamp, matching this file's style */,
    createdAt: /* … */.notNull(),
    updatedAt: /* … */.notNull(),
  },
  (table) => [uniqueIndex('customers_name_lower_idx').on(sql`lower(${table.name})`)],
);

export type Customer = typeof customers.$inferSelect;
export type NewCustomer = typeof customers.$inferInsert;
```

Case-insensitive uniqueness on the name is load-bearing: MCP callers name a workspace, and
`resolveRowRef` refuses ambiguous names rather than guessing, so two workspaces differing only
in case would make every by-name call fail. Import `sql` from `drizzle-orm` and `uniqueIndex`
from `drizzle-orm/pg-core` if not already imported.

**Verify:** `npx tsc --noEmit` passes; `npx drizzle-kit generate` (do not apply yet) produces a
migration containing `CREATE TABLE "customers"` and the unique index — inspect the SQL, then
`git checkout drizzle/` to discard it (Task 3 generates the real one together with the FKs).

---

## Task 3 — Nullable `customer_id` on the five root tables + indexes (migration A)

**Files:** `src/db/schema.ts` (edit), `drizzle/` (generated).

Add to `machines`, `systemPrompts`, `toolsets`, `promptGroups`, `runs`:

```ts
  customerId: integer('customer_id').references(() => customers.id, { onDelete: 'restrict' }),
```

— **nullable for now** (the `.notNull()` arrives in Task 5). `onDelete: 'restrict'` on all
five: deleting a workspace must never cascade into run history.

Add a per-table index on the new column (Postgres does not index FKs automatically), in each
table's config callback:

```ts
  (table) => [ /* existing entries */, index('machines_customer_id_idx').on(table.customerId) ],
```

and on the two tables where the layer will filter by `(customer_id, name)`
(`system_prompts`, `toolsets`, `prompt_groups`) add `index('<t>_customer_name_idx').on(table.customerId, table.name)`.
Non-unique on purpose: existing production data may already hold duplicate names, and
uniqueness is enforced in app code where a good error message can be produced.

Generate and apply:

```bash
npx drizzle-kit generate --name add_customers
npm run db:init            # or the phase-1 migrate script from the mapping
```

**Verify:**

```bash
psql "$DATABASE_URL" -c "\d machines" | grep customer_id      # nullable, FK to customers
psql "$DATABASE_URL" -c "\di" | grep customer                  # five+ indexes
npx tsc --noEmit
```

---

## Task 4 — Data migration: create `Default`, backfill every root row (migration B)

**Files:** `drizzle/00XX_assign_default_customer.sql` (hand-written), `drizzle/meta/*` (updated
by the generator).

```bash
npx drizzle-kit generate --custom --name assign_default_customer
```

That writes an **empty** SQL file registered in the journal. Fill it (adapt the timestamp
expressions to the column types this repo actually uses — `now()` for `timestamp`,
`(extract(epoch from now()) * 1000)::bigint` for epoch-millis):

```sql
--> statement-breakpoint
INSERT INTO "customers" ("name", "description", "created_at", "updated_at")
SELECT 'Default', 'Everything that existed before customer workspaces were introduced.', now(), now()
WHERE NOT EXISTS (SELECT 1 FROM "customers");
--> statement-breakpoint
UPDATE "machines"       SET "customer_id" = (SELECT MIN("id") FROM "customers") WHERE "customer_id" IS NULL;
--> statement-breakpoint
UPDATE "system_prompts" SET "customer_id" = (SELECT MIN("id") FROM "customers") WHERE "customer_id" IS NULL;
--> statement-breakpoint
UPDATE "toolsets"       SET "customer_id" = (SELECT MIN("id") FROM "customers") WHERE "customer_id" IS NULL;
--> statement-breakpoint
UPDATE "prompt_groups"  SET "customer_id" = (SELECT MIN("id") FROM "customers") WHERE "customer_id" IS NULL;
--> statement-breakpoint
UPDATE "runs"           SET "customer_id" = (SELECT MIN("id") FROM "customers") WHERE "customer_id" IS NULL;
```

`MIN(id)` rather than a hard-coded `1`: a database that already has customers (a re-run, or a
developer who created one by hand between migrations) must not be pointed at a row that does
not exist. The `WHERE NOT EXISTS` makes the insert idempotent.

Apply with the same command as Task 3.

**Verify:**

```bash
psql "$DATABASE_URL" -c "SELECT id, name FROM customers;"
for t in machines system_prompts toolsets prompt_groups runs; do
  psql "$DATABASE_URL" -tc "SELECT '$t', count(*) FILTER (WHERE customer_id IS NULL), count(*) FROM $t;"
done
```
Expected: exactly one `Default` customer; the NULL count is `0` for every table and the total
count is unchanged from a `pg_dump`-time count taken before Task 3.

---

## Task 5 — Flip `customer_id` to NOT NULL (migration C)

**Files:** `src/db/schema.ts` (edit), `drizzle/` (generated).

Append `.notNull()` to all five `customerId` columns, then:

```bash
npx drizzle-kit generate --name customer_id_required
npm run db:init
```

**Verify:** the generated SQL contains five `ALTER TABLE … ALTER COLUMN "customer_id" SET NOT NULL`
and nothing else; `psql "$DATABASE_URL" -c "\d runs" | grep customer_id` shows `not null`;
`npx tsc --noEmit` passes (inserts that do not set `customerId` are now type errors — that is
the point, and Tasks 8–13 fix them).

---

## Task 6 — `__app_seeds` and `users` gain a customer column (migration D)

**Files:** `src/db/schema.ts` (edit), `drizzle/` (generated).

1. `appSeeds` (moved into schema.ts by Phase 1): add
   `customerId: integer('customer_id').notNull().references(() => customers.id, { onDelete: 'cascade' })`
   and change the primary key to `primaryKey({ columns: [table.customerId, table.kind, table.scope, table.name] })`.
   Cascade here, not restrict: the ledger is bookkeeping about a workspace, not content, and it
   must not block a workspace from ever being deleted.
2. `users` (Phase 4): add
   `activeCustomerId: integer('active_customer_id').references(() => customers.id, { onDelete: 'set null' })`
   — nullable, `SET NULL`, so archiving/deleting the workspace a user was in logs them into the
   fallback rather than breaking their session.

Because `__app_seeds` rows already exist and the new column is NOT NULL, this needs the same
three-step dance in miniature. Simplest correct sequence, all in one custom migration written
by hand after generating the nullable version — or, since the ledger is small, generate the
column as nullable, add a custom migration
`UPDATE "__app_seeds" SET "customer_id" = (SELECT MIN(id) FROM customers) WHERE "customer_id" IS NULL;`
followed by the drop-and-recreate of the primary key:

```sql
ALTER TABLE "__app_seeds" DROP CONSTRAINT "__app_seeds_pkey";
ALTER TABLE "__app_seeds" ALTER COLUMN "customer_id" SET NOT NULL;
ALTER TABLE "__app_seeds" ADD PRIMARY KEY ("customer_id", "kind", "scope", "name");
```

**Verify:** `psql "$DATABASE_URL" -c "\d __app_seeds"` shows the four-column PK and no NULLs;
`psql "$DATABASE_URL" -c "\d users" | grep active_customer_id`.

---

## Task 7 — The scope object becomes customer-scoped

**Files:** create `src/lib/scope.ts`, create `src/lib/scope.test.ts`; edit the Phase-3 `Scope`
type wherever it lives.

```ts
/** Everything a query needs to know about who is asking and about what. */
export interface Scope {
  userId: string;           // shape per phase 4
  role: Role;
  /** The active customer workspace. Required: an unscoped query is a type error. */
  customerId: number;
}

/** A workspace as the switcher and the MCP `list_customers` tool see it. */
export interface CustomerOption {
  id: number;
  name: string;
  archived: boolean;
}

/**
 * Which workspace a user lands in.
 *
 * `preferred` is their stored `active_customer_id`; it is ignored when it names a workspace
 * that no longer exists or has been archived, because a stale pointer must degrade to a
 * working session rather than an empty app. Falls back to the oldest live workspace — with
 * only the migration's `Default` present, that is the one every existing install wants.
 */
export function resolveActiveCustomerId(
  preferred: number | null,
  customers: readonly CustomerOption[],
): number | null {
  const live = customers.filter((c) => !c.archived);
  if (preferred !== null && live.some((c) => c.id === preferred)) return preferred;
  return live[0]?.id ?? customers[0]?.id ?? null;
}
```

`src/lib/scope.test.ts` (pure, runs in the existing vitest suite): preferred present and live →
returned; preferred archived → first live returned; preferred missing → first live; no live
customers but one archived → the archived one (so an all-archived install is still usable);
empty list → `null`.

**Verify:** `npx vitest run src/lib/scope.test.ts` — all cases pass.

---

## Task 8 — Data-access layer: scope becomes real

**Files:** the Phase-3 layer (`src/db/access/*.ts` per the mapping) — this is the biggest task;
do it in three commits.

**8a — root tables.** Every read of `machines`, `system_prompts`, `toolsets`, `prompt_groups`,
`runs` gains `eq(table.customerId, scope.customerId)` in its `where`. Every write sets
`customerId: scope.customerId` on insert, and carries the same predicate on update/delete so a
guessed id from another workspace updates zero rows instead of someone else's row (today every
id-taking action is IDOR-shaped; workspace scoping is the second half of the fix Phase 4
started).

**8b — child tables inherit through a join.** No `customer_id` is added to `machine_models`,
`tools`, `prompts`, `prompt_toolsets`, `run_results`. Their queries join the parent and filter
there:

```ts
db.select({ … })
  .from(prompts)
  .innerJoin(promptGroups, eq(prompts.groupId, promptGroups.id))
  .where(and(eq(promptGroups.customerId, scope.customerId), /* … */));
```

Parents and their scope column: `machine_models → machines`, `tools → toolsets`,
`prompts → prompt_groups`, `prompt_toolsets → prompts → prompt_groups`,
`run_results → runs`. The `(customer_id)` indexes from Task 3 keep these joins cheap.

**8c — cross-scope write guard.** Add to the layer:

```ts
/**
 * Refuses a write that would point a row at another workspace's row.
 *
 * The database cannot express this (children inherit scope through their parent, so a link
 * table has no customer column to constrain), and the three places it can happen are exactly
 * the three places two roots meet: a prompt's toolsets, a run's machine, a result's prompt.
 */
export async function assertSameCustomer(
  scope: Scope,
  refs: { toolsetIds?: number[]; machineId?: number; groupIds?: number[]; promptIds?: number[] },
): Promise<void>   // throws Error('… belongs to another workspace.')
```

Implement it as one scoped `SELECT id` per referenced table with `inArray(...)` and compare set
sizes; a missing id and a foreign id are reported identically ("no longer exists in this
workspace") — the caller has no business learning that the id exists elsewhere.

Also add customer accessors here: `listCustomers(scope)` (all, including archived; the caller
filters), `getCustomer(scope, id)`, `createCustomer`, `updateCustomer`, `setCustomerArchived`,
`deleteCustomer` (see Task 9), `countCustomerContent(id)` → `{ machines, systemPrompts, toolsets, promptGroups, runs }`
used both by the delete guard and by the workspace list page. Customer accessors take the
session, not a customer scope — they are the one family of queries that is *about* workspaces
rather than *inside* one.

**Verify:** `npx tsc --noEmit` (call sites still broken at this point are Tasks 9–15's job);
`grep -rn "\.from(runs)\|\.from(machines)\|\.from(toolsets)\|\.from(promptGroups)\|\.from(systemPrompts)" src/db/access | grep -v customerId`
returns nothing — every root-table query mentions the scope column.

---

## Task 9 — Customer CRUD: server actions

**Files:** create `src/actions/customers.ts`.

Mirror the style of `src/actions/system-prompts.ts` (FormData in, `revalidatePath` out), with
Phase-4 role gates at the top of every export:

- `createCustomer(formData)` — `requireRole('member')`; name required, trimmed; refuse a
  case-insensitively duplicate name with a message naming the existing workspace (do not rely
  on the unique index's error text); `revalidatePath('/customers')`; `redirect` to `/customers`.
- `updateCustomer(id, formData)` — member; rename + description.
- `setCustomerArchived(id, archived)` — member. Archiving the caller's active workspace is
  allowed; the next `getActiveScope()` falls back (Task 7's helper) — that is why the fallback
  exists.
- `deleteCustomer(id)` — **admin** (see risk 1). Refuse when `countCustomerContent(id)` is
  non-zero, listing the counts: *"Workspace 'X' still holds 3 machines, 12 prompt groups and 41
  runs. Archive it instead, or delete its contents first."* The FK `RESTRICT` is the backstop;
  this check exists to produce a sentence instead of a constraint violation.
- `switchCustomer(customerId)` — any role, including `viewer`: switching is reading. Verifies
  the customer exists, writes `users.active_customer_id`, then `revalidatePath('/', 'layout')`.

**Verify:** `npx tsc --noEmit`; `npm run lint`.

---

## Task 10 — Active scope on the server + wrong-workspace handling

**Files:** create `src/lib/active-scope.ts` (server-only), create
`src/components/wrong-workspace-notice.tsx`.

```ts
import 'server-only';

/**
 * The scope every page, action and route handler runs under.
 *
 * The active workspace lives on the user row rather than in a cookie: it is then impossible to
 * forge from the client, it survives a session refresh, and there is exactly one place that
 * says which workspace a user is in. The cost — one extra column read per request — is a
 * primary-key lookup already on the session path.
 */
export async function getActiveScope(): Promise<Scope>;
/** Same, plus the list the switcher renders, so a layout needs one call. */
export async function getWorkspaceContext(): Promise<{ scope: Scope; customers: CustomerOption[] }>;
```

Behaviour: read the Phase-4 session (unauthenticated → let Phase-4's gate handle it), load the
customer list, run `resolveActiveCustomerId(user.activeCustomerId, customers)`. When the
resolved id differs from the stored one, persist it (self-healing after an archive). When it is
`null` — no customers at all, only reachable if someone deleted `Default` — `redirect('/customers?empty=1')`.

`WrongWorkspaceNotice` is the answer for a deep link into another workspace
(`/runs/42`, `/machines/3`): rather than a bare 404, render *"This run belongs to workspace
**Acme GmbH**"* with a switch button that calls `switchCustomer` and navigates back to the same
URL. A shared link between colleagues then works; the workspace still never changes without a
click. Detail pages use it; list pages never need it.

**Verify:** `npx tsc --noEmit`. Behavioural check after Task 12.

---

## Task 11 — Workspace switcher in the UI

**Files:** `src/app/layout.tsx` (edit), create `src/components/workspace-switcher.tsx`,
`src/components/sidebar-nav.tsx` (edit: add `{ href: '/customers', label: 'Workspaces' }`),
create `src/app/customers/page.tsx`.

`layout.tsx` becomes `async`, calls `getWorkspaceContext()`, and renders the switcher above
`<SidebarNav />`:

```tsx
<WorkspaceSwitcher customers={live} activeId={scope.customerId} />
```

`workspace-switcher.tsx` is a client component: a `<select>` (or a small popover; a select is
enough for a handful of workspaces) whose `onChange` runs
`startTransition(async () => { await switchCustomer(id); router.refresh(); })`. Archived
workspaces are omitted unless one is the active id. Cookies cannot be written during RSC
render (Next 16: `cookies().set` is only legal in a Server Function or Route Handler) — this is
a second reason the active workspace lives on the user row and the switch goes through a server
action.

`/customers` page: list every workspace with its content counts (`countCustomerContent`), the
`Default` badge for the oldest, archive/unarchive buttons, a hidden-by-default create form via
the existing `CreateToggle`, and rename inline like `system-prompt-row.tsx`. Delete button
rendered only for `admin`.

**Verify:** `npm run dev`, open `http://localhost:3000/agent-val/customers`; create a second
workspace, switch to it, confirm `/prompts`, `/machines`, `/runs`, `/results` are all empty,
switch back, confirm the seeded data returns.

---

## Task 12 — Pages, actions and route handlers pass the scope

**Files (pages):** `src/app/page.tsx`, `runs/page.tsx`, `runs/new/page.tsx`, `runs/[id]/page.tsx`,
`prompts/page.tsx`, `machines/page.tsx`, `machines/[id]/page.tsx`, `toolsets/page.tsx`,
`system-prompts/page.tsx`, `results/page.tsx` (Task 14 covers the last one's specifics).

Each starts with `const scope = await getActiveScope();` and passes it to every data-access
call. Detail pages (`runs/[id]`, `machines/[id]`) load the row **unscoped first** only to
decide between `notFound()` (does not exist) and `<WrongWorkspaceNotice/>` (exists elsewhere) —
one narrow, deliberate unscoped read, exposing nothing but the workspace's name.

**Files (actions):** all five files in `src/actions/`. Every export gains
`const scope = await getActiveScope();` after the Phase-4 role gate, and passes it down.
`createPrompt`/`updatePrompt` call `assertSameCustomer(scope, { groupIds: [groupId], toolsetIds })`
before writing links; `createRun` is covered in Task 13.

**Files (route handlers):** `api/machines/[id]/discover`, `api/machines/[id]/test`,
`api/toolsets/[id]/discover`, `api/runs/[id]/execute`. Each resolves the scope and 404s when the
target row is not in it — a scoped `SELECT` returning no row *is* the check; no extra query.

**Verify:** `npx tsc --noEmit && npm run build`. Manually: with workspace B active, hit
`curl -i http://localhost:3000/agent-val/api/machines/<id-from-A>/test -X POST` → 404, not a
connection probe of A's endpoint.

---

## Task 13 — Leak path 1: `createRunRecord`'s snapshot map

**Files:** `src/lib/run-create.ts` (edit), `src/lib/run-executor.ts` (edit).

`createRunRecord` currently does `const systemPromptRows = await db.select().from(systemPrompts);`
— **every** system prompt in the database, to build `systemPromptById` for the snapshot. Across
workspaces that map would happily resolve another customer's `system_prompt_id` into the frozen
`system_prompt_text` of a run. Change the signature and every query:

```ts
export async function createRunRecord(scope: Scope, input: CreateRunInput): Promise<CreateRunResult>
```

1. machine lookup → scoped (`and(eq(machines.id, input.machineId), eq(machines.customerId, scope.customerId))`).
2. `promptGroups` lookup → add the scope predicate; the existing "The selected prompt groups no
   longer exist." message then also covers a group from another workspace, which is correct — to
   this caller it does not exist.
3. system prompts → `where(eq(systemPrompts.customerId, scope.customerId))`. Even better,
   narrow it to the ids actually referenced (`inArray(systemPrompts.id, referencedIds)`),
   which removes the whole-table read Phase 3 flagged. Do both.
4. `resolveToolSnapshots` → `innerJoin(toolsets, …)` already exists; add
   `eq(toolsets.customerId, scope.customerId)` to its `where`. A prompt linked to a foreign
   toolset then contributes no tools, and the existing "has tool mode X but no enabled tools"
   refusal fires with an actionable message — no new error path.
5. the `runs` insert sets `customerId: scope.customerId`.
6. `machineModels` upsert → already keyed by the scoped machine id; no change beyond it being
   reached only after step 1.

`run-executor.ts`: `executeRun` loads the run by id. Add the run's own `customerId` to the row
it selects and use it for the two live-credential lookups — `machines` (line ~192) and
`buildMcpExecutor`'s `toolsets` (line ~84) — with `and(inArray(toolsets.id, toolsetIds), eq(toolsets.customerId, run.customerId))`.
Defence in depth: those ids come from a snapshot the app wrote, so they cannot legitimately be
foreign, and a foreign one now yields "no MCP server for this tool" instead of quietly calling
another customer's ERP with their credentials.

**Verify:** `npx vitest run` (existing suite green); the integration test in Task 17 is the real
proof. Manually: workspace B, `/runs/new` → the machine and group pickers list only B's rows.

---

## Task 14 — Leak path 2: the results page and the `prompt_text` fallback

**File:** `src/app/results/page.tsx` (edit). `src/lib/compare.ts` needs **no** change.

`buildCompareMatrix` matches results whose prompt was deleted (`promptId === null`) by
normalised prompt text. That is only dangerous if two workspaces' rows reach the same call — so
the fix belongs in the four queries feeding it, not in the pure function (which stays testable
and mode-agnostic):

1. `db.select().from(runs)` (line ~124) → `.where(eq(runs.customerId, scope.customerId))`.
2. the `summaryRows` select over all of `run_results` → `innerJoin(runs, …)` +
   `eq(runs.customerId, scope.customerId)` (this also fixes the O(runs × results) whole-table
   read Phase 3 flagged, if Phase 3 has not already).
3. run-mode cells: `inArray(runResults.runId, selectedRunIds)` where `selectedRunIds` is already
   filtered through `comparableById`, itself built from the scoped `runRows` — a URL naming a
   foreign run id silently drops it. Assert that in a comment; no code change needed once (1)
   is scoped.
4. model-mode cells (line ~281): add `eq(runs.customerId, scope.customerId)` to the existing
   `and(...)`, and scope the live-prompt query via `innerJoin(promptGroups)` +
   `eq(promptGroups.customerId, scope.customerId)`.

Add one sentence to the page's existing "archived runs not listed" line? No — the switcher
already says which workspace this is; do not add a blurb (the page's stated design principle is
that the pickers are the explanation).

**Verify:** integration test in Task 17. Manually: create the same-named prompt group in two
workspaces, run one in each, confirm `/results?mode=models` in A offers only A's model columns
and A's rows.

---

## Task 15 — MCP: every call names a workspace

**Files:** `src/lib/mcp/protocol.ts`, `src/lib/mcp/registry.ts`, `src/lib/mcp/tools-authoring.ts`,
`src/lib/mcp/tools-runs.ts`, create `src/lib/mcp/customer.ts`, `src/app/api/mcp/route.ts`,
tests `src/lib/mcp/protocol.test.ts` (edit), create `src/lib/mcp/customer.test.ts`.

The MCP server is **stateless by design** (no session id), so there is nowhere to "switch
workspace" — the workspace has to arrive with each request. Three ways, in precedence order,
implemented once in `src/lib/mcp/customer.ts`:

```ts
export const CUSTOMER_HEADER = 'x-customer';

export interface McpScopeSource {
  /** `X-Customer: acme` — set once in the client's mcp.json, applies to every call. */
  header: RowRef | null;
  /** The token's own workspace, if phase 4's api_tokens table carries one. */
  tokenDefault: number | null;
}

/**
 * The workspace one tool call runs in: an explicit `customer` argument wins over the
 * connection's header, which wins over the token's default. Nothing is guessed — with none of
 * the three present the call is refused with the list of workspaces, because a write with no
 * defined destination is worse than an error a model can act on.
 */
export async function resolveMcpScope(
  args: ToolArgs, source: McpScopeSource, session: McpSession,
): Promise<Scope>;

/** The `customer` property every tool advertises. */
export const CUSTOMER_ARG = {
  type: ['string', 'integer'],
  description:
    'Name or id of the customer workspace this call applies to. Required unless the connection sends an X-Customer header. list_customers shows what exists.',
} as const;
```

Changes:

1. `McpToolSpec.handler` becomes `(args: ToolArgs, ctx: McpContext) => Promise<unknown>` and
   `handleMcpMessage(payload, registry, ctx)` passes it through (if Phase 4 already added a
   context parameter, extend that type instead of adding a second one). `ctx` carries the
   authenticated user + role (Phase 4) and `McpScopeSource`.
2. `route.ts` builds the context: parse `X-Customer` with the existing `parseRowRef`
   (a numeric string is an id — the rule already in `args.ts`), read the token's default.
3. **Every** tool handler begins `const scope = await resolveMcpScope(args, ctx.source, ctx.session);`
   and threads `scope` into its queries via the data-access layer. Add `customer: CUSTOMER_ARG`
   to every `inputSchema.properties` — but **not** to `required`, since the header can supply
   it; the runtime refusal carries the explanation JSON Schema cannot.
4. Name resolution is the isolation mechanism: `allGroups`, `allSystemPrompts`, `allToolsets`
   in `tools-authoring.ts` and the `machines`/`promptGroups` lookups in `tools-runs.ts` all
   become scoped, so `resolveRowRef` can only ever match inside the workspace, and a prompt in A
   can never link a toolset from B. Its "Known: …" hint then lists only that workspace's rows,
   which is also the right answer for a confused model.
5. Id-taking tools (`get_prompt`, `update_prompt`, `delete_prompt`, `get_run`, `execute_run`,
   `get_run_result`, `set_rating`) must verify the row is in scope through its parent chain
   (`prompts → prompt_groups`, `run_results → runs`) and throw the **existing** message —
   `No prompt with id 12.` / `No run result with id 88.` A cross-workspace id is, to this
   caller, an id that does not exist.
6. New read-only tool `list_customers` in a new `tools-customers.ts` (added to `registry.ts`),
   the **only** tool that needs no scope: `{ customers: [{ id, name, description, archived,
   counts: { prompt_groups, prompts, machines, runs } }] }`. Its description tells the caller to
   pass the chosen name as `customer` (or set the header).
7. `initialize`'s `instructions` string gains: *"Every call is scoped to one customer workspace.
   Pass `customer` (name or id) on each call, or send an `X-Customer` header on the connection.
   `list_customers` lists them."*
8. Customers are **not** writable over MCP — creating an engagement is a human decision with
   billing behind it, and the app's existing line (machines and toolsets stay UI-only because
   they are credentials) already puts workspace administration on the UI side.

`customer.test.ts` (pure): arg beats header beats token default; a name resolved
case-insensitively; an unknown name lists the known workspaces; none of the three present →
`McpToolError` naming the header and the argument; a numeric string treated as an id.
`protocol.test.ts`: update the dispatch tests for the new handler arity and add one asserting
`tools/list` advertises `customer` on a writing tool.

**Verify:**

```bash
npx vitest run src/lib/mcp
# against the dev server, with a phase-4 token:
curl -s localhost:3000/agent-val/api/mcp -H 'x-api-key: <token>' -H 'content-type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"list_prompt_groups","arguments":{}}}'
#   → isError content naming the customer argument and the X-Customer header
curl -s … -H 'x-customer: Default' -d '…same…'   # → the Default workspace's groups
curl -s … -d '{"…","params":{"name":"list_customers","arguments":{}}}'  # → both workspaces
```

---

## Task 16 — Seeding is per workspace

**File:** `scripts/seed-prompts.mjs` (edit), `package.json` (script comment), `README.md`.

Decision: **per-customer seeding, defaulting to the default workspace.** A new engagement's
workspace starts empty, and the standard suite (tool tests, the prompt-injection group) is
exactly what you want to run against a customer's candidate models on day one — so seeding must
be repeatable into any workspace. The alternative (seed only the `Default` workspace, ever) makes
the seeded suite unreachable from every real engagement, which defeats it.

Implementation:

1. Resolve the target once, at the top of `main()`:
   ```js
   const wanted = process.env.SEED_CUSTOMER ?? null;   // name or id; unset = default workspace
   ```
   Unset → `SELECT id, name FROM customers ORDER BY id LIMIT 1` (the migration's `Default`).
   Set and numeric → by id. Set and non-numeric → case-insensitive name match; ambiguous or
   missing → exit(1) with the list of workspaces, never create one implicitly (a typo must not
   silently produce a workspace called `Acme Gmbh`).
2. Every `INSERT` gains `customer_id`: `toolsets`, `prompt_groups` (prompts/tools/links inherit).
3. Every existence lookup gains the same predicate: `toolsetByName`, `groupByName`
   (`WHERE customer_id = $1 AND name = $2`), `promptExists` (already keyed by `group_id`, which
   is now workspace-specific — no change needed, but add a comment saying why).
4. `__app_seeds`: `wasSeeded`/`markSeeded` take `customer_id` as their first key, matching the
   PK from Task 6. This is what makes "seeded once, then deleted, stays deleted" a *per-workspace*
   promise — deleting a seeded prompt in Acme's workspace must not suppress it in the next one.
5. Log line gains the workspace name: `[seed-prompts] workspace "Default" (id 1)`.
6. README/`.env.example`: document `SEED_CUSTOMER`.

**Verify:**

```bash
npm run db:seed                                  # → "workspace \"Default\"", 0 added (already seeded)
SEED_CUSTOMER="Acme GmbH" npm run db:seed        # → creates the full set in the new workspace
psql "$DATABASE_URL" -tc "SELECT c.name, count(*) FROM prompt_groups g JOIN customers c ON c.id=g.customer_id GROUP BY 1;"
SEED_CUSTOMER="Acme GmbH" npm run db:seed        # → "up to date", nothing duplicated
SEED_CUSTOMER=nope npm run db:seed               # → exit 1, lists workspaces, writes nothing
```

---

## Task 17 — Cross-scope integration tests

**File:** create `tests/integration/workspaces.test.ts` (use the Phase-2 scratch-Postgres
harness; the CLAUDE.md scratch-copy-of-`app.db` recipe is obsolete after Phase 2).

Fixture: two customers `A` and `B`, each with a machine, a system prompt, a manual toolset with
one tool, a prompt group holding **one prompt with byte-identical text and title in both**, and
one completed run with one `ok` result. That identical prompt text is the fixture's whole point:
it is what the compare fallback matches on.

Cases:

1. `createRunRecord(scopeA, { groupIds: [groupB.id] })` → throws "no longer exist"; nothing
   written (assert `runs` count unchanged).
2. `createRunRecord(scopeA, …)` where A's prompt references A's system prompt → the result row's
   `system_prompt_text` equals A's content; then repeat with B's system prompt id forced onto
   A's prompt row → the snapshot is `null`, never B's text. **This is the snapshot-map leak.**
3. Results page query path with scope A → model columns contain only A's runs, and the
   deleted-prompt fallback never produces a row containing B's result (delete A's prompt first
   so `promptId` is null on both sides — the exact shape that made text matching necessary).
4. `set_rating` / `get_run_result` with scope A and B's `result_id` → `McpToolError` "No run
   result with id …"; B's row unchanged.
5. `update_prompt` in A naming a toolset that exists only in B → refused; links unchanged.
6. `executeRun` on B's run with A's toolset id injected into the snapshot → no MCP server
   resolved (tool result is an error string), and B's toolset URL is never fetched.
7. `deleteCustomer(A)` with content → refused with counts; after deleting content → succeeds.

**Verify:** the integration command from the mapping (e.g. `npx vitest run tests/integration`) —
all seven pass; each one fails if you revert the corresponding scope predicate (check at least
cases 2 and 3 by temporarily reverting).

---

## Task 18 — Documentation

**Files:** `CLAUDE.md` (edit), `README.md` (edit), `.env.example` (edit).

`CLAUDE.md` gets a `### Customer workspaces` section under Architecture, stating: workspace =
label, not tenant; the five root tables carry `customer_id NOT NULL`, children inherit through
their parent FK (and *why* there is no denormalised column); `ON DELETE RESTRICT` + archiving;
active workspace lives on the user row, switched through a server action because cookies cannot
be set during RSC render; MCP scope precedence (argument → `X-Customer` header → token default →
refusal) and that customers are not writable over MCP; per-workspace seeding via `SEED_CUSTOMER`
and the per-workspace `__app_seeds` key. Update the existing snapshot-model and compare
paragraphs to mention that both leak paths are now closed by scoped queries.

README: workspace concept, `SEED_CUSTOMER`, the MCP header in a sample `mcp.json`.

**Verify:** `grep -n "workspace" CLAUDE.md README.md` shows the new sections; re-read the
Architecture section end-to-end for contradictions with the pre-phase-5 text.

---

## Phase verification (run all of it before declaring the phase done)

```bash
export PATH="$HOME/.nvm/versions/node/v22.23.1/bin:$PATH"
cd <repo root>
npm test                     # whole vitest suite, incl. scope/customer/mcp tests — green
npx vitest run tests/integration
npx tsc --noEmit             # zero errors
npm run lint                 # zero errors
npm run build                # standalone build succeeds (catches RSC/route errors)
```

Phase-specific checks:

1. **No unscoped root query survives.**
   `grep -rn "from '@/db'" src | grep -v src/db/` → empty (everything goes through the layer).
   `grep -rn "\.from(runs)\|\.from(machines)\|\.from(toolsets)\|\.from(promptGroups)\|\.from(systemPrompts)" src --include=*.ts --include=*.tsx | grep -v "customerId"`
   → only the two deliberate unscoped detail-page reads from Task 12, each carrying the comment
   that says so.
2. **Schema is what the migrations produced.** `npx drizzle-kit generate --name verify_noop`
   emits an empty migration (schema and database agree); delete it and revert the journal.
3. **Row counts survived.** Compare the pre-Task-3 `pg_dump` counts of the five root tables and
   `run_results` with the current ones — identical, all pointing at `Default`.
4. **Manual UI pass.** Two workspaces; switch; confirm Dashboard, Machines, System Prompts,
   Toolsets, Prompts, Runs, Results and `/runs/new` each show only the active workspace's rows;
   a deep link to the other workspace's run renders `WrongWorkspaceNotice`, and its switch
   button lands on the run.
5. **MCP pass.** The three `curl`s from Task 15 (no workspace → refusal listing workspaces;
   `X-Customer` → scoped answer; `list_customers` → both), plus `create_prompt` with
   `customer: "Acme GmbH"` and a `group` name that exists only in `Default` → refused.
6. **Executing run.** Start a run in workspace A, switch to B in another tab mid-run, confirm
   the run finishes (execution is bound to its own request and to the run's own
   `customer_id`, not to the browser's active workspace).
