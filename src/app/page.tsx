import Link from 'next/link';
import { desc } from 'drizzle-orm';
import { db } from '@/db';
import { machines, prompts, runResults, runs } from '@/db/schema';
import { formatDateTime, formatRate } from '@/lib/format';
import { StatusBadge } from '@/components/runs/status-badge';

export const dynamic = 'force-dynamic';

function snapshotName(raw: string): string {
  try {
    const parsed: unknown = JSON.parse(raw);
    const name =
      parsed && typeof parsed === 'object' ? (parsed as { name?: unknown }).name : undefined;
    return typeof name === 'string' && name.length > 0 ? name : '(deleted machine)';
  } catch {
    return '(deleted machine)';
  }
}

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
  const [runRows, machineRows, promptRows, resultRows] = await Promise.all([
    db.select().from(runs).orderBy(desc(runs.createdAt), desc(runs.id)),
    db.select().from(machines),
    db.select().from(prompts),
    db
      .select({
        runId: runResults.runId,
        status: runResults.status,
        rating: runResults.rating,
        tokensPerSec: runResults.tokensPerSec,
      })
      .from(runResults),
  ]);

  const totalGood = resultRows.filter((result) => result.rating === 'good').length;
  const totalBad = resultRows.filter((result) => result.rating === 'bad').length;

  const recentRuns = runRows.slice(0, 10).map((run) => {
    const results = resultRows.filter((result) => result.runId === run.id);
    const rates = results
      .map((result) => result.tokensPerSec)
      .filter((rate): rate is number => typeof rate === 'number');

    return {
      run,
      ok: results.filter((result) => result.status === 'ok').length,
      error: results.filter((result) => result.status === 'error').length,
      good: results.filter((result) => result.rating === 'good').length,
      bad: results.filter((result) => result.rating === 'bad').length,
      avgRate:
        rates.length > 0 ? rates.reduce((total, rate) => total + rate, 0) / rates.length : null,
    };
  });

  return (
    <div className="flex flex-1 flex-col gap-8 p-8">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex flex-col gap-2">
          <h1 className="text-2xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
            Dashboard
          </h1>
          <p className="max-w-prose text-sm text-zinc-600 dark:text-zinc-400">
            A summary of machines, recent runs, and rated results.
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

      <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5">
        <StatCard label="Total runs" value={String(runRows.length)} />
        <StatCard label="Machines" value={String(machineRows.length)} />
        <StatCard label="Prompts" value={String(promptRows.length)} />
        <StatCard label="Good ratings" value={String(totalGood)} />
        <StatCard label="Bad ratings" value={String(totalBad)} />
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
                <th className="px-4 py-3">Good/Bad</th>
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
              {recentRuns.map(({ run, ok, error, good, bad, avgRate }) => (
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
                    {snapshotName(run.machineSnapshot)}
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
                  <td className="px-4 py-3">
                    <span className="text-emerald-600 dark:text-emerald-400">{good}</span>
                    <span className="text-zinc-400 dark:text-zinc-500">/</span>
                    <span className="text-red-600 dark:text-red-400">{bad}</span>
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
