/** Runs and their result rows, including the list and dashboard aggregates. */

import { asc, desc, eq, inArray, isNotNull, isNull, sql, type SQL } from 'drizzle-orm';
import { db } from '@/db';
import {
  runResults,
  runs,
  type NewRun,
  type NewRunResult,
  type Run,
  type RunResult,
} from '../schema';
import {
  combine,
  scopeFromCustomerId,
  scopeValues,
  whereScoped,
  type Scope,
} from '../scope';
import { num, scopeThroughParent, type DbHandle } from './scoped';

// ---------------------------------------------------------------------------
// Runs
// ---------------------------------------------------------------------------

export async function getRun(scope: Scope, id: number): Promise<Run | null> {
  const [row] = await db.select().from(runs).where(whereScoped(scope, runs, eq(runs.id, id)));
  return row ?? null;
}

export interface ListRunsOptions {
  status?: string;
  archived?: 'exclude' | 'only' | 'all';
  runIds?: number[];
  limit?: number;
}

export async function listRuns(scope: Scope, opts: ListRunsOptions = {}): Promise<Run[]> {
  if (opts.runIds !== undefined && opts.runIds.length === 0) return [];

  const query = db
    .select()
    .from(runs)
    .where(
      whereScoped(
        scope,
        runs,
        opts.status === undefined ? undefined : eq(runs.status, opts.status),
        archivedCondition(opts.archived ?? 'all'),
        opts.runIds === undefined ? undefined : inArray(runs.id, opts.runIds),
      ),
    )
    .orderBy(desc(runs.createdAt), desc(runs.id));

  return opts.limit === undefined ? query : query.limit(opts.limit);
}

function archivedCondition(archived: 'exclude' | 'only' | 'all'): SQL | undefined {
  if (archived === 'exclude') return isNull(runs.archivedAt);
  if (archived === 'only') return isNotNull(runs.archivedAt);
  return undefined;
}

export async function createRun(
  scope: Scope,
  values: NewRun,
  handle: DbHandle = db,
): Promise<{ id: number }> {
  const [row] = await handle
    .insert(runs)
    .values({ ...values, ...scopeValues(scope) })
    .returning({ id: runs.id });
  return row;
}

export async function updateRunStatus(
  scope: Scope,
  id: number,
  values: Pick<NewRun, 'status' | 'startedAt' | 'finishedAt'>,
): Promise<void> {
  await db.update(runs).set(values).where(whereScoped(scope, runs, eq(runs.id, id)));
}

export async function updateRunComment(
  scope: Scope,
  id: number,
  comment: string | null,
): Promise<void> {
  await db.update(runs).set({ comment }).where(whereScoped(scope, runs, eq(runs.id, id)));
}

export async function setRunArchivedAt(
  scope: Scope,
  id: number,
  archivedAt: Date | null,
): Promise<void> {
  await db.update(runs).set({ archivedAt }).where(whereScoped(scope, runs, eq(runs.id, id)));
}

export async function deleteRun(scope: Scope, id: number): Promise<void> {
  await db.delete(runs).where(whereScoped(scope, runs, eq(runs.id, id)));
}

export async function countArchivedRuns(scope: Scope): Promise<number> {
  const [row] = await db
    .select({ count: sql<number>`count(*)`.mapWith(Number) })
    .from(runs)
    .where(whereScoped(scope, runs, isNotNull(runs.archivedAt)));
  return row?.count ?? 0;
}

/**
 * The scope entry point for background work.
 *
 * `run-executor.ts` runs outside any request — MCP `execute_run` is
 * fire-and-forget — so it cannot derive a scope from a session. It reads the run
 * row instead and takes the scope *from* it. This is deliberately the only
 * function in the repositories that is not itself scoped, and the only one
 * Phase 4 has to put an authorization check in front of.
 */
export async function scopeForRun(runId: number): Promise<{ scope: Scope; run: Run } | null> {
  const [run] = await db.select().from(runs).where(eq(runs.id, runId));
  if (!run) return null;
  // Phase 5: scopeFromCustomerId(run.customerId).
  return { scope: scopeFromCustomerId(null), run };
}

// ---------------------------------------------------------------------------
// Run results
// ---------------------------------------------------------------------------

export async function insertRunResults(
  scope: Scope,
  runId: number,
  rows: NewRunResult[],
  handle: DbHandle = db,
): Promise<void> {
  // Every row carries `runId`, which is what scopes a result.
  void scope;
  void runId;
  if (rows.length === 0) return;
  await handle.insert(runResults).values(rows);
}

export function listRunResults(scope: Scope, runId: number): Promise<RunResult[]> {
  return db
    .select()
    .from(runResults)
    .where(resultsOfRun(scope, runId))
    .orderBy(asc(runResults.sortOrder), asc(runResults.id));
}

/** A single result, scoped through the run it belongs to. */
export async function getRunResult(scope: Scope, id: number): Promise<RunResult | null> {
  const [row] = await db
    .select({ result: runResults })
    .from(runResults)
    .innerJoin(runs, eq(runResults.runId, runs.id))
    .where(whereScoped(scope, runs, eq(runResults.id, id)));
  return row?.result ?? null;
}

export function listResultStatuses(
  scope: Scope,
  runId: number,
): Promise<{ id: number; status: string }[]> {
  return db
    .select({ id: runResults.id, status: runResults.status })
    .from(runResults)
    .where(resultsOfRun(scope, runId))
    .orderBy(asc(runResults.sortOrder), asc(runResults.id));
}

export async function countPendingResults(scope: Scope, runId: number): Promise<number> {
  const [row] = await db
    .select({ count: sql<number>`count(*)`.mapWith(Number) })
    .from(runResults)
    .where(combine([resultsOfRun(scope, runId), eq(runResults.status, 'pending')]));
  return row?.count ?? 0;
}

/**
 * Updates one result. Both keys go into the WHERE: the executor always knows
 * which run it is working on, and the run is what carries the scope.
 */
export async function updateRunResult(
  scope: Scope,
  runId: number,
  resultId: number,
  values: Partial<NewRunResult>,
): Promise<void> {
  await db
    .update(runResults)
    .set(values)
    .where(combine([resultsOfRun(scope, runId), eq(runResults.id, resultId)]));
}

export async function resetResultsInStatus(
  scope: Scope,
  runId: number,
  status: string,
  values: Partial<NewRunResult>,
): Promise<void> {
  await db
    .update(runResults)
    .set(values)
    .where(combine([resultsOfRun(scope, runId), eq(runResults.status, status)]));
}

/**
 * Sets a result's verdict. Only a result id is available here (both the UI and
 * MCP have one), so the scope comes through the parent run.
 *
 * Returns the row's run id — the caller needs it to revalidate — plus what was
 * actually stored, or null when nothing matched.
 */
export async function rateResult(
  scope: Scope,
  resultId: number,
  values: { rating: 'good' | 'meh' | 'bad' | null; ratingNote?: string | null },
): Promise<{ runId: number; rating: string | null; ratingNote: string | null } | null> {
  const [row] = await db
    .update(runResults)
    .set(values)
    .where(resultById(scope, resultId))
    .returning({
      runId: runResults.runId,
      rating: runResults.rating,
      ratingNote: runResults.ratingNote,
    });
  return row ?? null;
}

/** Saves a result's free-text note without touching its rating. */
export async function setResultNote(
  scope: Scope,
  resultId: number,
  note: string | null,
): Promise<{ runId: number } | null> {
  const [row] = await db
    .update(runResults)
    .set({ ratingNote: note })
    .where(resultById(scope, resultId))
    .returning({ runId: runResults.runId });
  return row ?? null;
}

export function listResultRatings(
  scope: Scope,
  runId: number,
): Promise<{ rating: string | null }[]> {
  return db
    .select({ rating: runResults.rating })
    .from(runResults)
    .where(resultsOfRun(scope, runId));
}

/** `run_results` rows of one run — scoped through the run. */
function resultsOfRun(scope: Scope, runId: number): SQL | undefined {
  return combine([
    eq(runResults.runId, runId),
    scopeThroughParent(scope, runResults.runId, runs, runs.id),
  ]);
}

/** One `run_results` row by its own id — scoped through its run. */
function resultById(scope: Scope, id: number): SQL | undefined {
  return combine([
    eq(runResults.id, id),
    scopeThroughParent(scope, runResults.runId, runs, runs.id),
  ]);
}

// ---------------------------------------------------------------------------
// Aggregates
// ---------------------------------------------------------------------------

/**
 * The per-run tallies the runs list, the dashboard and `/results` all need.
 *
 * `unrated` is *not* counted in SQL: it is `total - good - meh - bad`, which is
 * what preserves `countRatings`' rule that an unrecognised stored rating reads
 * as unrated. A legacy value must never vanish from the totals.
 */
const TALLIES = {
  ok: sql<number>`count(case when ${runResults.status} = 'ok' then 1 end)`.mapWith(Number),
  error: sql<number>`count(case when ${runResults.status} = 'error' then 1 end)`.mapWith(Number),
  pending:
    sql<number>`count(case when ${runResults.status} in ('pending','running') then 1 end)`.mapWith(
      Number,
    ),
  good: sql<number>`count(case when ${runResults.rating} = 'good' then 1 end)`.mapWith(Number),
  meh: sql<number>`count(case when ${runResults.rating} = 'meh' then 1 end)`.mapWith(Number),
  bad: sql<number>`count(case when ${runResults.rating} = 'bad' then 1 end)`.mapWith(Number),
  total: sql<number>`count(${runResults.id})`.mapWith(Number),
  // Never `.mapWith(Number)`: `Number(null)` is 0, and "nothing measured" is
  // not "0 tok/s".
  avgRate: sql<number | null>`avg(${runResults.tokensPerSec})`.mapWith(num),
  totalDurationMs:
    sql<number>`coalesce(sum(coalesce(${runResults.durationMs}, 0)), 0)`.mapWith(Number),
};

export interface RunListFilter {
  archived: 'exclude' | 'only' | 'all';
  machineId: string | null;
  modelId: string | null;
  groupName: string | null;
  status: string | null;
}

export interface RunSummaryRow {
  run: Run;
  groupNames: string[];
  ok: number;
  error: number;
  pending: number;
  good: number;
  meh: number;
  bad: number;
  unrated: number;
  avgRate: number | null;
  totalDurationMs: number;
}

/**
 * One aggregate row per run matching the filter, newest first.
 *
 * The group filter runs as its own query rather than a correlated subquery: it
 * needs `run_results` on a different axis than the aggregate does, and two plain
 * statements beat one clever one here.
 */
export async function listRunSummaries(
  scope: Scope,
  filter: RunListFilter,
): Promise<RunSummaryRow[]> {
  const conditions: (SQL | undefined)[] = [
    archivedCondition(filter.archived),
    filter.modelId === null ? undefined : eq(runs.modelId, filter.modelId),
    filter.status === null ? undefined : eq(runs.status, filter.status),
  ];

  if (filter.machineId !== null) {
    // The page compares `String(run.machineId ?? '')` to the raw filter, so a
    // run with no machine never matches and a non-canonical number never does
    // either. Anything that cannot be that comparison matches nothing.
    const parsed = Number(filter.machineId);
    if (!Number.isInteger(parsed) || String(parsed) !== filter.machineId) return [];
    conditions.push(eq(runs.machineId, parsed));
  }

  if (filter.groupName !== null) {
    const groupRunIds = await db
      .selectDistinct({ runId: runResults.runId })
      .from(runResults)
      .innerJoin(runs, eq(runResults.runId, runs.id))
      .where(whereScoped(scope, runs, eq(runResults.groupName, filter.groupName)));

    if (groupRunIds.length === 0) return [];
    conditions.push(
      inArray(
        runs.id,
        groupRunIds.map((row) => row.runId),
      ),
    );
  }

  const rows = await db
    .select({ run: runs, ...TALLIES })
    .from(runs)
    .leftJoin(runResults, eq(runResults.runId, runs.id))
    .where(whereScoped(scope, runs, ...conditions))
    .groupBy(runs.id)
    .orderBy(desc(runs.createdAt), desc(runs.id));

  const groupNames = await runGroupNames(
    scope,
    rows.map((row) => row.run.id),
  );

  return rows.map((row) => ({
    run: row.run,
    groupNames: groupNames.get(row.run.id) ?? [],
    ok: row.ok,
    error: row.error,
    pending: row.pending,
    good: row.good,
    meh: row.meh,
    bad: row.bad,
    unrated: row.total - row.good - row.meh - row.bad,
    avgRate: row.avgRate,
    totalDurationMs: row.totalDurationMs,
  }));
}

/**
 * Distinct result group names per run, in the order the rows were written.
 *
 * `min(id)` is what makes "first seen" reproducible — the page used to read it
 * off an unordered full-table scan, which is insertion order in practice but
 * nothing guarantees it.
 */
export async function runGroupNames(
  scope: Scope,
  runIds: number[],
): Promise<Map<number, string[]>> {
  if (runIds.length === 0) return new Map();

  const rows = await db
    .select({
      runId: runResults.runId,
      groupName: runResults.groupName,
      firstId: sql<number>`min(${runResults.id})`.mapWith(Number),
    })
    .from(runResults)
    .innerJoin(runs, eq(runResults.runId, runs.id))
    .where(whereScoped(scope, runs, inArray(runResults.runId, runIds)))
    .groupBy(runResults.runId, runResults.groupName)
    .orderBy(asc(runResults.runId), asc(sql`min(${runResults.id})`));

  const byRun = new Map<number, string[]>();
  for (const row of rows) {
    const list = byRun.get(row.runId);
    if (list) list.push(row.groupName);
    else byRun.set(row.runId, [row.groupName]);
  }
  return byRun;
}

/**
 * The values the runs filter bar offers.
 *
 * Deliberately derived from *all* runs rather than the filtered set — otherwise
 * picking a filter would erase the options next to it.
 */
export async function runFilterOptions(scope: Scope): Promise<{
  machineIds: number[];
  models: string[];
  groups: string[];
}> {
  const [machineRows, modelRows, groupRows] = await Promise.all([
    db.selectDistinct({ machineId: runs.machineId }).from(runs).where(whereScoped(scope, runs)),
    db.selectDistinct({ modelId: runs.modelId }).from(runs).where(whereScoped(scope, runs)),
    db
      .selectDistinct({ groupName: runResults.groupName })
      .from(runResults)
      .innerJoin(runs, eq(runResults.runId, runs.id))
      .where(whereScoped(scope, runs)),
  ]);

  return {
    machineIds: machineRows
      .map((row) => row.machineId)
      .filter((id): id is number => id !== null),
    models: modelRows.map((row) => row.modelId).sort(),
    groups: groupRows.map((row) => row.groupName).sort(),
  };
}

/**
 * Per-run progress and verdict tallies, for a set of runs already chosen.
 *
 * Unlike {@link listRunSummaries} this keeps `pending` and `running` apart —
 * the MCP `list_runs` tool reports them separately, because an agent polling a
 * run needs to know whether anything is actually moving.
 */
export async function runResultTallies(
  scope: Scope,
  runIds: number[],
): Promise<
  Map<
    number,
    {
      total: number;
      ok: number;
      error: number;
      pending: number;
      running: number;
      good: number;
      meh: number;
      bad: number;
      unrated: number;
      avgRate: number | null;
    }
  >
> {
  if (runIds.length === 0) return new Map();

  const rows = await db
    .select({
      runId: runResults.runId,
      total: TALLIES.total,
      ok: TALLIES.ok,
      error: TALLIES.error,
      pending: sql<number>`count(case when ${runResults.status} = 'pending' then 1 end)`.mapWith(
        Number,
      ),
      running: sql<number>`count(case when ${runResults.status} = 'running' then 1 end)`.mapWith(
        Number,
      ),
      good: TALLIES.good,
      meh: TALLIES.meh,
      bad: TALLIES.bad,
      avgRate: TALLIES.avgRate,
    })
    .from(runResults)
    .innerJoin(runs, eq(runResults.runId, runs.id))
    .where(whereScoped(scope, runs, inArray(runResults.runId, runIds)))
    .groupBy(runResults.runId);

  return new Map(
    rows.map((row) => [
      row.runId,
      {
        total: row.total,
        ok: row.ok,
        error: row.error,
        pending: row.pending,
        running: row.running,
        good: row.good,
        meh: row.meh,
        bad: row.bad,
        unrated: row.total - row.good - row.meh - row.bad,
        avgRate: row.avgRate,
      },
    ]),
  );
}

/** Rating totals across every result of the runs in scope. */
export async function ratingTotals(
  scope: Scope,
  opts: { archived: 'exclude' | 'only' | 'all' },
): Promise<{ good: number; meh: number; bad: number; unrated: number }> {
  const [row] = await db
    .select({
      good: TALLIES.good,
      meh: TALLIES.meh,
      bad: TALLIES.bad,
      total: TALLIES.total,
    })
    .from(runResults)
    .innerJoin(runs, eq(runResults.runId, runs.id))
    .where(whereScoped(scope, runs, archivedCondition(opts.archived)));

  if (!row) return { good: 0, meh: 0, bad: 0, unrated: 0 };
  return {
    good: row.good,
    meh: row.meh,
    bad: row.bad,
    unrated: row.total - row.good - row.meh - row.bad,
  };
}

export async function countRuns(
  scope: Scope,
  opts: { archived: 'exclude' | 'only' | 'all' },
): Promise<number> {
  const [row] = await db
    .select({ count: sql<number>`count(*)`.mapWith(Number) })
    .from(runs)
    .where(whereScoped(scope, runs, archivedCondition(opts.archived)));
  return row?.count ?? 0;
}

/** Shared with `src/db/repo/results.ts`, which pivots the same tallies by run. */
export { TALLIES as RESULT_TALLIES };

/** Also shared: `/results` filters runs by the same archived vocabulary. */
export { archivedCondition as runArchivedCondition };
