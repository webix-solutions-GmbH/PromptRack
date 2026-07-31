import Link from 'next/link';
import { and, asc, desc, eq, inArray, isNull } from 'drizzle-orm';
import { db } from '@/db';
import { promptGroups, prompts, runResults, runs } from '@/db/schema';
import { formatDateTime, formatRate, snapshotMachineName } from '@/lib/format';
import { countRatings, parseRating, RATING_META } from '@/lib/rating';
import type { RunResultStatus } from '@/lib/run-events';
import { parseTranscript } from '@/lib/tool-loop';
import type { ToolChoice, ToolMode } from '@/lib/tools';
import {
  MIN_COMPARE_MODELS,
  MIN_COMPARE_RUNS,
  buildCompareMatrix,
  buildModelColumns,
  buildModelMatrix,
  modelColumnKey,
  parseCompareMode,
  parseIdList,
  parseModelColumnKeys,
  parseRunIds,
  splitModelColumnKey,
  type CompareCellView,
  type CompareMode,
  type CompareRunView,
  type ComparePromptView,
  type ModelCompareCell,
} from '@/lib/compare';
import { CompareRow } from '@/components/compare/compare-row';
import { GroupFilter, type GroupFilterItem } from '@/components/compare/group-filter';
import { ModelPicker } from '@/components/compare/model-picker';
import { RunPicker } from '@/components/compare/run-picker';

export const dynamic = 'force-dynamic';

/** Enough to select every group; the cap only bounds a hostile URL. */
const MAX_GROUP_FILTER = 200;

type SearchParams = { [key: string]: string | string[] | undefined };

function parseGroupNames(raw: string): string[] {
  try {
    const parsed: unknown = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.filter((v): v is string => typeof v === 'string') : [];
  } catch {
    return [];
  }
}

/** Tool names a stored transcript shows being called, in order. */
function transcriptToolCallNames(raw: string | null): string[] {
  return (parseTranscript(raw) ?? []).flatMap((message) =>
    (message.toolCalls ?? []).map((call) => call.function.name),
  );
}

/**
 * A `/results` link that keeps the current query except for one param.
 *
 * Every link on this page is built this way so switching pivot or group never
 * drops the rest of the selection.
 */
function resultsHref(sp: SearchParams, key: string, values: string[]): string {
  const params = new URLSearchParams();
  for (const [name, value] of Object.entries(sp)) {
    if (name === key || value === undefined) continue;
    for (const item of Array.isArray(value) ? value : [value]) params.append(name, item);
  }
  for (const value of values) params.append(key, value);
  return `/results?${params.toString()}`;
}

/**
 * One `run_results` row as a matrix cell.
 *
 * Rendering always uses the row's own snapshots (prompt text, system prompt,
 * tools); the run is only consulted for what is not frozen per result — when it
 * was created, and the request params it was sent with.
 */
function toCell(
  row: typeof runResults.$inferSelect,
  run: { createdAt: number; params: string | null } | undefined,
): CompareCellView {
  return {
    id: row.id,
    runId: row.runId,
    runCreatedAt: run?.createdAt ?? 0,
    promptId: row.promptId,
    sortOrder: row.sortOrder,
    groupName: row.groupName,
    promptTitle: row.promptTitle,
    promptText: row.promptText,
    systemPromptText: row.systemPromptText,
    toolsSnapshot: row.toolsSnapshot,
    toolMode: row.toolMode as ToolMode,
    toolChoice: row.toolChoice as ToolChoice | null,
    maxTurns: row.maxTurns,
    runParams: run?.params ?? null,
    status: row.status as RunResultStatus,
    responseText: row.responseText,
    error: row.error,
    durationMs: row.durationMs,
    ttftMs: row.ttftMs,
    completionTokens: row.completionTokens,
    tokensPerSec: row.tokensPerSec,
    tokensEstimated: row.tokensEstimated,
    rating: parseRating(row.rating),
    ratingNote: row.ratingNote,
    turnCount: row.turnCount,
    toolCallCount: row.toolCallCount,
    toolCallNames: transcriptToolCallNames(row.transcriptJson),
  };
}

export default async function ResultsPage({
  searchParams,
}: {
  searchParams: Promise<SearchParams>;
}) {
  const sp = await searchParams;
  // Model mode is the default; an existing `?runs=` link keeps its pivot.
  const mode: CompareMode = parseCompareMode(sp.mode) ?? (sp.runs ? 'runs' : 'models');

  const [runRows, summaryRows] = await Promise.all([
    db.select().from(runs).orderBy(desc(runs.createdAt), desc(runs.id)),
    db
      .select({
        runId: runResults.runId,
        promptId: runResults.promptId,
        status: runResults.status,
        rating: runResults.rating,
        tokensPerSec: runResults.tokensPerSec,
        groupName: runResults.groupName,
      })
      .from(runResults),
  ]);

  const runById = new Map(runRows.map((run) => [run.id, run]));

  // ---------------------------------------------------------------- run mode
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
        archived: run.archivedAt !== null,
        createdAt: run.createdAt,
        groupNames: Array.from(
          new Set([...parseGroupNames(run.groupNames), ...results.map((r) => r.groupName)]),
        ),
        ...countRatings(results.map((result) => result.rating)),
        ok: results.filter((result) => result.status === 'ok').length,
        error: results.filter((result) => result.status === 'error').length,
        avgRate:
          rates.length > 0 ? rates.reduce((total, rate) => total + rate, 0) / rates.length : null,
      };
    })
    .filter((run) => run.ok > 0 || run.error > 0 || run.status === 'completed');

  const comparableById = new Map(comparableRuns.map((run) => [run.id, run]));
  const selectedRunIds =
    mode === 'runs' ? parseRunIds(sp.runs).filter((id) => comparableById.has(id)) : [];
  const runColumns = selectedRunIds.map((id) => comparableById.get(id)!);

  // Archived runs are hidden from the picker, but an already-selected one stays
  // listed so a bookmarked comparison still works and can be deselected.
  const pickerRuns = comparableRuns.filter(
    (run) => !run.archived || selectedRunIds.includes(run.id),
  );
  const hiddenArchivedCount = comparableRuns.length - pickerRuns.length;

  const runCells =
    selectedRunIds.length >= MIN_COMPARE_RUNS
      ? (await db.select().from(runResults).where(inArray(runResults.runId, selectedRunIds))).map(
          (row) => toCell(row, runById.get(row.runId)),
        )
      : [];

  // -------------------------------------------------------------- model mode
  const modelColumns = buildModelColumns(
    runRows.map((run) => ({
      id: run.id,
      machineId: run.machineId,
      machineName: snapshotMachineName(run.machineSnapshot),
      modelId: run.modelId,
      createdAt: run.createdAt,
      archived: run.archivedAt !== null,
    })),
    summaryRows,
  );
  const modelColumnByKey = new Map(modelColumns.map((column) => [column.key, column]));
  const selectedModelKeys =
    mode === 'models'
      ? parseModelColumnKeys(sp.model).filter((key) => modelColumnByKey.has(key))
      : [];
  const modelHeaders = selectedModelKeys.map((key) => modelColumnByKey.get(key)!);

  const promptRows: ComparePromptView[] =
    mode === 'models'
      ? await db
          .select({
            id: prompts.id,
            groupId: prompts.groupId,
            groupName: promptGroups.name,
            title: prompts.title,
            text: prompts.content,
          })
          .from(prompts)
          .innerJoin(promptGroups, eq(prompts.groupId, promptGroups.id))
          .orderBy(
            asc(promptGroups.sortOrder),
            asc(promptGroups.name),
            asc(prompts.sortOrder),
            asc(prompts.id),
          )
      : [];

  const groupOptions = Array.from(
    promptRows
      .reduce((groups, prompt) => {
        const current = groups.get(prompt.groupId);
        if (current) current.promptCount += 1;
        else
          groups.set(prompt.groupId, {
            id: prompt.groupId,
            name: prompt.groupName,
            promptCount: 1,
          });
        return groups;
      }, new Map<number, { id: number; name: string; promptCount: number }>())
      .values(),
  );
  const selectedGroupIds = parseIdList(sp.group, MAX_GROUP_FILTER).filter((id) =>
    groupOptions.some((group) => group.id === id),
  );
  const scopedPrompts =
    selectedGroupIds.length > 0
      ? promptRows.filter((prompt) => selectedGroupIds.includes(prompt.groupId))
      : promptRows;

  const groupItems: GroupFilterItem[] = [
    {
      key: 'all',
      label: 'All',
      count: null,
      href: resultsHref(sp, 'group', []),
      active: selectedGroupIds.length === 0,
    },
    ...groupOptions.map((group) => {
      const active = selectedGroupIds.includes(group.id);
      // A chip toggles its own group and leaves the others alone.
      const next = active
        ? selectedGroupIds.filter((id) => id !== group.id)
        : [...selectedGroupIds, group.id];
      return {
        key: String(group.id),
        label: group.name,
        count: group.promptCount,
        href: resultsHref(sp, 'group', next.map(String)),
        active,
      };
    }),
  ];

  let modelCells: ModelCompareCell[] = [];
  if (selectedModelKeys.length >= MIN_COMPARE_MODELS) {
    const modelIds = Array.from(
      new Set(selectedModelKeys.map((key) => splitModelColumnKey(key)!.modelId)),
    );
    // Archived runs never contribute: unlike run mode there is no explicit
    // selection that could ask for one back. Errors are fetched so a newer
    // failed attempt can be reported rather than silently skipped.
    const rows = await db
      .select({
        result: runResults,
        runCreatedAt: runs.createdAt,
        runParams: runs.params,
        machineId: runs.machineId,
        modelId: runs.modelId,
      })
      .from(runResults)
      .innerJoin(runs, eq(runResults.runId, runs.id))
      .where(
        and(
          isNull(runs.archivedAt),
          inArray(runs.modelId, modelIds),
          inArray(runResults.status, ['ok', 'error']),
        ),
      );

    modelCells = rows.map((row) => ({
      ...toCell(row.result, { createdAt: row.runCreatedAt, params: row.runParams }),
      columnKey: modelColumnKey(row.machineId, row.modelId),
    }));
  }

  const modelMatrix = buildModelMatrix(selectedModelKeys, scopedPrompts, modelCells);

  // Per-column tallies over the cells actually on screen — more useful than a
  // whole-run total, which would count prompts this comparison filtered out.
  const modelShown = selectedModelKeys.map((_, index) => {
    const cells = modelMatrix.rows
      .map((row) => row.cells[index])
      .filter((cell): cell is CompareCellView => cell !== null);
    const rates = cells
      .map((cell) => cell.tokensPerSec)
      .filter((rate): rate is number => typeof rate === 'number');
    return {
      answered: cells.length,
      ...countRatings(cells.map((cell) => cell.rating)),
      avgRate:
        rates.length > 0 ? rates.reduce((total, rate) => total + rate, 0) / rates.length : null,
    };
  });

  // ------------------------------------------------------------------ render
  const isModels = mode === 'models';
  const rows = isModels ? modelMatrix.rows : buildCompareMatrix(selectedRunIds, runCells);
  const columnCount = isModels ? selectedModelKeys.length : selectedRunIds.length;
  const minColumns = isModels ? MIN_COMPARE_MODELS : MIN_COMPARE_RUNS;

  const tab = (active: boolean) =>
    `rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
      active
        ? 'bg-zinc-900 text-white dark:bg-zinc-100 dark:text-zinc-900'
        : 'text-zinc-600 hover:bg-zinc-100 dark:text-zinc-400 dark:hover:bg-zinc-900'
    }`;

  return (
    <div className="flex flex-1 flex-col gap-8 p-8">
      <div className="flex flex-col gap-3">
        <h1 className="text-2xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
          Results
        </h1>

        <div className="flex w-fit gap-1 rounded-lg border border-zinc-200 p-1 dark:border-zinc-800">
          <Link href={resultsHref(sp, 'mode', ['models'])} className={tab(isModels)}>
            By model
          </Link>
          <Link href={resultsHref(sp, 'mode', ['runs'])} className={tab(!isModels)}>
            By run
          </Link>
        </div>

        {/* No blurb: the pickers and column headers say what the table is. The
            only thing the UI cannot show on its own is what it left out. */}
        {!isModels && hiddenArchivedCount > 0 && (
          <p className="text-sm text-zinc-500 dark:text-zinc-400">
            {hiddenArchivedCount} archived run{hiddenArchivedCount === 1 ? ' is' : 's are'} not
            listed below.
          </p>
        )}
      </div>

      {isModels ? (
        <>
          <ModelPicker columns={modelColumns} />
          <GroupFilter items={groupItems} />
        </>
      ) : (
        <RunPicker runs={pickerRuns} />
      )}

      {columnCount < minColumns ? (
        <div className="rounded-lg border border-dashed border-zinc-300 p-8 text-center text-sm text-zinc-500 dark:border-zinc-700 dark:text-zinc-400">
          {isModels
            ? 'Select a model above to see its results — or several to compare them.'
            : `Select at least ${MIN_COMPARE_RUNS} runs above to build the comparison matrix.`}
        </div>
      ) : rows.length === 0 ? (
        <div className="rounded-lg border border-dashed border-zinc-300 p-8 text-center text-sm text-zinc-500 dark:border-zinc-700 dark:text-zinc-400">
          {isModels
            ? `None of the prompts in scope has a result from the selected ${
                columnCount === 1 ? 'model' : 'models'
              } yet.`
            : 'The selected runs have no results to compare.'}
        </div>
      ) : (
        <section className="flex flex-col gap-3">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <h2 className="text-sm font-medium text-zinc-700 dark:text-zinc-300">
              {rows.length} prompt{rows.length === 1 ? '' : 's'} × {columnCount}{' '}
              {isModels ? 'model' : 'run'}
              {columnCount === 1 ? '' : 's'}
            </h2>
            {isModels && modelMatrix.uncoveredPrompts > 0 && (
              <span className="text-xs text-zinc-500 dark:text-zinc-400">
                {modelMatrix.uncoveredPrompts} prompt
                {modelMatrix.uncoveredPrompts === 1 ? '' : 's'} in scope not answered by any
                selected model
              </span>
            )}
          </div>

          <div className="overflow-x-auto rounded-lg border border-zinc-200 dark:border-zinc-800">
            <table className="w-full min-w-max border-collapse text-left text-sm">
              <thead className="border-b border-zinc-200 bg-zinc-50 dark:border-zinc-800 dark:bg-zinc-900">
                <tr>
                  <th className="sticky left-0 z-10 w-64 min-w-64 border-r border-zinc-200 bg-zinc-50 px-4 py-3 text-xs font-medium uppercase tracking-wide text-zinc-500 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-400">
                    Prompt
                  </th>

                  {isModels
                    ? modelHeaders.map((column, index) => (
                        <th
                          key={column.key}
                          className="w-96 min-w-80 max-w-md border-l border-zinc-200 px-4 py-3 align-top dark:border-zinc-800"
                        >
                          <div className="flex flex-col gap-1">
                            <span className="font-mono text-xs font-semibold text-zinc-900 dark:text-zinc-50">
                              {column.modelId}
                            </span>
                            <span className="text-xs font-normal text-zinc-600 dark:text-zinc-400">
                              @ {column.machineName}
                            </span>
                            <span className="text-xs font-normal text-zinc-500 dark:text-zinc-500">
                              {modelShown[index].answered}/{rows.length} answered · latest{' '}
                              {formatDateTime(column.latestRunAt)}
                            </span>
                            <span className="flex flex-wrap items-center gap-2 text-xs font-normal">
                              <span
                                className="text-zinc-500 dark:text-zinc-400"
                                title="good / meh / bad over the cells shown"
                              >
                                <span className={RATING_META.good.text}>
                                  {modelShown[index].good}
                                </span>
                                /
                                <span className={RATING_META.meh.text}>
                                  {modelShown[index].meh}
                                </span>
                                /
                                <span className={RATING_META.bad.text}>
                                  {modelShown[index].bad}
                                </span>
                              </span>
                              <span className="text-zinc-500 dark:text-zinc-400">
                                {formatRate(modelShown[index].avgRate)}
                              </span>
                            </span>
                          </div>
                        </th>
                      ))
                    : runColumns.map((run) => (
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
                              <span
                                className="text-zinc-500 dark:text-zinc-400"
                                title="good / meh / bad"
                              >
                                <span className={RATING_META.good.text}>{run.good}</span>/
                                <span className={RATING_META.meh.text}>{run.meh}</span>/
                                <span className={RATING_META.bad.text}>{run.bad}</span>
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
                {rows.map((row) => (
                  <CompareRow key={row.key} row={row} anchoredToLivePrompt={isModels} />
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </div>
  );
}
