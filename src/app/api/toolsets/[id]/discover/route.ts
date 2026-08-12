import { revalidatePath } from 'next/cache';
import { currentScope } from '@/db/scope';
import { getToolset, syncDiscoveredTools } from '@/db/repo/toolsets';
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

  const scope = await currentScope();
  const toolset = await getToolset(scope, id);
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

  const synced = await syncDiscoveredTools(scope, id, discovered);

  revalidatePath('/toolsets');
  revalidatePath('/prompts');

  return Response.json({
    ok: true,
    discovered: synced.discovered,
    retired: synced.retired,
    tools: discovered.map((tool) => tool.name),
  });
}
