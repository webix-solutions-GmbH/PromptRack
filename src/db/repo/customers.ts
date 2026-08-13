/**
 * Customer workspaces.
 *
 * The one family of queries that is *about* workspaces rather than inside one,
 * so these functions take no {@link Scope}: a scope is derived *from* a
 * customer, and asking "which workspaces exist" under one workspace's scope
 * would be circular. Authorization is the role gate in `src/actions/customers.ts`
 * — every signed-in user may see and switch into every workspace, which is what
 * "a workspace is a label, not a tenant" means.
 */

import { and, asc, eq, inArray, ne, sql, type SQL } from 'drizzle-orm';
import { db } from '@/db';
import {
  customers,
  machines,
  promptGroups,
  prompts,
  runs,
  systemPrompts,
  toolsets,
  type Customer,
} from '../schema';
import { requireCustomerId, type CustomerOption, type Scope } from '../scope';

/** Every workspace, oldest first — the order `Default` is the first row in. */
export function listCustomers(): Promise<Customer[]> {
  return db.select().from(customers).orderBy(asc(customers.id));
}

/** The switcher's view: id, name, and whether it is hidden. */
export async function listCustomerOptions(): Promise<CustomerOption[]> {
  const rows = await db
    .select({ id: customers.id, name: customers.name, archivedAt: customers.archivedAt })
    .from(customers)
    .orderBy(asc(customers.id));
  return rows.map((row) => ({ id: row.id, name: row.name, archived: row.archivedAt !== null }));
}

export async function getCustomer(id: number): Promise<Customer | null> {
  const [row] = await db.select().from(customers).where(eq(customers.id, id));
  return row ?? null;
}

/**
 * A workspace whose name matches case-insensitively, ignoring one id.
 *
 * The unique index would refuse a duplicate anyway; this exists so the action
 * can name the workspace that is in the way instead of surfacing a constraint
 * violation.
 */
export async function findCustomerByName(
  name: string,
  exceptId?: number,
): Promise<Customer | null> {
  const conditions: SQL[] = [sql`lower(${customers.name}) = lower(${name})`];
  if (exceptId !== undefined) conditions.push(ne(customers.id, exceptId));
  const [row] = await db
    .select()
    .from(customers)
    .where(conditions.length === 1 ? conditions[0] : and(...conditions));
  return row ?? null;
}

export async function createCustomer(values: {
  name: string;
  description: string | null;
  now: Date;
}): Promise<{ id: number }> {
  const [row] = await db
    .insert(customers)
    .values({
      name: values.name,
      description: values.description,
      createdAt: values.now,
      updatedAt: values.now,
    })
    .returning({ id: customers.id });
  return row;
}

export async function updateCustomer(
  id: number,
  values: { name: string; description: string | null; now: Date },
): Promise<void> {
  await db
    .update(customers)
    .set({ name: values.name, description: values.description, updatedAt: values.now })
    .where(eq(customers.id, id));
}

export async function setCustomerArchivedAt(id: number, archivedAt: Date | null): Promise<void> {
  await db
    .update(customers)
    .set({ archivedAt, updatedAt: new Date() })
    .where(eq(customers.id, id));
}

/** The FK `RESTRICT` is the backstop; the action's count check is the message. */
export async function deleteCustomer(id: number): Promise<void> {
  await db.delete(customers).where(eq(customers.id, id));
}

export interface CustomerContentCounts {
  machines: number;
  systemPrompts: number;
  toolsets: number;
  promptGroups: number;
  runs: number;
}

const COUNT = sql<number>`count(*)`.mapWith(Number);

/** What a workspace holds — the delete guard's message and the list page's columns. */
export async function countCustomerContent(id: number): Promise<CustomerContentCounts> {
  const [machineRows, systemPromptRows, toolsetRows, groupRows, runRows] = await Promise.all([
    db.select({ count: COUNT }).from(machines).where(eq(machines.customerId, id)),
    db.select({ count: COUNT }).from(systemPrompts).where(eq(systemPrompts.customerId, id)),
    db.select({ count: COUNT }).from(toolsets).where(eq(toolsets.customerId, id)),
    db.select({ count: COUNT }).from(promptGroups).where(eq(promptGroups.customerId, id)),
    db.select({ count: COUNT }).from(runs).where(eq(runs.customerId, id)),
  ]);

  return {
    machines: machineRows[0]?.count ?? 0,
    systemPrompts: systemPromptRows[0]?.count ?? 0,
    toolsets: toolsetRows[0]?.count ?? 0,
    promptGroups: groupRows[0]?.count ?? 0,
    runs: runRows[0]?.count ?? 0,
  };
}

/** Prompts per workspace, for the MCP `list_customers` view. */
export async function countPromptsByCustomer(): Promise<Map<number, number>> {
  const rows = await db
    .select({
      customerId: promptGroups.customerId,
      count: sql<number>`count(*)`.mapWith(Number),
    })
    .from(prompts)
    .innerJoin(promptGroups, eq(prompts.groupId, promptGroups.id))
    .groupBy(promptGroups.customerId);
  return new Map(rows.map((row) => [row.customerId, row.count]));
}

/**
 * Which workspace a run or a machine lives in — deliberately unscoped.
 *
 * The two detail pages use it to tell "does not exist" (404) apart from "exists
 * in another workspace" (offer to switch). It exposes nothing but the
 * workspace's name, which the switcher already lists for every user anyway.
 */
export async function findRunWorkspace(
  runId: number,
): Promise<{ id: number; name: string } | null> {
  const [row] = await db
    .select({ id: customers.id, name: customers.name })
    .from(runs)
    .innerJoin(customers, eq(runs.customerId, customers.id))
    .where(eq(runs.id, runId));
  return row ?? null;
}

export async function findMachineWorkspace(
  machineId: number,
): Promise<{ id: number; name: string } | null> {
  const [row] = await db
    .select({ id: customers.id, name: customers.name })
    .from(machines)
    .innerJoin(customers, eq(machines.customerId, customers.id))
    .where(eq(machines.id, machineId));
  return row ?? null;
}

/**
 * Refuses a write that would point a row at another workspace's row.
 *
 * The database cannot express this: children inherit scope through their parent,
 * so a link table has no `customer_id` to constrain, and adding one would
 * denormalise the column onto every child table. The three places it can happen
 * are exactly the three places two roots meet — a prompt's group, a prompt's
 * toolsets, a run's machine.
 *
 * A missing id and a foreign id are reported identically: to this caller the row
 * does not exist, and it has no business learning that it exists elsewhere.
 */
export async function assertSameCustomer(
  scope: Scope,
  refs: {
    machineIds?: number[];
    toolsetIds?: number[];
    groupIds?: number[];
    systemPromptIds?: number[];
  },
): Promise<void> {
  const customerId = requireCustomerId(scope);

  const found = await Promise.all([
    idsInWorkspace(refs.machineIds, (ids) =>
      db
        .select({ id: machines.id })
        .from(machines)
        .where(and(eq(machines.customerId, customerId), inArray(machines.id, ids))),
    ),
    idsInWorkspace(refs.toolsetIds, (ids) =>
      db
        .select({ id: toolsets.id })
        .from(toolsets)
        .where(and(eq(toolsets.customerId, customerId), inArray(toolsets.id, ids))),
    ),
    idsInWorkspace(refs.groupIds, (ids) =>
      db
        .select({ id: promptGroups.id })
        .from(promptGroups)
        .where(and(eq(promptGroups.customerId, customerId), inArray(promptGroups.id, ids))),
    ),
    idsInWorkspace(refs.systemPromptIds, (ids) =>
      db
        .select({ id: systemPrompts.id })
        .from(systemPrompts)
        .where(and(eq(systemPrompts.customerId, customerId), inArray(systemPrompts.id, ids))),
    ),
  ]);

  const labels = ['machine', 'toolset', 'prompt group', 'system prompt'];
  const wanted = [refs.machineIds, refs.toolsetIds, refs.groupIds, refs.systemPromptIds];

  for (const [index, present] of found.entries()) {
    const asked = [...new Set(wanted[index] ?? [])];
    const missing = asked.filter((id) => !present.has(id));
    if (missing.length === 0) continue;

    const label = labels[index];
    throw new Error(
      missing.length === 1
        ? `The selected ${label} (id ${missing[0]}) no longer exists in this workspace.`
        : `The selected ${label}s (ids ${missing.join(', ')}) no longer exist in this workspace.`,
    );
  }
}

async function idsInWorkspace(
  ids: number[] | undefined,
  query: (ids: number[]) => Promise<{ id: number }[]>,
): Promise<Set<number>> {
  const wanted = [...new Set(ids ?? [])];
  if (wanted.length === 0) return new Set();
  return new Set((await query(wanted)).map((row) => row.id));
}
