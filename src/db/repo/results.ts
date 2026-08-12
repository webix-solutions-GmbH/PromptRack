/**
 * Cross-entity reads for `/results`.
 *
 * Each pivot gets exactly the rows it uses: run mode fetches the results of the
 * selected runs, model mode fetches the results of the selected (machine, model)
 * pairs. Neither loads the whole `run_results` table, which is what the page
 * used to do in both modes on every request.
 */

import { and, asc, desc, eq, inArray, isNull, or } from 'drizzle-orm';
import { db } from '@/db';
import { runResults, runs, type Run, type RunResult } from '../schema';
import { whereScoped, type Scope } from '../scope';
import { RESULT_TALLIES, runArchivedCondition } from './runs';

/** One selectable run in the run-mode picker, with its tallies. */
export interface ComparableRunRow {
  run: Run;
  ok: number;
  error: number;
  good: number;
  meh: number;
  bad: number;
  avgRate: number | null;
}

export async function listComparableRuns(scope: Scope): Promise<ComparableRunRow[]> {
  const rows = await db
    .select({
      run: runs,
      ok: RESULT_TALLIES.ok,
      error: RESULT_TALLIES.error,
      good: RESULT_TALLIES.good,
      meh: RESULT_TALLIES.meh,
      bad: RESULT_TALLIES.bad,
      avgRate: RESULT_TALLIES.avgRate,
    })
    .from(runs)
    .leftJoin(runResults, eq(runResults.runId, runs.id))
    .where(whereScoped(scope, runs))
    .groupBy(runs.id)
    .orderBy(desc(runs.createdAt), desc(runs.id));

  return rows;
}

/** The `runs` fields model-column building needs, plus their `ok` results. */
export interface ModelColumnInputs {
  runs: {
    id: number;
    machineId: number | null;
    machineSnapshot: string;
    modelId: string;
    createdAt: Date;
  }[];
  results: {
    runId: number;
    promptId: number | null;
    status: string;
    rating: string | null;
    tokensPerSec: number | null;
  }[];
}

/**
 * Narrowed in SQL to what `buildModelColumns` actually reads: non-archived runs,
 * and only their `ok` results. The output is identical because the pure function
 * already skips archived runs and non-ok results itself.
 */
export async function modelColumnInputs(scope: Scope): Promise<ModelColumnInputs> {
  const [runRows, resultRows] = await Promise.all([
    db
      .select({
        id: runs.id,
        machineId: runs.machineId,
        machineSnapshot: runs.machineSnapshot,
        modelId: runs.modelId,
        createdAt: runs.createdAt,
      })
      .from(runs)
      .where(whereScoped(scope, runs, runArchivedCondition('exclude')))
      .orderBy(desc(runs.createdAt), desc(runs.id)),
    db
      .select({
        runId: runResults.runId,
        promptId: runResults.promptId,
        status: runResults.status,
        rating: runResults.rating,
        tokensPerSec: runResults.tokensPerSec,
      })
      .from(runResults)
      .innerJoin(runs, eq(runResults.runId, runs.id))
      .where(
        whereScoped(
          scope,
          runs,
          runArchivedCondition('exclude'),
          eq(runResults.status, 'ok'),
        ),
      )
      .orderBy(asc(runResults.id)),
  ]);

  return { runs: runRows, results: resultRows };
}

/** A result plus the two run fields a cell is not frozen with. */
export interface CompareCellRow {
  result: RunResult;
  runCreatedAt: Date;
  runParams: string | null;
}

/** Run mode: every result of the selected runs. */
export async function compareCellsForRuns(
  scope: Scope,
  runIds: number[],
): Promise<CompareCellRow[]> {
  if (runIds.length === 0) return [];
  return db
    .select({
      result: runResults,
      runCreatedAt: runs.createdAt,
      runParams: runs.params,
    })
    .from(runResults)
    .innerJoin(runs, eq(runResults.runId, runs.id))
    .where(whereScoped(scope, runs, inArray(runResults.runId, runIds)));
}

/** One selected model column, as a (machine, model) pair. */
export interface ModelColumnRef {
  machineId: number | null;
  modelId: string;
}

export interface ModelCompareCellRow extends CompareCellRow {
  machineId: number | null;
  modelId: string;
}

/**
 * Model mode: the `ok` and `error` results of non-archived runs, restricted to
 * the selected (machine, model) *pairs*.
 *
 * The pair predicate matters: filtering on the model id alone would load the
 * same model's results from every other machine and then throw them away, and
 * `tokens_per_sec` is a property of the hardware. Errors are fetched too, so a
 * newer failed attempt can be reported rather than silently skipped.
 */
export async function compareCellsForModels(
  scope: Scope,
  columns: ModelColumnRef[],
  promptIds: number[] | null,
): Promise<ModelCompareCellRow[]> {
  if (columns.length === 0) return [];
  if (promptIds !== null && promptIds.length === 0) return [];

  const pairs = columns.map((column) =>
    column.machineId === null
      ? and(isNull(runs.machineId), eq(runs.modelId, column.modelId))
      : and(eq(runs.machineId, column.machineId), eq(runs.modelId, column.modelId)),
  );

  return db
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
      whereScoped(
        scope,
        runs,
        runArchivedCondition('exclude'),
        inArray(runResults.status, ['ok', 'error']),
        pairs.length === 1 ? pairs[0] : or(...pairs),
        promptIds === null ? undefined : inArray(runResults.promptId, promptIds),
      ),
    );
}
