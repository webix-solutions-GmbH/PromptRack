#!/usr/bin/env node
/**
 * Applies the committed migrations under drizzle/ to the database named by
 * DATABASE_URL. Runs at container start (docker-entrypoint.sh) and in dev
 * (scripts/dev-db.mjs). Drizzle owns its own __drizzle_migrations ledger.
 */
import path from 'node:path';
import { Pool } from 'pg';
import { drizzle } from 'drizzle-orm/node-postgres';
import { migrate } from 'drizzle-orm/node-postgres/migrator';

const root = process.env.APP_ROOT ?? process.cwd();
const migrationsFolder = path.join(root, 'drizzle');
const connectionString =
  process.env.DATABASE_URL ?? 'postgres://agentval:dev@127.0.0.1:5433/agentval';

const pool = new Pool({ connectionString, max: 1 });
try {
  await migrate(drizzle(pool), { migrationsFolder });
  console.log(`[init-db] schema up to date (${redact(connectionString)})`);
} finally {
  await pool.end();
}

function redact(url) {
  try {
    const u = new URL(url);
    if (u.password) u.password = '***';
    return u.toString();
  } catch {
    return '(unparsable DATABASE_URL)';
  }
}
