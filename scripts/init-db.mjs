#!/usr/bin/env node
/**
 * Schema bootstrap for the container start-up.
 *
 * The SQL under `drizzle/` is generated from `src/db/schema.ts` at image build
 * time (`drizzle-kit generate`). At runtime this script applies any migration
 * files the database has not seen yet, which is what makes a completely empty
 * `./data` volume come up with a working schema.
 *
 * It deliberately depends on nothing but `better-sqlite3` (already present in
 * the standalone output), so the runner image does not need drizzle-kit,
 * TypeScript, or the dev dependencies.
 *
 * Idempotency: applied files are recorded in `__app_migrations`, and
 * `CREATE TABLE`/`CREATE INDEX` are rewritten to `IF NOT EXISTS` so a database
 * that was originally created with `drizzle-kit push` in development can be
 * mounted here without exploding.
 */
import fs from 'node:fs';
import path from 'node:path';
import Database from 'better-sqlite3';

const root = process.env.APP_ROOT ?? process.cwd();
const dataDir = process.env.DATA_DIR ?? path.join(root, 'data');
const dbPath = path.join(dataDir, 'app.db');
const migrationsDir = path.join(root, 'drizzle');

/** Errors that mean "this object is already there" — safe to skip. */
const ALREADY_APPLIED = /already exists|duplicate column name/i;

function statementsOf(sql) {
  return sql
    .split('--> statement-breakpoint')
    .map((statement) => statement.trim())
    .filter((statement) => statement.length > 0)
    .map((statement) =>
      statement
        .replace(/^CREATE TABLE (?!IF NOT EXISTS)/i, 'CREATE TABLE IF NOT EXISTS ')
        .replace(/^CREATE (UNIQUE )?INDEX (?!IF NOT EXISTS)/i, 'CREATE $1INDEX IF NOT EXISTS '),
    );
}

function main() {
  fs.mkdirSync(dataDir, { recursive: true });

  if (!fs.existsSync(migrationsDir)) {
    console.error(`[init-db] no migrations directory at ${migrationsDir}`);
    process.exit(1);
  }

  const files = fs
    .readdirSync(migrationsDir)
    .filter((file) => file.endsWith('.sql'))
    .sort();

  if (files.length === 0) {
    console.error(`[init-db] no .sql migrations found in ${migrationsDir}`);
    process.exit(1);
  }

  const db = new Database(dbPath);
  db.pragma('journal_mode = WAL');
  db.exec(
    'CREATE TABLE IF NOT EXISTS __app_migrations (name TEXT PRIMARY KEY, applied_at INTEGER NOT NULL)',
  );

  const isApplied = db.prepare('SELECT 1 FROM __app_migrations WHERE name = ?');
  const markApplied = db.prepare(
    'INSERT INTO __app_migrations (name, applied_at) VALUES (?, ?)',
  );

  let applied = 0;
  for (const file of files) {
    if (isApplied.get(file)) continue;

    const sql = fs.readFileSync(path.join(migrationsDir, file), 'utf8');
    db.transaction(() => {
      for (const statement of statementsOf(sql)) {
        try {
          db.exec(statement);
        } catch (error) {
          if (!ALREADY_APPLIED.test(String(error?.message))) throw error;
          console.log(`[init-db] ${file}: skipping existing object`);
        }
      }
      markApplied.run(file, Date.now());
    })();

    applied += 1;
    console.log(`[init-db] applied ${file}`);
  }

  db.close();
  console.log(
    applied === 0
      ? `[init-db] schema up to date (${dbPath})`
      : `[init-db] schema ready, ${applied} migration(s) applied (${dbPath})`,
  );
}

main();
