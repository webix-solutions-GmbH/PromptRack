import { pool } from '@/db';

/**
 * Namespace for this app's advisory locks. Postgres advisory locks are a
 * single global int8 (or two int4s) per database; the class id keeps run locks
 * from colliding with anything else that might use them later.
 */
const LOCK_CLASS = 1094992214; // 'AGEV' as int4

export interface RunLock {
  release(): Promise<void>;
}

/**
 * Claims exclusive execution of a run, across processes.
 *
 * The lock lives on a dedicated pooled connection held for the whole run, so it
 * is released automatically if the process dies — reproducing the semantics of
 * the in-memory Set it replaces (rows left in 'running' are reclaimed by the
 * next execution) while also being safe with more than one app process.
 *
 * Returns null when another execution already holds the run.
 */
export async function acquireRunLock(runId: number): Promise<RunLock | null> {
  const client = await pool.connect();
  let locked = false;
  try {
    const { rows } = await client.query<{ locked: boolean }>(
      'SELECT pg_try_advisory_lock($1, $2) AS locked',
      [LOCK_CLASS, runId],
    );
    locked = rows[0]?.locked === true;
  } catch (err) {
    client.release();
    throw err;
  }
  if (!locked) {
    client.release();
    return null;
  }

  let released = false;
  return {
    async release() {
      if (released) return;
      released = true;
      try {
        await client.query('SELECT pg_advisory_unlock($1, $2)', [LOCK_CLASS, runId]);
      } catch {
        // The connection is gone; the lock died with it.
      } finally {
        client.release();
      }
    },
  };
}

/**
 * Whether some process is executing this run. Read-only — it inspects pg_locks
 * rather than trying to take the lock.
 *
 * For a two-key advisory lock, pg_locks reports classid = first key,
 * objid = second key, objsubid = 2.
 */
export async function isRunExecuting(runId: number): Promise<boolean> {
  const { rows } = await pool.query(
    `SELECT 1 FROM pg_locks
      WHERE locktype = 'advisory'
        AND database = (SELECT oid FROM pg_database WHERE datname = current_database())
        AND classid = $1 AND objid = $2 AND objsubid = 2 AND granted`,
    [LOCK_CLASS, runId],
  );
  return rows.length > 0;
}
