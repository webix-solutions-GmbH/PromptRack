/**
 * The active customer workspace of the current request.
 *
 * This is the impure half of `currentScope()` (`src/db/scope.ts`), kept apart so
 * that module — which every repository imports for the branded `Scope` — stays
 * free of the session, the auth tables and `next/headers`, and can therefore be
 * unit-tested without a database.
 *
 * The active workspace lives on the user row rather than in a cookie: it cannot
 * be forged from the client, it survives a session refresh, there is exactly one
 * place that says which workspace a user is in — and Next 16 would not let an
 * RSC render write a cookie anyway, which is why switching goes through a server
 * action.
 */

import 'server-only';
import { cache } from 'react';
import type { CustomerOption } from '@/db/scope';
import { resolveActiveCustomerId } from '@/db/scope';
import { listCustomerOptions } from '@/db/repo/customers';
import { requireActor } from '@/lib/auth/guards';
import { getActiveCustomerId, setActiveCustomerId } from '@/lib/auth/users';

export interface ActiveWorkspace {
  customerId: number;
  /** Every workspace, archived ones included — the switcher decides what to show. */
  customers: CustomerOption[];
}

/**
 * Resolves the request's workspace once and reuses it.
 *
 * `cache()` is per-request, so a page that reads the scope and then renders the
 * switcher — or an action that calls `currentScope()` three times — pays for one
 * lookup, not three.
 */
export const activeWorkspace = cache(async (): Promise<ActiveWorkspace> => {
  const actor = await requireActor();
  const [stored, customers] = await Promise.all([
    getActiveCustomerId(actor.userId),
    listCustomerOptions(),
  ]);

  const resolved = resolveActiveCustomerId(stored, customers);
  if (resolved === null) {
    // Only reachable if every workspace was deleted, which `deleteCustomer`
    // refuses for the last one — so this is a broken database, not a state a
    // user can navigate into.
    throw new Error(
      'No customer workspace exists. Create one before using the app (the migration seeds "Default").',
    );
  }

  // Self-healing: the stored pointer was stale (archived, or deleted out from
  // under the user), so write back what they actually landed in.
  if (resolved !== stored) {
    await setActiveCustomerId(actor.userId, resolved);
  }

  return { customerId: resolved, customers };
});

/** What `currentScope()` wraps into a branded scope. */
export async function activeCustomerId(): Promise<number> {
  return (await activeWorkspace()).customerId;
}
