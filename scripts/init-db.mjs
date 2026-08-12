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
