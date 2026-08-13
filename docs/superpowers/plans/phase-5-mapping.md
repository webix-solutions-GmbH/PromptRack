# Phase 5 preflight mapping (working notes)

*Historical implementation plan, kept as a record of how the app was built. It describes the app under its former name and may not match the current code.*

Discovered on the `platform-evolution` branch before Task 2. Substitute these for the
role-names the plan uses.

| Plan calls it | Actually is |
|---|---|
| `src/db/access/*` | `src/db/repo/*.ts` — `machines`, `prompts`, `results`, `runs`, `scoped`, `system-prompts`, `toolsets` |
| `Scope` | `src/db/scope.ts` — **branded** `interface Scope { customerId: number \| null; origin: 'session'\|'row'\|'system' }`, constructible only inside that module |
| `getSession()` | `currentActor()` / `requireActor()` in `src/lib/auth/guards.ts` → `Actor { userId, email, name, role, via }` |
| `requireRole('member')` | `requireWriter()` (member+) / `requireAdmin()` in the same file; pages wrap in `onPage(...)`, routes use `guardRequest(request, 'read'\|'write'\|'admin')` |
| `McpContext` | `McpCallContext { actor }` in `src/lib/mcp/protocol.ts`, threaded through `handleMcpMessage(payload, registry, ctx)` and into every `McpToolSpec.handler(args, ctx)` |
| `npm run db:init` | `npm run db:init` = `drizzle-kit generate && node scripts/init-db.mjs`; `npm run db:generate` / `npm run db:migrate` are the halves |
| `src/lib/scope.ts` (new) | **not created** — reviewer override: extend `src/db/scope.ts` instead |
| `getActiveScope()` | `currentScope()` in `src/db/scope.ts` (already the entry point every page/action/route uses) |

## Facts the plan asks for

- **id style**: `serial('id').primaryKey()`; FKs are `integer('x_id')`.
- **timestamps**: `timestamp('x', { withTimezone: true, mode: 'date' })` — server code holds `Date`;
  SQL literals use `now()`.
- **users table**: `users = pgTable('user', …)` (better-auth's singular default), `role` is
  `text('role', { enum: ['admin','member','viewer'] })`, id is `text`.
- **api_tokens**: `id text`, `userId text`, no customer column.
- **last migration**: `drizzle/0002_many_stingray.sql` (journal idx 2).
- **commands**: `npm test` (pure, no DB) / `npm run test:integration` (scratch pg, port 55432) /
  `npx tsc --noEmit` / `npm run lint` / `npm run build`.
- **dev db**: `agent-val-dev-db` container, `postgres://agentval:dev@127.0.0.1:5433/agentval`;
  psql via `docker exec agent-val-dev-db psql -U agentval -d agentval -c …`.
- **node**: Homebrew v26.7.0, already on PATH (the CLAUDE.md nvm line is stale on this machine).

## Task 1 verification

`grep -rn "from '@/db'" src` lists only:

```
src/db/repo/{scoped,toolsets,runs,results,system-prompts,prompts,machines}.ts
src/lib/auth.ts  src/lib/auth/users.ts  src/lib/auth/tokens.ts  src/lib/run-lock.ts
```

— the repository layer plus the four ESLint-exempt infrastructure files. Phase 3 is complete;
no page, action or route handler touches `db`.

## Pre-migration row counts (dev DB, for Task 4/5 and the phase verification)

```
machines 2 | system_prompts 1 | toolsets 3 | tools 12 | prompt_groups 4 | prompts 38
prompt_toolsets 6 | runs 6 | run_results 16 | machine_models 3 | __app_seeds 41
user 4 | api_tokens 2
```

## Decisions taken here (reported to the reviewer)

1. `resolveActiveCustomerId` lives in `src/db/scope.ts` (pure, tested in the existing
   `src/db/scope.test.ts`). No `src/lib/scope.ts`.
2. `currentScope()` stays the one entry point and gains the session read; it delegates the
   impure half to `src/lib/workspace.ts` (`server-only`) through a **dynamic** import so
   `src/db/scope.ts` stays importable by the database-free pure test suite.
3. `api_tokens` gets **no** customer column — `tokenDefault` is `null` for now, the precedence
   chain (arg → `X-Customer` header → token default) is implemented anyway.
4. Customer accessors live in `src/db/repo/customers.ts` and take no `Scope`: they are the one
   family of queries that is *about* workspaces rather than inside one.
