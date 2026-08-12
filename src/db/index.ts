import { Pool } from 'pg';
import { drizzle } from 'drizzle-orm/node-postgres';
import * as schema from './schema';

/** Dev fallback so a fresh clone works with `npm run dev` and nothing else. */
export const DEV_DATABASE_URL = 'postgres://agentval:dev@127.0.0.1:5433/agentval';

export function resolveDatabaseUrl(): string {
  const url = process.env.DATABASE_URL;
  if (url && url.length > 0) return url;
  if (process.env.NODE_ENV === 'production') {
    throw new Error('DATABASE_URL is required in production.');
  }
  return DEV_DATABASE_URL;
}

function createPool() {
  return new Pool({
    connectionString: resolveDatabaseUrl(),
    // One connection is held for the whole lifetime of an executing run
    // (see src/lib/run-lock.ts), so this must exceed the number of runs that
    // can execute concurrently plus normal request concurrency.
    max: Number(process.env.DATABASE_POOL_MAX ?? 10),
    idleTimeoutMillis: 30_000,
  });
}

declare global {
  var __pgPool: Pool | undefined;
}

export const pool = globalThis.__pgPool ?? createPool();

if (process.env.NODE_ENV !== 'production') {
  globalThis.__pgPool = pool;
}

// A pool with no error handler crashes the process when the server closes an
// idle connection.
pool.on('error', (err) => {
  console.error('[db] idle client error', err);
});

export const db = drizzle(pool, { schema });
