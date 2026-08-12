import Link from 'next/link';
import { currentScope } from '@/db/scope';
import { listMachines } from '@/db/repo/machines';
import { countPrompts } from '@/db/repo/prompts';
import {
  countArchivedRuns,
  countRuns,
  listRunSummaries,
  ratingTotals,
} from '@/db/repo/runs';
import { formatDateTime, formatRate, snapshotMachineName } from '@/lib/format';
import { RATING_META } from '@/lib/rating';
import { StatusBadge } from '@/components/runs/status-badge';

export const dynamic = 'force-dynamic';

function StatCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col gap-1 rounded-lg border border-zinc-200 p-5 dark:border-zinc-800">
      <span className="text-xs uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
        {label}
      </span>
      <span className="text-2xl font-semibold text-zinc-900 dark:text-zinc-50">{value}</span>
    </div>
  );
}

export default async function Home() {
  // Archiving a run takes it out of the picture here too — the dashboard is a
  // view of work in play, not an all-time total.
  const scope = await currentScope();
  const [activeRunRows, activeRunCount, machineRows, promptCount, totals, archivedCount] =
    await Promise.all([
      listRunSummaries(scope, {
        archived: 'exclude',
        machineId: null,
        modelId: null,
        groupName: null,
        status: null,
      }),
      countRuns(scope, { archived: 'exclude' }),
      listMachines(scope),
      countPrompts(scope),
      ratingTotals(scope, { archived: 'exclude' }),
      countArchivedRuns(scope),
    ]);

  const recentRuns = activeRunRows.slice(0, 10);

  return (
    <div className="flex flex-1 flex-col gap-8 p-8">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex flex-col gap-2">
          <h1 className="text-2xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
            Dashboard
          </h1>
          <p className="max-w-prose text-sm text-zinc-600 dark:text-zinc-400">
            A summary of machines, recent runs, and rated results.
            {archivedCount > 0 && (
              <>
                {' '}
                <Link href="/runs?archived=only" className="underline-offset-2 hover:underline">
                  {archivedCount} archived run{archivedCount === 1 ? '' : 's'}
                </Link>{' '}
                excluded.
              </>
            )}
          </p>
        </div>
        <div className="flex items-center gap-3">
          <Link
            href="/prompts"
            className="rounded-md border border-zinc-300 px-4 py-2 text-sm font-medium text-zinc-700 transition-colors hover:bg-zinc-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800"
          >
            Manage prompts
          </Link>
          <Link
            href="/runs/new"
            className="rounded-md bg-zinc-900 px-4 py-2 text-sm font-medium text-zinc-50 transition-colors hover:bg-zinc-700 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-300"
          >
            New run
          </Link>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-6">
        <StatCard label="Active runs" value={String(activeRunCount)} />
        <StatCard label="Machines" value={String(machineRows.length)} />
        <StatCard label="Prompts" value={String(promptCount)} />
        <StatCard label="Good ratings" value={String(totals.good)} />
        <StatCard label="Meh ratings" value={String(totals.meh)} />
        <StatCard label="Bad ratings" value={String(totals.bad)} />
      </div>

      <section className="flex flex-col gap-4">
        <h2 className="text-lg font-semibold text-zinc-900 dark:text-zinc-50">Recent runs</h2>

        <div className="overflow-x-auto rounded-lg border border-zinc-200 dark:border-zinc-800">
          <table className="w-full min-w-max text-left text-sm">
            <thead className="border-b border-zinc-200 bg-zinc-50 text-xs font-medium uppercase tracking-wide text-zinc-500 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-400">
              <tr>
                <th className="px-4 py-3">Run</th>
                <th className="px-4 py-3">Created</th>
                <th className="px-4 py-3">Machine</th>
                <th className="px-4 py-3">Model</th>
                <th className="px-4 py-3">Status</th>
                <th className="px-4 py-3">Ok/Err</th>
                <th className="px-4 py-3" title="good / meh / bad">
                  Rating
                </th>
                <th className="px-4 py-3">Avg speed</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-200 dark:divide-zinc-800">
              {recentRuns.length === 0 && (
                <tr>
                  <td
                    colSpan={8}
                    className="px-4 py-6 text-center text-zinc-500 dark:text-zinc-400"
                  >
                    No runs yet — start one from “New run”.
                  </td>
                </tr>
              )}
              {recentRuns.map(({ run, ok, error, good, meh, bad, avgRate }) => (
                <tr
                  key={run.id}
                  className="transition-colors hover:bg-zinc-50 dark:hover:bg-zinc-900"
                >
                  <td className="px-4 py-3 font-medium text-zinc-900 dark:text-zinc-50">
                    <Link href={`/runs/${run.id}`} className="hover:underline">
                      #{run.id}
                    </Link>
                  </td>
                  <td className="px-4 py-3 text-zinc-600 dark:text-zinc-400">
                    {formatDateTime(run.createdAt)}
                  </td>
                  <td className="px-4 py-3 text-zinc-600 dark:text-zinc-400">
                    {snapshotMachineName(run.machineSnapshot)}
                  </td>
                  <td className="px-4 py-3 font-mono text-xs text-zinc-600 dark:text-zinc-400">
                    {run.modelId}
                  </td>
                  <td className="px-4 py-3">
                    <StatusBadge status={run.status} />
                  </td>
                  <td className="px-4 py-3 text-zinc-600 dark:text-zinc-400">
                    <span className="text-emerald-600 dark:text-emerald-400">{ok}</span>
                    <span className="text-zinc-400 dark:text-zinc-500">/</span>
                    <span className={error > 0 ? 'text-red-600 dark:text-red-400' : ''}>
                      {error}
                    </span>
                  </td>
                  <td className="whitespace-nowrap px-4 py-3">
                    <span className={RATING_META.good.text}>{good}</span>
                    <span className="text-zinc-400 dark:text-zinc-500">/</span>
                    <span className={RATING_META.meh.text}>{meh}</span>
                    <span className="text-zinc-400 dark:text-zinc-500">/</span>
                    <span className={RATING_META.bad.text}>{bad}</span>
                  </td>
                  <td className="px-4 py-3 text-zinc-600 dark:text-zinc-400">
                    {formatRate(avgRate)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
