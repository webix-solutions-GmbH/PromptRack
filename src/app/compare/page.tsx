import Link from 'next/link';
import { desc, inArray } from 'drizzle-orm';
import { db } from '@/db';
import { runResults, runs } from '@/db/schema';
import { formatDateTime, formatRate, snapshotMachineName } from '@/lib/format';
import type { RunResultStatus } from '@/lib/run-events';
import {
  MAX_COMPARE_RUNS,
  MIN_COMPARE_RUNS,
  buildCompareMatrix,
  parseRunIds,
  type CompareCellView,
  type CompareRunView,
} from '@/lib/compare';
import { CompareRow } from '@/components/compare/compare-row';
import { RunPicker } from '@/components/compare/run-picker';

export const dynamic = 'force-dynamic';

function parseGroupNames(raw: string): string[] {
  try {
    const parsed: unknown = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.filter((v): v is string => typeof v === 'string') : [];
  } catch {
    return [];
  }
}

export default async function ComparePage({
  searchParams,
}: {
  searchParams: Promise<{ [key: string]: string | string[] | undefined }>;
}) {
  const sp = await searchParams;

  const [runRows, summaryRows] = await Promise.all([
    db.select().from(runs).orderBy(desc(runs.createdAt), desc(runs.id)),
    db
      .select({
        runId: runResults.runId,
        status: runResults.status,
        rating: runResults.rating,
        tokensPerSec: runResults.tokensPerSec,
        groupName: runResults.groupName,
      })
      .from(runResults),
  ]);

  // A run is comparable once it has produced at least one result — that covers
  // completed runs as well as ones that were stopped or partially failed.
  const comparableRuns: CompareRunView[] = runRows
    .map((run) => {
      const results = summaryRows.filter((result) => result.runId === run.id);
      const rates = results
        .map((result) => result.tokensPerSec)
        .filter((rate): rate is number => typeof rate === 'number');

      return {
        id: run.id,
        modelId: run.modelId,
        machineName: snapshotMachineName(run.machineSnapshot),
        status: run.status,
        createdAt: run.createdAt,
        groupNames: Array.from(
          new Set([...parseGroupNames(run.groupNames), ...results.map((r) => r.groupName)]),
        ),
        good: results.filter((result) => result.rating === 'good').length,
        bad: results.filter((result) => result.rating === 'bad').length,
        ok: results.filter((result) => result.status === 'ok').length,
        error: results.filter((result) => result.status === 'error').length,
        avgRate:
          rates.length > 0 ? rates.reduce((total, rate) => total + rate, 0) / rates.length : null,
      };
    })
    .filter((run) => run.ok > 0 || run.error > 0 || run.status === 'completed');

  const comparableById = new Map(comparableRuns.map((run) => [run.id, run]));
  const selectedIds = parseRunIds(sp.runs).filter((id) => comparableById.has(id));
  const columns = selectedIds.map((id) => comparableById.get(id)!);

  const resultRows =
    selectedIds.length >= MIN_COMPARE_RUNS
      ? await db.select().from(runResults).where(inArray(runResults.runId, selectedIds))
      : [];

  const cells: CompareCellView[] = resultRows.map((row) => ({
    id: row.id,
    runId: row.runId,
    promptId: row.promptId,
    sortOrder: row.sortOrder,
    groupName: row.groupName,
    promptTitle: row.promptTitle,
    promptText: row.promptText,
    status: row.status as RunResultStatus,
    responseText: row.responseText,
    error: row.error,
    durationMs: row.durationMs,
    ttftMs: row.ttftMs,
    completionTokens: row.completionTokens,
    tokensPerSec: row.tokensPerSec,
    tokensEstimated: row.tokensEstimated,
    rating: row.rating as 'good' | 'bad' | null,
    ratingNote: row.ratingNote,
  }));

  const matrix = buildCompareMatrix(selectedIds, cells);

  return (
    <div className="flex flex-1 flex-col gap-8 p-8">
      <div className="flex flex-col gap-2">
        <h1 className="text-2xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
          Compare
        </h1>
        <p className="max-w-prose text-sm text-zinc-600 dark:text-zinc-400">
          Put {MIN_COMPARE_RUNS}–{MAX_COMPARE_RUNS} runs side by side: rows are prompts, columns
          are runs. The selection lives in the URL, so a comparison can be bookmarked or shared.
        </p>
      </div>

      <RunPicker runs={comparableRuns} />

      {selectedIds.length < MIN_COMPARE_RUNS ? (
        <div className="rounded-lg border border-dashed border-zinc-300 p-8 text-center text-sm text-zinc-500 dark:border-zinc-700 dark:text-zinc-400">
          Select at least {MIN_COMPARE_RUNS} runs above to build the comparison matrix.
        </div>
      ) : matrix.length === 0 ? (
        <div className="rounded-lg border border-dashed border-zinc-300 p-8 text-center text-sm text-zinc-500 dark:border-zinc-700 dark:text-zinc-400">
          The selected runs have no results to compare.
        </div>
      ) : (
        <section className="flex flex-col gap-3">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <h2 className="text-sm font-medium text-zinc-700 dark:text-zinc-300">
              {matrix.length} prompt{matrix.length === 1 ? '' : 's'} × {columns.length} runs
            </h2>
          </div>

          <div className="overflow-x-auto rounded-lg border border-zinc-200 dark:border-zinc-800">
            <table className="w-full min-w-max border-collapse text-left text-sm">
              <thead className="border-b border-zinc-200 bg-zinc-50 dark:border-zinc-800 dark:bg-zinc-900">
                <tr>
                  <th className="sticky left-0 z-10 w-64 min-w-64 border-r border-zinc-200 bg-zinc-50 px-4 py-3 text-xs font-medium uppercase tracking-wide text-zinc-500 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-400">
                    Prompt
                  </th>
                  {columns.map((run) => (
                    <th
                      key={run.id}
                      className="w-96 min-w-80 max-w-md border-l border-zinc-200 px-4 py-3 align-top dark:border-zinc-800"
                    >
                      <div className="flex flex-col gap-1">
                        <span className="font-mono text-xs font-semibold text-zinc-900 dark:text-zinc-50">
                          {run.modelId}
                        </span>
                        <span className="text-xs font-normal text-zinc-600 dark:text-zinc-400">
                          @ {run.machineName}
                        </span>
                        <span className="text-xs font-normal text-zinc-500 dark:text-zinc-500">
                          {formatDateTime(run.createdAt)}
                        </span>
                        <span className="flex flex-wrap items-center gap-2 text-xs font-normal">
                          <Link
                            href={`/runs/${run.id}`}
                            className="font-medium text-zinc-700 underline-offset-2 hover:underline dark:text-zinc-300"
                          >
                            run #{run.id}
                          </Link>
                          <span className="text-zinc-500 dark:text-zinc-400">
                            <span className="text-emerald-600 dark:text-emerald-400">
                              {run.good}
                            </span>
                            /<span className="text-red-600 dark:text-red-400">{run.bad}</span>
                          </span>
                          <span className="text-zinc-500 dark:text-zinc-400">
                            {formatRate(run.avgRate)}
                          </span>
                        </span>
                      </div>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-zinc-200 dark:divide-zinc-800">
                {matrix.map((row) => (
                  <CompareRow key={row.key} row={row} />
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </div>
  );
}
