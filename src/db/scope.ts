import { and, type SQL } from 'drizzle-orm';
import type { machines, promptGroups, runs, systemPrompts, toolsets } from './schema';

declare const scopeBrand: unique symbol;

/** Where a scope came from: 'session' (a user), 'row' (derived from a record), 'system'. */
export type ScopeOrigin = 'session' | 'row' | 'system';

/**
 * Who a query is allowed to see. Unforgeable: the brand means no caller outside
 * this module can construct one, so "a query without a scope" cannot be written.
 * Phase 3 has one implicit workspace, so `customerId` is always null.
 */
export interface Scope {
  readonly [scopeBrand]: true;
  readonly customerId: number | null;
  readonly origin: ScopeOrigin;
}

function makeScope(customerId: number | null, origin: ScopeOrigin): Scope {
  // The brand is phantom — it exists only in the type system, so the object
  // literal has to be cast into it.
  return { customerId, origin } as unknown as Scope;
}

/** Phase 3 has one implicit workspace; Phase 5 replaces this with a session read. */
const IMPLICIT_SCOPE = makeScope(null, 'session');

/**
 * The scope of the current request. Async already, so Phase 4's `await cookies()`
 * changes no call site. Phase 3: the single implicit workspace.
 */
export async function currentScope(): Promise<Scope> {
  // Phase 5: read the session and return makeScope(session.customerId, 'session').
  return IMPLICIT_SCOPE;
}

/** Derived from a row that already carries its own scope (background work). */
export function scopeFromCustomerId(customerId: number | null): Scope {
  return makeScope(customerId, 'row');
}

/** Explicit, grep-able escape hatch for migrations/admin. `reason` is documentation. */
export function systemScope(reason: string): Scope {
  void reason;
  return makeScope(null, 'system');
}

/** The five root tables that will carry `customer_id` in Phase 5. */
export type ScopedRootTable =
  | typeof machines
  | typeof systemPrompts
  | typeof toolsets
  | typeof promptGroups
  | typeof runs;

/** Phase 3: undefined. Phase 5: eq(table.customerId, scope.customerId). */
export function scopeWhere(scope: Scope, table: ScopedRootTable): SQL | undefined {
  void scope;
  void table;
  // Phase 5: return eq(table.customerId, requireCustomerId(scope));
  return undefined;
}

/** Columns a new root row must carry. Phase 3: {}. Phase 5: { customerId }. */
export function scopeValues(scope: Scope): Record<string, never> {
  void scope;
  // Phase 5: return { customerId: requireCustomerId(scope) };
  return {};
}

/** `and()` that tolerates undefined and collapses to undefined when empty. */
export function combine(conditions: readonly (SQL | undefined)[]): SQL | undefined {
  const present = conditions.filter((condition): condition is SQL => condition !== undefined);
  if (present.length === 0) return undefined;
  if (present.length === 1) return present[0];
  return and(...present);
}

/** The scope predicate for `table` AND-ed with the caller's own conditions. */
export function whereScoped(
  scope: Scope,
  table: ScopedRootTable,
  ...conditions: (SQL | undefined)[]
): SQL | undefined {
  return combine([scopeWhere(scope, table), ...conditions]);
}
