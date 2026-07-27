import Link from 'next/link';
import { asc, desc } from 'drizzle-orm';
import { db } from '@/db';
import { machineModels, machines, promptGroups, prompts } from '@/db/schema';
import { NewRunForm } from '@/components/runs/new-run-form';

export const dynamic = 'force-dynamic';

export default async function NewRunPage() {
  const machineRows = await db.select().from(machines).orderBy(asc(machines.name));
  const modelRows = await db
    .select()
    .from(machineModels)
    .orderBy(desc(machineModels.currentlyLoaded), desc(machineModels.lastSeenAt));
  const groupRows = await db
    .select()
    .from(promptGroups)
    .orderBy(asc(promptGroups.sortOrder), asc(promptGroups.name));
  const promptRows = await db.select({ groupId: prompts.groupId }).from(prompts);

  const counts: Record<number, number> = {};
  for (const prompt of promptRows) {
    counts[prompt.groupId] = (counts[prompt.groupId] ?? 0) + 1;
  }

  return (
    <div className="flex flex-1 flex-col gap-8 p-8">
      <div className="flex flex-col gap-2">
        <h1 className="text-2xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
          New run
        </h1>
        <p className="max-w-prose text-sm text-zinc-600 dark:text-zinc-400">
          Every prompt in the selected groups is executed sequentially against one machine and
          model. Prompts, system prompts and machine specs are snapshotted, so later edits never
          change this run&apos;s history.
        </p>
        <Link
          href="/runs"
          className="text-sm text-zinc-500 underline-offset-2 hover:underline dark:text-zinc-400"
        >
          ← Back to runs
        </Link>
      </div>

      <NewRunForm
        machines={machineRows.map((machine) => ({
          id: machine.id,
          name: machine.name,
          baseUrl: machine.baseUrl,
        }))}
        models={modelRows.map((model) => ({
          machineId: model.machineId,
          modelId: model.modelId,
          currentlyLoaded: model.currentlyLoaded,
        }))}
        groups={groupRows.map((group) => ({
          id: group.id,
          name: group.name,
          promptCount: counts[group.id] ?? 0,
        }))}
      />
    </div>
  );
}
