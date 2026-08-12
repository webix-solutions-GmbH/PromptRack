/** Reusable base system prompts. */

import { asc, desc, eq, inArray } from 'drizzle-orm';
import { db } from '@/db';
import { systemPrompts, type SystemPrompt } from '../schema';
import { scopeValues, whereScoped, type Scope } from '../scope';

export function listSystemPrompts(
  scope: Scope,
  order: 'name' | 'updated',
): Promise<SystemPrompt[]> {
  return db
    .select()
    .from(systemPrompts)
    .where(whereScoped(scope, systemPrompts))
    .orderBy(order === 'updated' ? desc(systemPrompts.updatedAt) : asc(systemPrompts.name));
}

export async function getSystemPrompt(scope: Scope, id: number): Promise<SystemPrompt | null> {
  const [row] = await db
    .select()
    .from(systemPrompts)
    .where(whereScoped(scope, systemPrompts, eq(systemPrompts.id, id)));
  return row ?? null;
}

/**
 * The named system prompts, for building a lookup map.
 *
 * An empty id list answers without querying: `inArray(col, [])` is both a
 * portability trap and a pointless round trip.
 */
export async function listSystemPromptsByIds(
  scope: Scope,
  ids: number[],
): Promise<SystemPrompt[]> {
  if (ids.length === 0) return [];
  return db
    .select()
    .from(systemPrompts)
    .where(whereScoped(scope, systemPrompts, inArray(systemPrompts.id, ids)));
}

export async function createSystemPrompt(
  scope: Scope,
  values: { name: string; content: string; now: Date },
): Promise<{ id: number }> {
  const [row] = await db
    .insert(systemPrompts)
    .values({
      name: values.name,
      content: values.content,
      createdAt: values.now,
      updatedAt: values.now,
      ...scopeValues(scope),
    })
    .returning({ id: systemPrompts.id });
  return row;
}

export async function updateSystemPrompt(
  scope: Scope,
  id: number,
  values: { name: string; content: string; now: Date },
): Promise<void> {
  await db
    .update(systemPrompts)
    .set({ name: values.name, content: values.content, updatedAt: values.now })
    .where(whereScoped(scope, systemPrompts, eq(systemPrompts.id, id)));
}

export async function deleteSystemPrompt(scope: Scope, id: number): Promise<void> {
  await db
    .delete(systemPrompts)
    .where(whereScoped(scope, systemPrompts, eq(systemPrompts.id, id)));
}
