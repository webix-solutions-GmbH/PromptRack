/** Prompt groups, prompts, and which toolsets a prompt pulls in. */

import { asc, eq, inArray, sql } from 'drizzle-orm';
import { db } from '@/db';
import {
  promptGroups,
  promptToolsets,
  prompts,
  tools,
  toolsets,
  type NewPrompt,
  type Prompt,
  type PromptGroup,
  type PromptToolset,
} from '../schema';
import { combine, scopeValues, whereScoped, type Scope } from '../scope';
import { scopeThroughParent } from './scoped';

// ---------------------------------------------------------------------------
// Prompt groups
// ---------------------------------------------------------------------------

export function listGroups(scope: Scope, order: 'sort-name' | 'sort-id'): Promise<PromptGroup[]> {
  return db
    .select()
    .from(promptGroups)
    .where(whereScoped(scope, promptGroups))
    .orderBy(
      asc(promptGroups.sortOrder),
      order === 'sort-id' ? asc(promptGroups.id) : asc(promptGroups.name),
    );
}

export async function getGroup(scope: Scope, id: number): Promise<PromptGroup | null> {
  const [row] = await db
    .select()
    .from(promptGroups)
    .where(whereScoped(scope, promptGroups, eq(promptGroups.id, id)));
  return row ?? null;
}

export async function listGroupsByIds(scope: Scope, ids: number[]): Promise<PromptGroup[]> {
  if (ids.length === 0) return [];
  return db
    .select()
    .from(promptGroups)
    .where(whereScoped(scope, promptGroups, inArray(promptGroups.id, ids)))
    .orderBy(asc(promptGroups.sortOrder), asc(promptGroups.id));
}

export async function createGroup(
  scope: Scope,
  values: { name: string; description: string | null; now: Date },
): Promise<{ id: number }> {
  const [row] = await db
    .insert(promptGroups)
    .values({
      name: values.name,
      description: values.description,
      sortOrder: 0,
      createdAt: values.now,
      ...scopeValues(scope),
    })
    .returning({ id: promptGroups.id });
  return row;
}

export async function updateGroup(
  scope: Scope,
  id: number,
  values: { name: string; description: string | null },
): Promise<void> {
  await db
    .update(promptGroups)
    .set(values)
    .where(whereScoped(scope, promptGroups, eq(promptGroups.id, id)));
}

export async function deleteGroup(scope: Scope, id: number): Promise<void> {
  await db
    .delete(promptGroups)
    .where(whereScoped(scope, promptGroups, eq(promptGroups.id, id)));
}

/** How many prompts each group holds. */
export async function promptCountsByGroup(scope: Scope): Promise<Map<number, number>> {
  const rows = await db
    .select({
      groupId: prompts.groupId,
      count: sql<number>`count(*)`.mapWith(Number),
    })
    .from(prompts)
    .innerJoin(promptGroups, eq(prompts.groupId, promptGroups.id))
    .where(whereScoped(scope, promptGroups))
    .groupBy(prompts.groupId);

  return new Map(rows.map((row) => [row.groupId, row.count]));
}

export async function countPrompts(scope: Scope): Promise<number> {
  const [row] = await db
    .select({ count: sql<number>`count(*)`.mapWith(Number) })
    .from(prompts)
    .innerJoin(promptGroups, eq(prompts.groupId, promptGroups.id))
    .where(whereScoped(scope, promptGroups));
  return row?.count ?? 0;
}

// ---------------------------------------------------------------------------
// Prompts
// ---------------------------------------------------------------------------

/**
 * Prompts, in run order.
 *
 * Narrowed to one group or a set of groups it is `sortOrder, id` — the order a
 * run materializes its results in. Unnarrowed it additionally groups by
 * `groupId` first, which is what the MCP `list_prompts` tool returns.
 */
export async function listPrompts(
  scope: Scope,
  opts: { groupId?: number; groupIds?: number[] } = {},
): Promise<Prompt[]> {
  if (opts.groupIds !== undefined && opts.groupIds.length === 0) return [];

  const narrowed =
    opts.groupId !== undefined
      ? eq(prompts.groupId, opts.groupId)
      : opts.groupIds !== undefined
        ? inArray(prompts.groupId, opts.groupIds)
        : undefined;

  const scoped = db
    .select({ prompt: prompts })
    .from(prompts)
    .innerJoin(promptGroups, eq(prompts.groupId, promptGroups.id))
    .where(whereScoped(scope, promptGroups, narrowed));

  const rows =
    narrowed === undefined
      ? await scoped.orderBy(asc(prompts.groupId), asc(prompts.sortOrder), asc(prompts.id))
      : await scoped.orderBy(asc(prompts.sortOrder), asc(prompts.id));

  return rows.map((row) => row.prompt);
}

export async function getPrompt(scope: Scope, id: number): Promise<Prompt | null> {
  const [row] = await db
    .select({ prompt: prompts })
    .from(prompts)
    .innerJoin(promptGroups, eq(prompts.groupId, promptGroups.id))
    .where(whereScoped(scope, promptGroups, eq(prompts.id, id)));
  return row?.prompt ?? null;
}

export async function createPrompt(
  scope: Scope,
  values: NewPrompt,
): Promise<{ id: number }> {
  // `prompts` inherits its scope from its group, which the caller resolved
  // through this repository.
  // Phase 5: assert the group is in scope before inserting under it.
  void scope;
  const [row] = await db.insert(prompts).values(values).returning({ id: prompts.id });
  return row;
}

export async function updatePrompt(
  scope: Scope,
  id: number,
  values: Partial<NewPrompt>,
): Promise<void> {
  await db.update(prompts).set(values).where(promptWhere(scope, id));
}

export async function deletePrompt(scope: Scope, id: number): Promise<void> {
  await db.delete(prompts).where(promptWhere(scope, id));
}

/**
 * `prompts` is a child of `prompt_groups`, so a write that only knows a prompt
 * id inherits its scope through the group it belongs to.
 */
function promptWhere(scope: Scope, id: number) {
  return combine([
    eq(prompts.id, id),
    scopeThroughParent(scope, prompts.groupId, promptGroups, promptGroups.id),
  ]);
}

/** A live prompt joined to its group — the rows of `/results` in model mode. */
export interface ComparePromptRow {
  id: number;
  groupId: number;
  groupName: string;
  title: string;
  text: string;
}

export function comparePromptRows(scope: Scope): Promise<ComparePromptRow[]> {
  return db
    .select({
      id: prompts.id,
      groupId: prompts.groupId,
      groupName: promptGroups.name,
      title: prompts.title,
      text: prompts.content,
    })
    .from(prompts)
    .innerJoin(promptGroups, eq(prompts.groupId, promptGroups.id))
    .where(whereScoped(scope, promptGroups))
    .orderBy(
      asc(promptGroups.sortOrder),
      asc(promptGroups.name),
      asc(prompts.sortOrder),
      asc(prompts.id),
    );
}

// ---------------------------------------------------------------------------
// prompt_toolsets
// ---------------------------------------------------------------------------

/**
 * Replaces a prompt's toolset links.
 *
 * Rewriting the set is simpler than diffing it and the table holds a handful of
 * rows per prompt. The link order is the caller's array order.
 */
export async function replaceToolsetLinks(
  scope: Scope,
  promptId: number,
  toolsetIds: number[],
): Promise<void> {
  // Both statements carry the parent key, which is what scopes a link row.
  // Phase 5: assert the prompt and the toolsets are in scope.
  void scope;
  await db.delete(promptToolsets).where(eq(promptToolsets.promptId, promptId));

  if (toolsetIds.length === 0) return;

  await db.insert(promptToolsets).values(
    toolsetIds.map((toolsetId, index) => ({
      promptId,
      toolsetId,
      sortOrder: index,
    })),
  );
}

export async function listToolsetLinks(
  scope: Scope,
  promptIds?: number[],
): Promise<PromptToolset[]> {
  if (promptIds !== undefined && promptIds.length === 0) return [];

  const rows = await db
    .select({ link: promptToolsets })
    .from(promptToolsets)
    .innerJoin(prompts, eq(promptToolsets.promptId, prompts.id))
    .innerJoin(promptGroups, eq(prompts.groupId, promptGroups.id))
    .where(
      whereScoped(
        scope,
        promptGroups,
        promptIds === undefined ? undefined : inArray(promptToolsets.promptId, promptIds),
      ),
    )
    .orderBy(asc(promptToolsets.sortOrder));

  return rows.map((row) => row.link);
}

/** A prompt's toolsets, named — what the MCP prompt views report. */
export interface PromptToolsetView {
  promptId: number;
  toolsetId: number;
  name: string;
  kind: 'manual' | 'mcp';
  sortOrder: number;
}

export async function listPromptToolsetViews(
  scope: Scope,
  promptIds: number[],
): Promise<PromptToolsetView[]> {
  if (promptIds.length === 0) return [];
  return db
    .select({
      promptId: promptToolsets.promptId,
      toolsetId: toolsets.id,
      name: toolsets.name,
      kind: toolsets.kind,
      sortOrder: promptToolsets.sortOrder,
    })
    .from(promptToolsets)
    .innerJoin(toolsets, eq(promptToolsets.toolsetId, toolsets.id))
    .where(whereScoped(scope, toolsets, inArray(promptToolsets.promptId, promptIds)))
    .orderBy(asc(promptToolsets.promptId), asc(promptToolsets.sortOrder));
}

/** One tool as it will be frozen into a run. */
export interface SnapshotToolRow {
  promptId: number;
  toolsetId: number;
  toolsetName: string;
  toolName: string;
  description: string | null;
  parametersJson: string;
  mockResponse: string | null;
  enabled: boolean;
  source: 'manual' | 'mcp';
}

/**
 * The prompt → toolset → tool traversal `createRunRecord` snapshots from.
 *
 * Ordered so a run's frozen tool list is reproducible: by prompt, then by the
 * order the prompt lists its toolsets in, then by tool name.
 */
export async function listSnapshotToolRows(
  scope: Scope,
  promptIds: number[],
): Promise<SnapshotToolRow[]> {
  if (promptIds.length === 0) return [];
  return db
    .select({
      promptId: promptToolsets.promptId,
      toolsetId: toolsets.id,
      toolsetName: toolsets.name,
      toolName: tools.name,
      description: tools.description,
      parametersJson: tools.parametersJson,
      mockResponse: tools.mockResponse,
      enabled: tools.enabled,
      source: tools.source,
    })
    .from(promptToolsets)
    .innerJoin(toolsets, eq(promptToolsets.toolsetId, toolsets.id))
    .innerJoin(tools, eq(tools.toolsetId, toolsets.id))
    .where(whereScoped(scope, toolsets, inArray(promptToolsets.promptId, promptIds)))
    .orderBy(
      asc(promptToolsets.promptId),
      asc(promptToolsets.sortOrder),
      asc(toolsets.id),
      asc(tools.name),
    );
}
