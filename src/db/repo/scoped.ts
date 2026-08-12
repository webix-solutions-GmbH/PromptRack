/**
 * Shared machinery for the repositories.
 *
 * Everything here may import the `db` handle; nothing outside `src/db/**` may
 * (ESLint enforces it). The repositories are the only place a query is written,
 * and every one of their exported functions takes a {@link Scope} first.
 */

import type { AnyColumn, SQL } from 'drizzle-orm';
import { db } from '@/db';
import type { Scope, ScopedRootTable } from '../scope';

/**
 * What a repository function runs its query on: the pool, or an open
 * transaction. Callers inside `src/db/**` pass a transaction when several
 * writes have to land as one unit; everyone else takes the default.
 */
export type DbHandle = typeof db | Parameters<Parameters<typeof db.transaction>[0]>[0];

/**
 * Runs `fn` inside one transaction, handing it the transaction to pass on to the
 * repository functions it calls. The only way a transaction leaves this layer,
 * which is what keeps "several writes are one unit" a data-access concern rather
 * than something every caller could get subtly wrong.
 */
export function withTransaction<T>(fn: (tx: DbHandle) => Promise<T>): Promise<T> {
  return db.transaction((tx) => fn(tx));
}

/**
 * Restricts a child row to parents that are in scope.
 *
 * Child tables (`machine_models`, `tools`, `prompts`, `prompt_toolsets`,
 * `run_results`) never carry `customer_id` themselves — they inherit it through
 * their foreign key. Phase 3: undefined, there is one implicit workspace.
 */
export function scopeThroughParent(
  scope: Scope,
  childFk: AnyColumn,
  parentTable: ScopedRootTable,
  parentId: AnyColumn,
): SQL | undefined {
  void scope;
  void childFk;
  void parentTable;
  void parentId;
  // Phase 5: return inArray(
  //   childFk,
  //   db.select({ id: parentId }).from(parentTable).where(scopeWhere(scope, parentTable)),
  // );
  return undefined;
}

/**
 * Reads a nullable numeric aggregate (`avg`, and any `sum` that can come back
 * empty).
 *
 * `Number(null)` is `0`, so `.mapWith(Number)` on an aggregate over no rows
 * would turn "nothing was measured" into "0 tok/s". Null has to survive. Counts
 * are the opposite case and keep `.mapWith(Number)`: Postgres returns `bigint`
 * as a string, and a count is never null.
 */
export function num(value: unknown): number | null {
  if (value === null || value === undefined) return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}
