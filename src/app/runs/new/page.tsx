import Link from 'next/link';
import { currentScope } from '@/db/scope';
import { listMachineModels, listMachines } from '@/db/repo/machines';
import { listGroups, promptCountsByGroup } from '@/db/repo/prompts';
import { onPage, requireWriter } from '@/lib/auth/guards';
import { NewRunForm } from '@/components/runs/new-run-form';

export const dynamic = 'force-dynamic';

export default async function NewRunPage() {
  // The whole page is a mutation form, so it is refused outright rather than
  // rendered with its submit button hidden.
  await onPage(requireWriter);
  const scope = await currentScope();
  const machineRows = await listMachines(scope, 'name');
  const modelRows = await listMachineModels(scope, { order: 'loaded-first' });
  const groupRows = await listGroups(scope, 'sort-name');
  const counts = await promptCountsByGroup(scope);

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
          promptCount: counts.get(group.id) ?? 0,
        }))}
      />
    </div>
  );
}
