# Phase 1 — Foundation: migrations that work

*Historical implementation plan, kept as a record of how the app was built. It describes the app under its former name and may not match the current code.*

Spec: `docs/superpowers/specs/2026-08-12-platform-evolution-design.md` (Phase 1).
Target: **SQLite only.** Postgres is Phase 2 and will rewrite `src/db/schema.ts`,
`src/db/index.ts`, `scripts/init-db.mjs` and `scripts/seed-prompts.mjs` wholesale —
so keep everything here minimal and dialect-swappable, and do **not** build SQLite-specific
migration test harnesses or tooling beyond what the verification steps below need.

## What this phase fixes (two verified bugs)

1. **A fresh clone cannot create the database.** `/data/` is gitignored, so `data/` does
   not exist after clone; `npm run db:push` (the documented first step) fails because
   drizzle-kit cannot `mkdir` it. Fixed by `npm run db:init`, whose script `mkdir -p`s
   `data/` before touching sqlite.
2. **Schema changes silently no-op against an existing database.** `drizzle/` is gitignored
   and regenerated inside the Docker build, so `drizzle-kit generate` always emits a
   *full-schema* `0000_<random>.sql` (it has no journal to diff against). `scripts/init-db.mjs`
   then rewrites every `CREATE TABLE`/`CREATE INDEX` to `IF NOT EXISTS`, swallows
   `already exists`/`duplicate column name` errors, records the file as applied and exits 0.
   A new column therefore never reaches production and nothing reports it. Fixed by
   committing `drizzle/` (real incremental diffs) and replacing the hand-rolled applier with
   drizzle's `migrate()`, which applies statements verbatim and fails loudly.

---

## Risks & open questions (read before starting)

- **R1 — Node version.** `CLAUDE.md` says Node 22 lives at `$HOME/.nvm/versions/node/v22.23.1/bin`.
  **That path does not exist on this machine**; `node -v` is `v26.7.0` (homebrew), and
  `better-sqlite3` is currently built against it (`require('better-sqlite3')` works, 210 tests pass).
  Prepending the nvm path is harmless (it just falls through), so keep doing it for consistency
  with project docs, but do not assume Node 22 locally. The Docker image is `node:22-alpine`
  and is unaffected. Do not "fix" `CLAUDE.md`'s environment section in this phase — ask the user.
- **R2 — The existing production database must be adopted, not re-created.** It has all app
  tables plus `__app_migrations`, and no `__drizzle_migrations`. Running a bare `migrate()`
  against it throws `table machines already exists` and the container would crash-loop on start.
  Task 4 therefore contains a one-shot **baseline/adoption** block. It is verified below
  (Task 4 verification B) and must not be skipped. Mark it in the source as *delete in Phase 2*
  (Postgres starts empty and is filled by the one-time import script; nothing to adopt).
- **R3 — `drizzle-orm/better-sqlite3/migrator` is not in the standalone output.**
  Next.js traces only modules the app actually imports; `migrator.js` (and the root
  `migrator.js` it pulls in) are imported by nothing in `src/`, so `.next/standalone/node_modules/drizzle-orm`
  would lack them and the container entrypoint would die with `ERR_MODULE_NOT_FOUND`.
  Task 7 copies the whole `drizzle-orm` package into the runner image. **This cannot be
  verified without a Docker build** — see Task 7's hand-off snippet.
- **R4 — `db:push` is removed** (Task 5). Keeping it is a footgun: a `push` against a database
  that already has a `__drizzle_migrations` row makes the *next* generated `ALTER TABLE`
  fail with `duplicate column name`. If the user objects, the fallback is to keep the script
  but document it as dev-scratch-only, never against `data/app.db`.
- **R5 — `DROP TABLE __app_migrations` during adoption is one-way.** It is safe (that table only
  ever tracked our own applier, and the schema it described is now the baseline), but a rollback
  to the pre-phase-1 code would re-apply the old full-schema file — harmless, because every
  statement is `IF NOT EXISTS`-rewritten there. Flagged, not blocking.
- **R6 — Local `data/app.db` is a broken artifact.** It is a 4096-byte file with **zero tables**
  (a failed first `db:push`). Task 9 deletes and recreates it. Nothing of value is lost — confirmed
  by `SELECT name FROM sqlite_master` returning `[]`.
- **Open question (non-blocking):** no automated test covers the migration path; verification is
  the manual command list below. Writing a vitest suite around SQLite migration mechanics is
  exactly the work Phase 2 throws away, so it is deliberately out of scope.

Everything in this plan except R3 has been prototyped and verified end-to-end in a scratch
directory against this repo's `drizzle-kit@0.31.10` / `drizzle-orm@0.45.2`.

---

## Conventions for every task

- Shell prefix for all commands: `export PATH="$HOME/.nvm/versions/node/v22.23.1/bin:$PATH"` (see R1).
- Repo root: `<repo root>`. All paths below are repo-relative.
- Work on branch `master` (project convention; remote is Azure DevOps). Do not commit unless asked.
- A scratch dir for verification: use `$SCRATCH` = your session scratchpad. Never point verification
  commands at `data/app.db` unless the task says so.

---

## Task 1 — Move `__app_seeds` into `src/db/schema.ts`

**Why:** the seed ledger is currently created ad hoc by `scripts/seed-prompts.mjs`. Once
`drizzle-kit` owns the schema, a table it does not know about is a table `push`-style tooling
will offer to drop, and a table the Phase-2 Postgres port would forget.

**Edit `src/db/schema.ts`** — append at the end of the file (after the `runResults` exports):

```ts
// ---------------------------------------------------------------------------
// __app_seeds — ledger owned by scripts/seed-prompts.mjs
//
// Not application data: it records which seeded toolsets/prompt groups have ever
// been inserted, so seeding is additive and respects deletions. It lives here (and
// not only in the seed script) so migration tooling knows it exists and can never
// offer to drop it. `scope` is the group name for a prompt, empty for a toolset.
// ---------------------------------------------------------------------------
export const appSeeds = sqliteTable(
  '__app_seeds',
  {
    kind: text('kind').notNull(),
    scope: text('scope').notNull(),
    name: text('name').notNull(),
    seededAt: integer('seeded_at', { mode: 'number' }).notNull(),
  },
  (table) => [primaryKey({ columns: [table.kind, table.scope, table.name] })],
);
```

`sqliteTable`, `text`, `integer`, `primaryKey` are already imported at the top of the file.
Column names/types/PK order must match the DDL currently in `scripts/seed-prompts.mjs` exactly
(`kind, scope, name, seeded_at`, PK `(kind, scope, name)`) — an existing production database
already has this table and the baseline migration must describe it identically.

**Verify:** `npx tsc --noEmit` → exits 0.

---

## Task 2 — Un-ignore `drizzle/`

**Edit `.gitignore`** — delete the last block (lines ~46-48):

```
# SQL generated from src/db/schema.ts (created by `drizzle-kit generate`,
# regenerated inside the Docker build)
/drizzle/
```

Replace it with nothing, or with a comment noting that `drizzle/` is now committed.
Keep `/data/` ignored.

**Edit `.dockerignore`** — remove the line `drizzle`. Keep `data` and `data-docker-test` ignored
(the local database must never be baked into the image).

**Verify:**

```bash
git check-ignore -v drizzle 2>&1 || echo "drizzle NOT ignored (correct)"
git check-ignore -v data/app.db   # must still print a .gitignore match
```

Expected: first prints `drizzle NOT ignored (correct)`, second prints `.gitignore:44:/data/	data/app.db`.

---

## Task 3 — Generate the committed baseline migration

**Run** (from repo root):

```bash
npx drizzle-kit generate --name baseline
```

`drizzle.config.ts` already sets `dialect: 'sqlite'`, `schema: './src/db/schema.ts'`, `out: './drizzle'`
— leave it unchanged (its `dbCredentials.url` is unused by `generate` and Phase 2 rewrites the file).
`--name baseline` matters: without it drizzle-kit invents a random suffix
(`0000_violet_peter_parker.sql`), which is a poor thing to commit and to reference in the adoption code.

**Expect** to be created:

- `drizzle/0000_baseline.sql` — 11 `CREATE TABLE` statements (`__app_seeds`, `machine_models`,
  `machines`, `prompt_groups`, `prompt_toolsets`, `prompts`, `run_results`, `runs`, `system_prompts`,
  `tools`, `toolsets`) plus 2 unique indexes, separated by `--> statement-breakpoint`.
- `drizzle/meta/0000_snapshot.json`
- `drizzle/meta/_journal.json` — one entry, `"tag": "0000_baseline"`, `"breakpoints": true`.

**Verify:**

```bash
grep -c 'CREATE TABLE' drizzle/0000_baseline.sql          # → 11
grep -q '__app_seeds' drizzle/0000_baseline.sql && echo ok  # → ok
node -e "console.log(JSON.parse(require('fs').readFileSync('drizzle/meta/_journal.json','utf8')).entries.map(e=>e.tag))"
# → [ '0000_baseline' ]
git status --short drizzle    # → untracked files listed (proves Task 2 worked)
```

**Do not hand-edit any file under `drizzle/` from here on** — the `.sql` hash is the migration
identity in drizzle's ledger. Future schema changes are `npx drizzle-kit generate --name <slug>`,
which emits `0001_<slug>.sql` as a real diff (verified: adding a column produces exactly
`ALTER TABLE \`machines\` ADD \`smoke_col\` text;`).

---

## Task 4 — Replace `scripts/init-db.mjs` with drizzle's migrator

**Rewrite `scripts/init-db.mjs` entirely** with the content below. It is short on purpose; the only
non-obvious part is the adoption block, which is prototyped and verified.

```js
#!/usr/bin/env node
/**
 * Schema bootstrap. Applies the committed migrations under `drizzle/` to
 * `data/app.db` — run in development via `npm run db:init` and at container
 * start-up from `docker-entrypoint.sh`.
 *
 * The migrations are drizzle's own (`drizzle-kit generate` writes them,
 * `migrate()` applies them, `__drizzle_migrations` is its ledger). Statements
 * are applied verbatim: a migration that cannot apply cleanly fails loudly
 * instead of being rewritten into a no-op, which is what the previous
 * hand-rolled applier did.
 *
 * PHASE 2 (Postgres) replaces the three sqlite imports and deletes
 * `adoptExistingDatabase()` — a fresh Postgres database has nothing to adopt.
 */
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import Database from 'better-sqlite3';
import { drizzle } from 'drizzle-orm/better-sqlite3';
import { migrate } from 'drizzle-orm/better-sqlite3/migrator';

const root = process.env.APP_ROOT ?? process.cwd();
const dataDir = process.env.DATA_DIR ?? path.join(root, 'data');
const dbPath = path.join(dataDir, 'app.db');
const migrationsFolder = path.join(root, 'drizzle');

/**
 * One-shot adoption of a database that predates this migrator: it already has
 * the app's tables (created by `drizzle-kit push`, or by the old init-db.mjs
 * applier) but no drizzle ledger, so `migrate()` would try to CREATE them again
 * and abort. Record the baseline as applied without executing it; anything
 * after the baseline still runs normally, because the sqlite migrator compares
 * the journal's `when` against the newest ledger row.
 */
function adoptExistingDatabase(sqlite) {
  const journal = JSON.parse(
    fs.readFileSync(path.join(migrationsFolder, 'meta/_journal.json'), 'utf8'),
  );
  const baseline = journal.entries[0];
  const sql = fs.readFileSync(path.join(migrationsFolder, `${baseline.tag}.sql`), 'utf8');
  const hash = crypto.createHash('sha256').update(sql).digest('hex');

  // Same DDL and same insert shape drizzle's own migrator uses (`id SERIAL` is
  // not a rowid alias in SQLite, so the column is left out of the insert).
  sqlite.exec(
    'CREATE TABLE IF NOT EXISTS __drizzle_migrations (id SERIAL PRIMARY KEY, hash text NOT NULL, created_at numeric)',
  );
  sqlite
    .prepare('INSERT INTO __drizzle_migrations ("hash", "created_at") VALUES (?, ?)')
    .run(hash, baseline.when);

  // Ledger of the retired hand-rolled applier; nothing reads it any more.
  sqlite.exec('DROP TABLE IF EXISTS __app_migrations');

  console.log(`[init-db] existing database adopted at baseline ${baseline.tag}`);
}

function main() {
  if (!fs.existsSync(path.join(migrationsFolder, 'meta/_journal.json'))) {
    console.error(
      `[init-db] no migrations found at ${migrationsFolder}. ` +
        'Run `npx drizzle-kit generate` and commit the result.',
    );
    process.exit(1);
  }

  // The one thing a fresh clone needs: `data/` is gitignored and does not exist.
  fs.mkdirSync(dataDir, { recursive: true });

  const sqlite = new Database(dbPath);
  sqlite.pragma('journal_mode = WAL');
  // foreign_keys is deliberately left OFF here: drizzle's sqlite migrator wraps
  // everything in one transaction, and a table-recreation migration needs FKs
  // disabled. The app turns them ON for its own connection (src/db/index.ts).

  const tableExists = (name) =>
    !!sqlite
      .prepare("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?")
      .get(name);
  const ledgerRows = tableExists('__drizzle_migrations')
    ? sqlite.prepare('SELECT count(*) AS c FROM __drizzle_migrations').get().c
    : 0;

  if (ledgerRows === 0 && tableExists('machines')) {
    adoptExistingDatabase(sqlite);
  }

  migrate(drizzle(sqlite), { migrationsFolder });

  const applied = sqlite.prepare('SELECT count(*) AS c FROM __drizzle_migrations').get().c;
  sqlite.close();
  console.log(`[init-db] schema ready — ${applied} migration(s) recorded (${dbPath})`);
}

main();
```

Notes for the implementor:

- `machines` is the adoption probe because it is the oldest table and exists in every database
  that ever ran. Do not probe `__app_seeds` (it may be absent) or `sqlite_master` count (WAL/
  `sqlite_sequence` noise).
- Do **not** re-add the `IF NOT EXISTS` rewriting or the `ALREADY_APPLIED` error swallowing.
  Failing loudly is the point of the phase.
- Keep `APP_ROOT` / `DATA_DIR` env overrides: the verification steps and the container both use them.

**Verify A (fresh database):**

```bash
S=$SCRATCH/mig-a && mkdir -p $S && ln -sfn "$PWD/node_modules" $S/node_modules && ln -sfn "$PWD/drizzle" $S/drizzle
APP_ROOT=$S node scripts/init-db.mjs
APP_ROOT=$S node scripts/init-db.mjs   # second run
node -e "const D=require('better-sqlite3'),d=new D('$S/data/app.db');
console.log(d.prepare(\"select count(*) c from sqlite_master where type='table'\").get());
console.log(d.prepare('select hash, created_at from __drizzle_migrations').all())"
```

Expected: both runs print `schema ready — 1 migration(s) recorded`; 13 tables
(11 app tables + `__drizzle_migrations` + `sqlite_sequence`); exactly **one** ledger row.

**Verify B (adoption of a pre-migrator database — this is the production path, R2):**

```bash
S=$SCRATCH/mig-b && mkdir -p $S/data && ln -sfn "$PWD/drizzle" $S/drizzle
cp $SCRATCH/mig-a/data/app.db $S/data/app.db
node -e "const D=require('better-sqlite3'),d=new D('$S/data/app.db');
d.exec('DROP TABLE __drizzle_migrations');
d.exec('CREATE TABLE __app_migrations (name TEXT PRIMARY KEY, applied_at INTEGER NOT NULL)');
d.prepare('insert into __app_migrations values (?,?)').run('0000_old.sql', 1);
d.prepare(\"insert into machines (name,base_url,created_at,updated_at) values ('keepme','http://x',1,1)\").run()"
APP_ROOT=$S node scripts/init-db.mjs
node -e "const D=require('better-sqlite3'),d=new D('$S/data/app.db');
console.log(d.prepare('select count(*) c from machines').get());
console.log(d.prepare(\"select name from sqlite_master where name like '__%'\").all())"
```

Expected: `existing database adopted at baseline 0000_baseline`, then
`schema ready — 1 migration(s) recorded`; **no error**; `machines` still has its 1 row;
`__app_migrations` gone, `__drizzle_migrations` present.

**Verify C (an incremental migration actually applies — the bug this phase fixes):**

```bash
# add a throwaway column
perl -0pi -e "s/  notes: text\('notes'\),/  notes: text('notes'),\n  smokeCol: text('smoke_col'),/" src/db/schema.ts
npx drizzle-kit generate --name smoke
cat drizzle/0001_smoke.sql          # → ALTER TABLE `machines` ADD `smoke_col` text;
APP_ROOT=$SCRATCH/mig-b node scripts/init-db.mjs   # against the ADOPTED database
node -e "const D=require('better-sqlite3'),d=new D('$SCRATCH/mig-b/data/app.db');
console.log(d.prepare('pragma table_info(machines)').all().map(c=>c.name).includes('smoke_col'))"
# → true, and init-db reports 2 migration(s) recorded

# ROLL BACK the smoke test completely:
git checkout src/db/schema.ts
rm drizzle/0001_smoke.sql drizzle/meta/0001_snapshot.json
node -e "const fs=require('fs'),p='drizzle/meta/_journal.json',j=JSON.parse(fs.readFileSync(p,'utf8'));
j.entries=j.entries.filter(e=>e.tag!=='0001_smoke');fs.writeFileSync(p,JSON.stringify(j,null,2))"
git status --short   # only Task 1-3 changes remain; drizzle/ has 0000_baseline only
```

That rollback is the one sanctioned hand-edit of `drizzle/meta/_journal.json`, and only because
the migration was never committed. Scratch databases can be thrown away afterwards.

---

## Task 5 — npm scripts

**Edit `package.json`**, `scripts` block:

- **Remove** `"db:push": "drizzle-kit push"` (see R4 — it desynchronises the ledger).
- **Add:**
  ```json
  "db:generate": "drizzle-kit generate",
  "db:migrate": "node scripts/init-db.mjs",
  "db:init": "drizzle-kit generate && node scripts/init-db.mjs",
  ```
- Keep `db:seed` unchanged.

`db:init` is the one command a fresh clone runs; `generate` is a no-op printing
`No schema changes, nothing to migrate` when `schema.ts` matches the latest snapshot.
`db:migrate` alone is what you run when someone else's migration arrived via git.

**Verify:**

```bash
npm run db:init      # against the real data/app.db — do Task 8 first, it is still the broken file
npm pkg get scripts  # db:push absent; db:generate/db:migrate/db:init present
```

---

## Task 6 — `scripts/seed-prompts.mjs` stops owning `__app_seeds`

**Edit `scripts/seed-prompts.mjs`:**

1. Delete the `db.exec('CREATE TABLE IF NOT EXISTS __app_seeds (...)')` call (around line 1079,
   inside `main()`), together with its `// Ledger of what this script has ever seeded.` comment
   body about creating it. The table now comes from the baseline migration.
2. Update the header comment (lines ~9-16): keep the description of the idempotency ledger and
   the backfill behaviour, but replace the parenthetical
   `` (`__app_seeds` is owned by this script, in the same spirit as `__app_migrations` in `init-db.mjs`; neither belongs in `src/db/schema.ts`.) ``
   with something like:
   `` (`__app_seeds` is declared in `src/db/schema.ts` and created by the migrations, so schema tooling knows about it; this script only writes rows. Run `npm run db:init` first.) ``
3. Leave the `wasSeeded` / `markSeeded` prepared statements and the backfill logic untouched.

**Verify:**

```bash
S=$SCRATCH/mig-a
APP_ROOT=$S node scripts/seed-prompts.mjs
APP_ROOT=$S node scripts/seed-prompts.mjs   # second run must be a no-op
node -e "const D=require('better-sqlite3'),d=new D('$S/data/app.db');
console.log(d.prepare('select count(*) c from prompts').get(), d.prepare('select count(*) c from __app_seeds').get())"
```

Expected: first run seeds, second run reports nothing new; prompt count identical after both runs
(no duplicates); `__app_seeds` populated.

---

## Task 7 — Docker: ship the committed migrations and the migrator module

**Edit `Dockerfile`:**

1. In the **builder** stage, delete:
   ```dockerfile
   # drizzle/*.sql is generated from src/db/schema.ts here, so the runner image can
   # create the schema without drizzle-kit or TypeScript.
   RUN npx drizzle-kit generate
   ```
   and replace it with a fail-fast guard plus a comment explaining that `drizzle/` now arrives
   from the build context (`COPY . .`) because it is committed:
   ```dockerfile
   # drizzle/ is committed, so the image ships the exact migrations that were reviewed.
   # Generating here would re-derive a full-schema baseline and defeat incremental diffs.
   RUN test -f drizzle/meta/_journal.json \
     || (echo 'drizzle/ missing — run `npm run db:generate` and commit it' && exit 1)
   ```
2. In the **runner** stage, after `COPY --from=builder /app/.next/standalone ./`, add:
   ```dockerfile
   # scripts/init-db.mjs runs outside Next, so it needs drizzle-orm resolvable from
   # /app/node_modules. The standalone trace only carries the modules the app itself
   # imports — `drizzle-orm/better-sqlite3/migrator` is not one of them (R3).
   COPY --from=deps /app/node_modules/drizzle-orm ./node_modules/drizzle-orm
   ```
   It must come **after** the standalone COPY so it overwrites the partial traced copy, and
   **before**/independent of the `drizzle` and `scripts` copies (which stay as they are).
3. Leave `serverExternalPackages`/`outputFileTracingIncludes` in `next.config.ts` alone —
   `better-sqlite3` and its prebuilds are already handled, and adding `drizzle-orm` to the
   trace globs would bloat every route's trace for a start-up-only need.

**Edit `docker-entrypoint.sh`** — the command stays `node /app/scripts/init-db.mjs`; update the
comment to say the schema is applied from the **committed** migrations under `/app/drizzle`, and
that `set -e` means a failed migration stops the container instead of serving a half-migrated
database (which is the intended behaviour now that failures are loud).

**Verify:** static review only unless Docker is available:

```bash
grep -n 'drizzle' Dockerfile          # no `drizzle-kit generate`; journal guard + drizzle-orm COPY present
```

Docker build/run verification is **handed to the user** (per `CLAUDE.md`, production container
actions are run by the user). Give them this snippet to run:

```bash
docker build -t agent-val-p1 .
docker run --rm -v "$PWD/data-docker-test:/app/data" agent-val-p1 node /app/scripts/init-db.mjs
# expected: "[init-db] schema ready — 1 migration(s) recorded (/app/data/app.db)"
```

Failure mode to watch for: `ERR_MODULE_NOT_FOUND: drizzle-orm/better-sqlite3/migrator` → step 2 was
missed or ordered before the standalone COPY.

---

## Task 8 — Rebuild the local database

`data/app.db` is a zero-table 4096-byte leftover from a failed `db:push` (R6). Replace it:

```bash
rm -f data/app.db data/app.db-wal data/app.db-shm
npm run db:init
npm run db:seed
node -e "const D=require('better-sqlite3'),d=new D('data/app.db');
console.log(d.prepare(\"select name from sqlite_master where type='table' order by name\").all().map(r=>r.name).join(','));
console.log(d.prepare('select count(*) c from prompts').get())"
```

Expected: all 11 app tables + `__drizzle_migrations` + `sqlite_sequence`, prompts seeded (> 0).
Then start the app and click through once:

```bash
npm run dev   # http://localhost:3000/agent-val — Prompts and Runs pages render, no 500s
```

---

## Task 9 — README

**Edit `README.md`:**

1. **Development** block — fix the setup order and the command:
   ```bash
   nvm use 22
   npm install
   npm run db:init   # create/update data/app.db from the migrations in drizzle/
   npm run db:seed   # optional: ready-made toolsets and prompt groups
   npm run dev       # http://localhost:3000/agent-val (the app lives under its basePath)
   ```
2. Replace the paragraph beginning `` `npm run db:push` applies `src/db/schema.ts` directly … there are no checked-in migrations. `` with the new model: `src/db/schema.ts` is the source of truth; `npm run db:generate` writes an incremental SQL migration under `drizzle/` (committed); `npm run db:migrate` applies pending ones; `db:init` is both. Note that `drizzle/` files are never hand-edited and that a migration is reviewed like code.
3. **Schema bootstrap on start** section — rewrite. Drop `__app_migrations` and the `IF NOT EXISTS`
   rewriting entirely. New content: migrations are committed and copied into the image; the
   entrypoint runs `scripts/init-db.mjs`, which calls drizzle's `migrate()` and records applied
   files in drizzle's own `__drizzle_migrations`; statements are applied verbatim so a broken
   migration stops the container rather than silently no-opping. Keep the closing rationale
   (no drizzle-kit / TypeScript in the runner image) but correct it: the runner also carries the
   `drizzle-orm` package for the migrator.
4. Mention once, near the bootstrap section, that a database predating this setup is adopted
   automatically on first start (baseline recorded, not re-applied).

**Verify:** `grep -n 'db:push\|__app_migrations\|IF NOT EXISTS' README.md` → no matches.

---

## Task 10 — CLAUDE.md

**Edit `CLAUDE.md`:**

1. **Commands** block: replace `npm run db:push  # drizzle-kit push …` with
   `npm run db:init   # generate pending migration SQL + apply drizzle/ to data/app.db`
   and add `npm run db:generate` / `npm run db:migrate`. Keep `db:seed`.
2. Delete the paragraph starting `` `db:push` needs a TTY and will stall in a piped shell … `` and its
   `npx drizzle-kit generate && node scripts/init-db.mjs` fallback — that fallback **is** `db:init` now.
   Replace with one line: migrations are committed under `drizzle/`; `drizzle-kit generate` can still
   prompt interactively when it suspects a *rename*, so run it in a real terminal when renaming a
   table or column.
3. **Deployment** section: update the schema-bootstrap sentence — `scripts/init-db.mjs` applies the
   **committed** migrations with drizzle's `migrate()` (ledger `__drizzle_migrations`); `__app_migrations`
   and `IF NOT EXISTS`-rewriting are retired.
4. **Seeding** section: `__app_seeds` is now declared in `src/db/schema.ts` (created by the migrations);
   the script still owns the *rows* and the idempotency semantics — do not change that wording.

**Verify:** `grep -n 'db:push\|__app_migrations' CLAUDE.md` → no matches.

---

## Phase verification (run all, in order, from repo root)

```bash
export PATH="$HOME/.nvm/versions/node/v22.23.1/bin:$PATH"

npm test              # → 10 files, 210 tests passed (unchanged; nothing here touches tested code)
npx tsc --noEmit      # → exits 0, no output
npm run lint          # → no new errors
npm run build         # → compiles; standalone output written
```

Phase-specific checks:

```bash
# 1. drizzle/ is tracked and complete
git status --short drizzle           # 0000_baseline.sql + meta/0000_snapshot.json + meta/_journal.json
git check-ignore drizzle || echo "not ignored (correct)"

# 2. a from-scratch clone path works (the bug-1 regression test)
S=$SCRATCH/clone && rm -rf $S && mkdir -p $S && ln -sfn "$PWD/node_modules" $S/node_modules && ln -sfn "$PWD/drizzle" $S/drizzle
ls $S/data 2>/dev/null && echo "FAIL: data/ pre-exists"   # must NOT exist
APP_ROOT=$S node scripts/init-db.mjs                       # must create it and report 1 migration
APP_ROOT=$S node scripts/seed-prompts.mjs                  # must seed without a missing-table error

# 3. the bug-2 regression test lives in Task 4C: an ALTER really reaches an already-migrated
#    database and init-db reports 2 migrations. Re-run it here if Task 4 was changed since.

# 4. no dead references
grep -rn '__app_migrations\|db:push' --include='*.ts' --include='*.mjs' --include='*.md' \
  --include='Dockerfile' --include='*.sh' --include='*.json' . | grep -v node_modules | grep -v docs/superpowers
# → no matches
```

Expected end state: `drizzle/` committed with one baseline migration; `scripts/init-db.mjs` ~90 lines
using `migrate()`; `db:push` gone; `data/app.db` recreated and seeded; README/CLAUDE.md consistent;
Docker build verification handed to the user with the exact commands from Task 7.

Hand-off note for the reviewer: Task 4's adoption block and Task 7's `drizzle-orm` COPY are the two
places where a mistake only shows up in production. Both have an explicit failure signature listed above.
