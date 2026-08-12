import { currentScope } from '@/db/scope';
import {
  listGroups,
  listPrompts,
  listToolsetLinks,
  promptCountsByGroup,
} from '@/db/repo/prompts';
import { listSystemPrompts } from '@/db/repo/system-prompts';
import { listTools, listToolsets } from '@/db/repo/toolsets';
import { onPage, requireActor } from '@/lib/auth/guards';
import { canWrite } from '@/lib/auth/policy';
import { GroupSidebar } from '@/components/prompts/group-sidebar';
import { PromptsPanel } from '@/components/prompts/prompts-panel';
import type { ToolsetOption } from '@/components/prompts/prompt-editor';

export const dynamic = 'force-dynamic';

export default async function PromptsPage({
  searchParams,
}: {
  searchParams: Promise<{ group?: string }>;
}) {
  const { group } = await searchParams;

  const actor = await onPage(requireActor);
  const writable = canWrite(actor.role);
  const scope = await currentScope();
  const groups = await listGroups(scope, 'sort-name');
  const allPrompts = await listPrompts(scope);
  const systemPromptRows = await listSystemPrompts(scope, 'name');

  const counts: Record<number, number> = {};
  for (const [groupId, count] of await promptCountsByGroup(scope)) {
    counts[groupId] = count;
  }

  const requestedId = group ? Number(group) : null;
  const selectedGroup =
    requestedId !== null ? groups.find((g) => g.id === requestedId) : groups[0];
  const selectedGroupId = selectedGroup?.id ?? null;
  const promptsForGroup =
    selectedGroupId !== null
      ? allPrompts
          .filter((prompt) => prompt.groupId === selectedGroupId)
          .sort((a, b) => a.sortOrder - b.sortOrder || a.id - b.id)
      : [];

  const systemPromptOptions = systemPromptRows.map((sp) => ({
    id: sp.id,
    name: sp.name,
    content: sp.content,
  }));

  const toolsetRows = await listToolsets(scope);
  const toolRows = await listTools(scope);
  const linkRows = await listToolsetLinks(scope);

  const toolsetOptions: ToolsetOption[] = toolsetRows.map((toolset) => ({
    id: toolset.id,
    name: toolset.name,
    kind: toolset.kind,
    tools: toolRows
      .filter((tool) => tool.toolsetId === toolset.id)
      .map((tool) => ({
        name: tool.name,
        description: tool.description,
        enabled: tool.enabled,
      })),
  }));

  const toolsetIdsByPrompt: Record<number, number[]> = {};
  for (const link of linkRows) {
    const list = toolsetIdsByPrompt[link.promptId] ?? [];
    list.push(link.toolsetId);
    toolsetIdsByPrompt[link.promptId] = list;
  }

  return (
    <div className="flex flex-1 flex-col gap-8 p-8">
      <div className="flex flex-col gap-2">
        <h1 className="text-2xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
          Prompts
        </h1>
        <p className="max-w-prose text-sm text-zinc-600 dark:text-zinc-400">
          Test cases, organized into groups. Each prompt may reference a reusable base system
          prompt.
        </p>
      </div>

      <div className="flex flex-1 flex-col gap-8 lg:flex-row">
        <aside className="w-full shrink-0 lg:w-64">
          <GroupSidebar
            groups={groups}
            selectedGroupId={selectedGroupId}
            counts={counts}
            canWrite={writable}
          />
        </aside>
        <main className="min-w-0 flex-1">
          {selectedGroup ? (
            <PromptsPanel
              groupId={selectedGroup.id}
              prompts={promptsForGroup}
              systemPrompts={systemPromptOptions}
              toolsets={toolsetOptions}
              toolsetIdsByPrompt={toolsetIdsByPrompt}
              canWrite={writable}
            />
          ) : (
            <div className="rounded-lg border border-zinc-200 px-6 py-12 text-center text-sm text-zinc-500 dark:border-zinc-800 dark:text-zinc-400">
              Create a group to get started.
            </div>
          )}
        </main>
      </div>
    </div>
  );
}
