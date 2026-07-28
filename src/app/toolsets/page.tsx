import { asc } from 'drizzle-orm';
import { db } from '@/db';
import { tools, toolsets } from '@/db/schema';
import { createToolset } from '@/actions/toolsets';
import { CreateToggle } from '@/components/create-toggle';
import { ToolsetCard } from '@/components/toolsets/toolset-card';
import { CreateToolsetForm } from '@/components/toolsets/create-toolset-form';

export const dynamic = 'force-dynamic';

export default async function ToolsetsPage() {
  const toolsetRows = await db.select().from(toolsets).orderBy(asc(toolsets.name));
  const toolRows = await db.select().from(tools).orderBy(asc(tools.name));

  const toolsByToolset = new Map<number, typeof toolRows>();
  for (const tool of toolRows) {
    const list = toolsByToolset.get(tool.toolsetId) ?? [];
    list.push(tool);
    toolsByToolset.set(tool.toolsetId, list);
  }

  return (
    <div className="flex flex-1 flex-col gap-8 p-8">
      <CreateToggle
        label="New toolset"
        title="New toolset"
        className="max-w-4xl"
        header={
          <div className="flex flex-col gap-2">
            <h1 className="text-2xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
              Toolsets
            </h1>
            <p className="max-w-prose text-sm text-zinc-600 dark:text-zinc-400">
              Bundles of callable functions a prompt can be run with. A manual toolset answers with
              canned responses, which keeps a multi-turn test deterministic; an MCP toolset
              discovers its tools from a server over HTTP and really executes them.
            </p>
          </div>
        }
      >
        <CreateToolsetForm action={createToolset} />
      </CreateToggle>

      <ul className="flex max-w-4xl flex-col gap-4">
        {toolsetRows.length === 0 && (
          <li className="rounded-lg border border-zinc-200 px-4 py-6 text-center text-sm text-zinc-500 dark:border-zinc-800 dark:text-zinc-400">
            No toolsets yet — add one with “New toolset”.
          </li>
        )}
        {toolsetRows.map((toolset) => (
          <ToolsetCard
            key={toolset.id}
            toolset={toolset}
            tools={toolsByToolset.get(toolset.id) ?? []}
          />
        ))}
      </ul>
    </div>
  );
}
