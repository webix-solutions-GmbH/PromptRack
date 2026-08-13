import { and, eq, type SQL } from 'drizzle-orm';
import type { machines, promptGroups, runs, systemPrompts, toolsets } from './schema';

declare const scopeBrand: unique symbol;

/** Where a scope came from: 'session' (a user), 'row' (derived from a record), 'system'. */
export type ScopeOrigin = 'session' | 'row' | 'system';

/**
 * Who a query is allowed to see. Unforgeable: the brand means no caller outside
 * this module can construct one, so "a query without a scope" cannot be written.
 *
 * `customerId` is null only for a {@link systemScope} — the deliberate,
 * grep-able escape hatch that spans every workspace. Everything else names
 * exactly one.
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

/**
 * The workspace a scope names, or a throw.
 *
 * Only a system scope can fail this, and only where a workspace is structurally
 * required (any insert, since a new row has to land in exactly one workspace).
 */
export function requireCustomerId(scope: Scope): number {
  if (scope.customerId === null) {
    throw new Error(
      'This operation needs a customer workspace, but it ran under the system scope.',
    );
  }
  return scope.customerId;
}

/**
 * The scope of the current request: the signed-in user's active workspace.
 *
 * The active workspace lives on the user row rather than in a cookie — it is
 * then impossible to forge from the client, it survives a session refresh, and
 * there is exactly one place that says which workspace a user is in. Cookies
 * could not carry it anyway: Next 16 only lets a Server Function or Route
 * Handler write one, never an RSC render.
 *
 * The impure half (session, user row, customer list) lives in the server-only
 * `@/lib/workspace` and is pulled in *dynamically*, so this module stays
 * importable by the database-free unit tests while the branded construction
 * stays here.
 */
export async function currentScope(): Promise<Scope> {
  const { activeCustomerId } = await import('@/lib/workspace');
  return makeScope(await activeCustomerId(), 'session');
}

/** Derived from a row that already carries its own scope (background work). */
export function scopeFromCustomerId(customerId: number): Scope {
  return makeScope(customerId, 'row');
}

/**
 * Explicit, grep-able escape hatch for migrations/admin. `reason` is
 * documentation.
 *
 * Reads under it span every workspace, which is what it is for; writes to a
 * scoped root table still fail, because an insert has no defensible workspace
 * to land in.
 */
export function systemScope(reason: string): Scope {
  void reason;
  return makeScope(null, 'system');
}

/** The five root tables that carry `customer_id`. */
export type ScopedRootTable =
  | typeof machines
  | typeof systemPrompts
  | typeof toolsets
  | typeof promptGroups
  | typeof runs;

/** Restricts a root-table query to the scope's workspace. */
export function scopeWhere(scope: Scope, table: ScopedRootTable): SQL | undefined {
  // A system scope is the documented "every workspace" read.
  if (scope.customerId === null) return undefined;
  return eq(table.customerId, scope.customerId);
}

/** Columns a new root row must carry. */
export function scopeValues(scope: Scope): { customerId: number } {
  return { customerId: requireCustomerId(scope) };
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

/** A workspace as the switcher and the MCP `list_customers` tool see it. */
export interface CustomerOption {
  id: number;
  name: string;
  archived: boolean;
}

/**
 * Which workspace a user lands in.
 *
 * `preferred` is their stored `active_customer_id`; it is ignored when it names
 * a workspace that no longer exists or has been archived, because a stale
 * pointer must degrade to a working session rather than an empty app. Falls back
 * to the oldest live workspace — with only the migration's `Default` present,
 * that is the one every existing install wants. An install whose every workspace
 * is archived still gets one, since an unusable app is worse than a hidden
 * workspace showing through.
 */
export function resolveActiveCustomerId(
  preferred: number | null,
  customers: readonly CustomerOption[],
): number | null {
  const live = customers.filter((customer) => !customer.archived);
  if (preferred !== null && live.some((customer) => customer.id === preferred)) return preferred;
  return live[0]?.id ?? customers[0]?.id ?? null;
}
