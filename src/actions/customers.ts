'use server';

import { revalidatePath } from 'next/cache';
import {
  countCustomerContent,
  createCustomer as createCustomerRow,
  deleteCustomer as deleteCustomerRow,
  findCustomerByName,
  getCustomer,
  listCustomers,
  setCustomerArchivedAt,
  updateCustomer as updateCustomerRow,
} from '@/db/repo/customers';
import { setActiveCustomerId } from '@/lib/auth/users';
import { optionalString, requiredString } from '@/lib/form-data';
import { requireActor, requireAdmin, requireWriter } from '@/lib/auth/guards';

/** Everything a workspace change can be seen in — which is every list there is. */
function revalidateWorkspaces() {
  revalidatePath('/customers');
  revalidatePath('/', 'layout');
}

export async function createCustomer(formData: FormData) {
  await requireWriter();
  const name = requiredString(formData, 'name');
  const description = optionalString(formData, 'description');

  // Named rather than left to the unique index: a constraint violation names the
  // index, and the person needs to know *which* workspace is already called this.
  const clash = await findCustomerByName(name);
  if (clash) {
    throw new Error(`A workspace named "${clash.name}" already exists (id ${clash.id}).`);
  }

  await createCustomerRow({ name, description, now: new Date() });

  revalidateWorkspaces();
}

export async function updateCustomer(id: number, formData: FormData) {
  await requireWriter();
  const name = requiredString(formData, 'name');
  const description = optionalString(formData, 'description');

  const clash = await findCustomerByName(name, id);
  if (clash) {
    throw new Error(`A workspace named "${clash.name}" already exists (id ${clash.id}).`);
  }

  await updateCustomerRow(id, { name, description, now: new Date() });

  revalidateWorkspaces();
}

/**
 * Hides a workspace from the switcher without touching anything it owns.
 *
 * Archiving the caller's own active workspace is allowed: the next
 * `currentScope()` falls back to the oldest live one and writes that back. That
 * fallback is exactly why archiving can be this unceremonious.
 */
export async function setCustomerArchived(id: number, archived: boolean) {
  await requireWriter();
  await setCustomerArchivedAt(id, archived ? new Date() : null);
  revalidateWorkspaces();
}

/**
 * Deletes an empty workspace. Admin-only, unlike the rest: a workspace holds
 * machines, i.e. base URLs and API keys, and the deletion is irreversible.
 *
 * The FK `RESTRICT` on all five root tables is the backstop; this check exists
 * to produce a sentence instead of a constraint violation.
 */
export async function deleteCustomer(id: number) {
  await requireAdmin();

  const customer = await getCustomer(id);
  if (!customer) {
    throw new Error('That workspace no longer exists.');
  }

  const all = await listCustomers();
  if (all.length <= 1) {
    // Every scope resolves to a workspace, so there has to be one left to
    // resolve to; archiving is the way to retire the last engagement.
    throw new Error('This is the only workspace. Archive it instead of deleting it.');
  }

  const counts = await countCustomerContent(id);
  const held = [
    counts.machines === 0 ? null : `${counts.machines} machine${counts.machines === 1 ? '' : 's'}`,
    counts.systemPrompts === 0
      ? null
      : `${counts.systemPrompts} system prompt${counts.systemPrompts === 1 ? '' : 's'}`,
    counts.toolsets === 0 ? null : `${counts.toolsets} toolset${counts.toolsets === 1 ? '' : 's'}`,
    counts.promptGroups === 0
      ? null
      : `${counts.promptGroups} prompt group${counts.promptGroups === 1 ? '' : 's'}`,
    counts.runs === 0 ? null : `${counts.runs} run${counts.runs === 1 ? '' : 's'}`,
  ].filter((entry): entry is string => entry !== null);

  if (held.length > 0) {
    throw new Error(
      `Workspace "${customer.name}" still holds ${held.join(', ')}. Archive it instead, or delete its contents first.`,
    );
  }

  await deleteCustomerRow(id);
  revalidateWorkspaces();
}

/**
 * Switches the caller into another workspace.
 *
 * Open to every role including `viewer` — switching is reading. It goes through
 * a server action rather than a cookie because the active workspace lives on the
 * user row (unforgeable, survives a session refresh) and because Next 16 will
 * not let an RSC render write a cookie anyway.
 */
export async function switchCustomer(customerId: number) {
  const actor = await requireActor();

  const customer = await getCustomer(customerId);
  if (!customer) {
    throw new Error('That workspace no longer exists.');
  }

  await setActiveCustomerId(actor.userId, customer.id);
  revalidatePath('/', 'layout');
}
