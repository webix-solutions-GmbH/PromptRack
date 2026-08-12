/** Toolsets and the tools they offer. */

import { asc, eq, inArray } from 'drizzle-orm';
import { db } from '@/db';
import { tools, toolsets, type NewTool, type NewToolset, type Tool, type Toolset } from '../schema';
import { combine, scopeValues, whereScoped, type Scope } from '../scope';
import { scopeThroughParent } from './scoped';

export type ToolsetFields = Omit<NewToolset, 'id' | 'createdAt' | 'updatedAt'>;
export type ToolFields = Pick<
  NewTool,
  'name' | 'description' | 'parametersJson' | 'mockResponse'
>;

/** One tool as an MCP server describes it. */
export interface McpToolDescriptor {
  name: string;
  description: string | null;
  parameters: unknown;
}

export function listToolsets(scope: Scope): Promise<Toolset[]> {
  return db
    .select()
    .from(toolsets)
    .where(whereScoped(scope, toolsets))
    .orderBy(asc(toolsets.name));
}

export async function getToolset(scope: Scope, id: number): Promise<Toolset | null> {
  const [row] = await db
    .select()
    .from(toolsets)
    .where(whereScoped(scope, toolsets, eq(toolsets.id, id)));
  return row ?? null;
}

export async function createToolset(
  scope: Scope,
  values: ToolsetFields & { now: Date },
): Promise<{ id: number }> {
  const { now, ...fields } = values;
  const [row] = await db
    .insert(toolsets)
    .values({ ...fields, createdAt: now, updatedAt: now, ...scopeValues(scope) })
    .returning({ id: toolsets.id });
  return row;
}

export async function updateToolset(
  scope: Scope,
  id: number,
  values: ToolsetFields & { now: Date },
): Promise<void> {
  const { now, ...fields } = values;
  await db
    .update(toolsets)
    .set({ ...fields, updatedAt: now })
    .where(whereScoped(scope, toolsets, eq(toolsets.id, id)));
}

export async function deleteToolset(scope: Scope, id: number): Promise<void> {
  await db.delete(toolsets).where(whereScoped(scope, toolsets, eq(toolsets.id, id)));
}

export async function listTools(
  scope: Scope,
  opts: { toolsetIds?: number[] } = {},
): Promise<Tool[]> {
  if (opts.toolsetIds !== undefined && opts.toolsetIds.length === 0) return [];

  const rows = await db
    .select({ tool: tools })
    .from(tools)
    .innerJoin(toolsets, eq(tools.toolsetId, toolsets.id))
    .where(
      whereScoped(
        scope,
        toolsets,
        opts.toolsetIds === undefined ? undefined : inArray(tools.toolsetId, opts.toolsetIds),
      ),
    )
    .orderBy(asc(tools.name));

  return rows.map((row) => row.tool);
}

/**
 * Writes a hand-authored tool.
 *
 * The driver's unique-violation error is deliberately allowed to escape: the
 * caller turns it into "this toolset already has a tool called …", which needs
 * the original error to recognise it.
 */
export async function createTool(
  scope: Scope,
  toolsetId: number,
  values: ToolFields & { now: Date },
): Promise<void> {
  // `tools` inherits its scope from the toolset it is inserted under, which the
  // caller has already resolved through this repository.
  // Phase 5: assert the toolset is in scope before inserting under it.
  void scope;
  const { now, ...fields } = values;
  await db.insert(tools).values({
    ...fields,
    toolsetId,
    source: 'manual',
    enabled: true,
    firstSeenAt: now,
    lastSeenAt: now,
  });
}

export async function updateTool(
  scope: Scope,
  id: number,
  values: ToolFields & { now: Date },
): Promise<void> {
  const { now, ...fields } = values;
  await db
    .update(tools)
    .set({ ...fields, lastSeenAt: now })
    .where(toolWhere(scope, id));
}

export async function deleteTool(scope: Scope, id: number): Promise<void> {
  await db.delete(tools).where(toolWhere(scope, id));
}

export async function setToolEnabled(
  scope: Scope,
  id: number,
  enabled: boolean,
): Promise<void> {
  await db.update(tools).set({ enabled }).where(toolWhere(scope, id));
}

/**
 * `tools` is a child of `toolsets`, so a write that only knows a tool id has to
 * inherit its scope through the toolset it belongs to.
 */
function toolWhere(scope: Scope, id: number) {
  return combine([
    eq(tools.id, id),
    scopeThroughParent(scope, tools.toolsetId, toolsets, toolsets.id),
  ]);
}

/**
 * Applies what an MCP server just reported for one toolset.
 *
 * Mirrors machine model discovery: rows are upserted and never deleted, so a
 * tool that has disappeared from the server is only disabled. A hand-written
 * `mock_response` survives discovery — it is useful for exercising the tool
 * without the server.
 */
export async function syncDiscoveredTools(
  scope: Scope,
  toolsetId: number,
  discovered: McpToolDescriptor[],
): Promise<{ discovered: number; retired: number }> {
  const now = new Date();
  const existingRows = await listTools(scope, { toolsetIds: [toolsetId] });
  const existingByName = new Map(existingRows.map((row) => [row.name, row]));

  for (const tool of discovered) {
    const existing = existingByName.get(tool.name);
    const values = {
      description: tool.description,
      parametersJson: JSON.stringify(tool.parameters),
      enabled: true,
      lastSeenAt: now,
    };

    if (existing) {
      await db.update(tools).set(values).where(eq(tools.id, existing.id));
    } else {
      await db.insert(tools).values({
        ...values,
        toolsetId,
        name: tool.name,
        source: 'mcp',
        firstSeenAt: now,
      });
    }
  }

  const discoveredNames = new Set(discovered.map((tool) => tool.name));
  const retired = existingRows.filter(
    (row) => row.source === 'mcp' && row.enabled && !discoveredNames.has(row.name),
  );
  for (const row of retired) {
    await db.update(tools).set({ enabled: false }).where(eq(tools.id, row.id));
  }

  return { discovered: discovered.length, retired: retired.length };
}

/**
 * The MCP endpoints of the given toolsets.
 *
 * Read live, never snapshotted: a server URL and its auth headers are
 * credentials, so a moved endpoint must not break a run created before the
 * move. The tool *definitions* travel with the run instead.
 */
export async function listMcpServers(
  scope: Scope,
  toolsetIds: number[],
): Promise<{ id: number; mcpUrl: string | null; mcpHeaders: string | null }[]> {
  if (toolsetIds.length === 0) return [];
  return db
    .select({ id: toolsets.id, mcpUrl: toolsets.mcpUrl, mcpHeaders: toolsets.mcpHeaders })
    .from(toolsets)
    .where(whereScoped(scope, toolsets, inArray(toolsets.id, toolsetIds)));
}
