import { revalidatePath } from 'next/cache';
import { eq } from 'drizzle-orm';
import { db } from '@/db';
import { tools, toolsets } from '@/db/schema';
import { listMcpTools, parseMcpHeaders } from '@/lib/mcp-client';

export const dynamic = 'force-dynamic';

/**
 * Imports an MCP server's tools into its toolset.
 *
 * Mirrors machine model discovery: rows are upserted and never deleted, so a
 * tool that has disappeared from the server is only disabled. Past runs keep
 * their frozen definitions either way, but the row still explains where they
 * came from.
 */
export async function POST(
  _request: Request,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id: idParam } = await params;
  const id = Number(idParam);
  if (!Number.isInteger(id)) {
    return Response.json({ ok: false, error: 'Invalid toolset id.' }, { status: 400 });
  }

  const [toolset] = await db.select().from(toolsets).where(eq(toolsets.id, id));
  if (!toolset) {
    return Response.json({ ok: false, error: 'Toolset not found.' }, { status: 404 });
  }
  if (toolset.kind !== 'mcp' || !toolset.mcpUrl) {
    return Response.json({
      ok: false,
      error: 'This toolset is not backed by an MCP server.',
    });
  }

  let discovered;
  try {
    discovered = await listMcpTools({
      url: toolset.mcpUrl,
      headers: parseMcpHeaders(toolset.mcpHeaders),
    });
  } catch (err) {
    return Response.json({
      ok: false,
      error: err instanceof Error ? err.message : 'Discovery failed.',
    });
  }

  const now = Date.now();
  const existingRows = await db.select().from(tools).where(eq(tools.toolsetId, id));
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
      // Re-enable and refresh the schema, but leave a hand-written canned
      // response alone — it is useful for testing this tool without the server.
      await db.update(tools).set(values).where(eq(tools.id, existing.id));
    } else {
      await db.insert(tools).values({
        ...values,
        toolsetId: id,
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

  revalidatePath('/toolsets');
  revalidatePath('/prompts');

  return Response.json({
    ok: true,
    discovered: discovered.length,
    retired: retired.length,
    tools: discovered.map((tool) => tool.name),
  });
}
