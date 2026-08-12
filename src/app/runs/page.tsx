import Link from 'next/link';
import { desc } from 'drizzle-orm';
import { db } from '@/db';
import { machines, runResults, runs } from '@/db/schema';
import { formatDuration, formatIsoDateTime, formatRate, snapshotMachineName } from '@/lib/format';
import { countRatings, ratingScore, RATING_META } from '@/lib/rating';
import { LinkedRow } from '@/components/linked-row';
import { ArchiveRunButton } from '@/components/runs/archive-run-button';
import { DeleteRunButton } from '@/components/runs/delete-run-button';
import { SortableHeader } from '@/components/runs/sortable-header';
import { StatusBadge } from '@/components/runs/status-badge';
import { RunsFilterBar } from '@/components/runs/runs-filter-bar';

export const dynamic = 'force-dynamic';

const RUN_STATUSES = ['pending', 'running', 'completed', 'failed'] as const;

function firstParam(value: string | string[] | undefined): string | null {
  const raw = Array.isArray(value) ? value[0] : value;
  return typeof raw === 'string' && raw.length > 0 ? raw : null;
}

function excerpt(value: string | null, max = 60): string {
  if (!value) return '—';
  const flat = value.replace(/\s+/g, ' ').trim();
  return flat.length > max ? `${flat.slice(0, max)}…` : flat;
}

export default async function RunsPage({
  searchParams,
}: {
  searchParams: Promise<{ [key: string]: string | string[] | undefined }>;
}) {
  const sp = await searchParams;
  const machineIdFilter = firstParam(sp.machineId);
  const modelFilter = firstParam(sp.model);
  const groupFilter = firstParam(sp.group);
  const statusFilter = firstParam(sp.status);
  // Archived runs are hidden unless explicitly asked for.
  const archivedFilter = firstParam(sp.archived);

  const runRows = await db.select().from(runs).orderBy(desc(runs.createdAt), desc(runs.id));
  const resultRows = await db
    .select({
      runId: runResults.runId,
      status: runResults.status,
      tokensPerSec: runResults.tokensPerSec,
      durationMs: runResults.durationMs,
      groupName: runResults.groupName,
      rating: runResults.rating,
    })
    .from(runResults);
  const machineRows = await db.select().from(machines);
  const machineNameById = new Map(machineRows.map((machine) => [machine.id, machine.name]));

  const allRows = runRows.map((run) => {
    const results = resultRows.filter((result) => result.runId === run.id);
    const rates = results
      .map((result) => result.tokensPerSec)
      .filter((rate): rate is number => typeof rate === 'number');
    const groupNames = Array.from(new Set(results.map((result) => result.groupName)));

    return {
      run,
      groupNames,
      ok: results.filter((result) => result.status === 'ok').length,
      error: results.filter((result) => result.status === 'error').length,
      pending: results.filter(
        (result) => result.status === 'pending' || result.status === 'running',
      ).length,
      ...countRatings(results.map((result) => result.rating)),
      avgRate:
        rates.length > 0 ? rates.reduce((total, rate) => total + rate, 0) / rates.length : null,
      totalDurationMs: results.reduce((total, result) => total + (result.durationMs ?? 0), 0),
    };
  });

  const filteredRows = allRows.filter(({ run, groupNames }) => {
    const isArchived = run.archivedAt !== null;
    if (archivedFilter === 'only' ? !isArchived : archivedFilter !== 'all' && isArchived) {
      return false;
    }
    if (machineIdFilter !== null && String(run.machineId ?? '') !== machineIdFilter) {
      return false;
    }
    if (modelFilter !== null && run.modelId !== modelFilter) return false;
    if (groupFilter !== null && !groupNames.includes(groupFilter)) return false;
    if (statusFilter !== null && run.status !== statusFilter) return false;
    return true;
  });

  const archivedCount = allRows.filter(({ run }) => run.archivedAt !== null).length;

  const sortKey = firstParam(sp.sort) ?? 'created';
  const sortDir = firstParam(sp.dir) === 'asc' ? 1 : -1;

  function sortValue(row: (typeof allRows)[number]): string | number {
    switch (sortKey) {
      case 'run':
        return row.run.id;
      case 'machine':
        return snapshotMachineName(row.run.machineSnapshot).toLowerCase();
      case 'model':
        return row.run.modelId.toLowerCase();
      case 'status':
        return row.run.status;
      case 'rating':
        return ratingScore(row);
      case 'speed':
        return row.avgRate ?? -1;
      case 'time':
        return row.totalDurationMs;
      case 'created':
      default:
        return row.run.createdAt.getTime();
    }
  }

  const rows = [...filteredRows].sort((a, b) => {
    const va = sortValue(a);
    const vb = sortValue(b);
    const order = va < vb ? -1 : va > vb ? 1 : b.run.id - a.run.id;
    return order * sortDir;
  });

  const filterOptions = {
    machines: Array.from(new Set(runRows.map((run) => run.machineId)))
      .filter((id): id is number => id !== null)
      .map((id) => ({ id, name: machineNameById.get(id) ?? `#${id}` }))
      .sort((a, b) => a.name.localeCompare(b.name)),
    models: Array.from(new Set(runRows.map((run) => run.modelId))).sort(),
    groups: Array.from(new Set(resultRows.map((result) => result.groupName))).sort(),
    statuses: [...RUN_STATUSES],
  };

  return (
    <div className="flex flex-1 flex-col gap-8 p-8">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex flex-col gap-2">
          <h1 className="text-2xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
            Runs
          </h1>
          <p className="max-w-prose text-sm text-zinc-600 dark:text-zinc-400">
            Each run executes the prompts of one or more groups against a single machine and
            model.
            {archivedCount > 0 && archivedFilter === null && (
              <>
                {' '}
                <Link
                  href="/runs?archived=only"
                  className="underline-offset-2 hover:underline"
                >
                  {archivedCount} archived run{archivedCount === 1 ? '' : 's'} hidden
                </Link>
                .
              </>
            )}
          </p>
        </div>
        <Link
          href="/runs/new"
          className="rounded-md bg-zinc-900 px-4 py-2 text-sm font-medium text-zinc-50 transition-colors hover:bg-zinc-700 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-300"
        >
          New run
        </Link>
      </div>

      <RunsFilterBar options={filterOptions} />

      <div className="overflow-x-auto rounded-lg border border-zinc-200 dark:border-zinc-800">
        <table className="w-full min-w-max text-left text-sm">
          <thead className="border-b border-zinc-200 bg-zinc-50 text-xs font-medium uppercase tracking-wide text-zinc-500 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-400">
            <tr>
              <th className="px-2 py-3">
                <SortableHeader label="Run" sortKey="run" />
              </th>
              <th className="px-2 py-3">
                <SortableHeader label="Created" sortKey="created" />
              </th>
              <th className="px-2 py-3">
                <SortableHeader label="Machine" sortKey="machine" firstDir="asc" />
              </th>
              <th className="px-2 py-3">
                <SortableHeader label="Model" sortKey="model" firstDir="asc" />
              </th>
              <th className="px-2 py-3">Groups</th>
              <th className="px-2 py-3">
                <SortableHeader label="Status" sortKey="status" firstDir="asc" />
              </th>
              <th className="px-2 py-3">Results</th>
              <th className="px-2 py-3" title="good / meh / bad / unrated">
                <SortableHeader label="Rating" sortKey="rating" />
              </th>
              <th className="px-2 py-3">
                <SortableHeader label="Avg speed" sortKey="speed" />
              </th>
              <th className="px-2 py-3">
                <SortableHeader label="Total time" sortKey="time" />
              </th>
              <th className="px-2 py-3">Comment</th>
              <th className="px-2 py-3">
                <span className="sr-only">Actions</span>
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-zinc-200 dark:divide-zinc-800">
            {rows.length === 0 && (
              <tr>
                <td
                  colSpan={12}
                  className="px-2 py-6 text-center text-zinc-500 dark:text-zinc-400"
                >
                  {allRows.length === 0
                    ? 'No runs yet — start one from “New run”.'
                    : 'No runs match the current filters.'}
                </td>
              </tr>
            )}
            {rows.map(({ run, groupNames, ok, error, pending, good, meh, bad, unrated, avgRate, totalDurationMs }) => (
              <LinkedRow
                key={run.id}
                href={`/runs/${run.id}`}
                className="transition-colors hover:bg-zinc-50 dark:hover:bg-zinc-900"
              >
                <td className="px-2 py-3 font-medium text-zinc-900 dark:text-zinc-50">
                  <Link href={`/runs/${run.id}`} className="hover:underline">
                    #{run.id}
                  </Link>
                </td>
                <td className="whitespace-nowrap px-2 py-3 text-zinc-600 dark:text-zinc-400">
                  {formatIsoDateTime(run.createdAt)}
                </td>
                <td className="px-2 py-3 text-zinc-600 dark:text-zinc-400">
                  {snapshotMachineName(run.machineSnapshot)}
                </td>
                <td className="px-2 py-3 font-mono text-xs text-zinc-600 dark:text-zinc-400">
                  <div className="max-w-56 truncate" title={run.modelId}>
                    {run.modelId}
                  </div>
                </td>
                <td className="px-2 py-3 text-zinc-600 dark:text-zinc-400">
                  <div className="max-w-40 truncate" title={groupNames.join(', ')}>
                    {groupNames.join(', ') || '—'}
                  </div>
                </td>
                <td className="px-2 py-3">
                  <div className="flex flex-wrap items-center gap-1">
                    <StatusBadge status={run.status} />
                    {run.archivedAt !== null && <StatusBadge status="archived" />}
                  </div>
                </td>
                <td className="px-2 py-3 text-zinc-600 dark:text-zinc-400">
                  <span className="text-emerald-600 dark:text-emerald-400">{ok} ok</span>
                  {' · '}
                  <span className={error > 0 ? 'text-red-600 dark:text-red-400' : ''}>
                    {error} err
                  </span>
                  {' · '}
                  <span>{pending} pending</span>
                </td>
                <td className="whitespace-nowrap px-2 py-3">
                  <span className={RATING_META.good.text}>{good}</span>
                  <span className="text-zinc-400 dark:text-zinc-500">/</span>
                  <span className={RATING_META.meh.text}>{meh}</span>
                  <span className="text-zinc-400 dark:text-zinc-500">/</span>
                  <span className={RATING_META.bad.text}>{bad}</span>
                  <span className="text-zinc-400 dark:text-zinc-500">/</span>
                  <span className="text-zinc-500 dark:text-zinc-400">{unrated}</span>
                </td>
                <td className="px-2 py-3 text-zinc-600 dark:text-zinc-400">
                  {formatRate(avgRate)}
                </td>
                <td className="px-2 py-3 text-zinc-600 dark:text-zinc-400">
                  {totalDurationMs > 0 ? formatDuration(totalDurationMs) : '—'}
                </td>
                <td className="px-2 py-3 text-zinc-600 dark:text-zinc-400">
                  {excerpt(run.comment)}
                </td>
                <td className="px-2 py-3 text-right">
                  <div className="flex items-center justify-end gap-1">
                    <ArchiveRunButton
                      runId={run.id}
                      archived={run.archivedAt !== null}
                      compact
                    />
                    <DeleteRunButton runId={run.id} compact />
                  </div>
                </td>
              </LinkedRow>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
