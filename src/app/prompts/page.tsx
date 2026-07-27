import { asc } from 'drizzle-orm';
import { db } from '@/db';
import { promptGroups, prompts, systemPrompts } from '@/db/schema';
import { GroupSidebar } from '@/components/prompts/group-sidebar';
import { PromptsPanel } from '@/components/prompts/prompts-panel';

export const dynamic = 'force-dynamic';

export default async function PromptsPage({
  searchParams,
}: {
  searchParams: Promise<{ group?: string }>;
}) {
  const { group } = await searchParams;

  const groups = await db
    .select()
    .from(promptGroups)
    .orderBy(asc(promptGroups.sortOrder), asc(promptGroups.name));
  const allPrompts = await db.select().from(prompts);
  const systemPromptRows = await db.select().from(systemPrompts).orderBy(asc(systemPrompts.name));

  const counts: Record<number, number> = {};
  for (const prompt of allPrompts) {
    counts[prompt.groupId] = (counts[prompt.groupId] ?? 0) + 1;
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
          <GroupSidebar groups={groups} selectedGroupId={selectedGroupId} counts={counts} />
        </aside>
        <main className="min-w-0 flex-1">
          {selectedGroup ? (
            <PromptsPanel
              groupId={selectedGroup.id}
              prompts={promptsForGroup}
              systemPrompts={systemPromptOptions}
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
