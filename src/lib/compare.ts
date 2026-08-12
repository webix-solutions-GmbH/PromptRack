/**
 * Pure helpers for the compare matrix.
 *
 * Kept free of server-only imports so both the page (server) and the picker
 * (client) can share the selection parsing, and so the row-matching logic is
 * unit-testable.
 */

import type { Rating } from './rating';
import type { RunResultStatus } from './run-events';
import type { ToolChoice, ToolMode } from './tools';

/** Hard cap on how many runs can be compared side by side. */
export const MAX_COMPARE_RUNS = 4;
/** Minimum selection before a matrix can be rendered. */
export const MIN_COMPARE_RUNS = 2;

/**
 * Which pivot the compare page is showing.
 *
 * `runs` compares hand-picked runs — the only way to put two runs of the *same*
 * model side by side (quantization swap, temperature A/B, prompt rewrite).
 * `models` takes the prompts as the base and fills each model's column with its
 * most recent usable result, so the answer to "which model is best at my
 * prompts" does not depend on remembering which run was which.
 */
export type CompareMode = 'runs' | 'models';

export const MAX_COMPARE_MODELS = 6;
/**
 * One model is a valid selection: the same prompt × model matrix with a single
 * column is exactly "show me everything this model answered", which is the
 * cheapest way to review a model's results across all of its runs. Run mode
 * still needs two — a single run is already its own detail page.
 */
export const MIN_COMPARE_MODELS = 1;

/** Reads an explicit `?mode=` value; `null` leaves the default to the caller. */
export function parseCompareMode(raw: string | string[] | undefined): CompareMode | null {
  const value = Array.isArray(raw) ? raw[0] : raw;
  return value === 'runs' || value === 'models' ? value : null;
}

/** One selectable run, as shown in the picker and in the column headers. */
export interface CompareRunView {
  id: number;
  modelId: string;
  machineName: string;
  status: string;
  /** Archived runs are kept out of the picker unless already selected. */
  archived: boolean;
  createdAt: number;
  groupNames: string[];
  good: number;
  meh: number;
  bad: number;
  ok: number;
  error: number;
  avgRate: number | null;
}

/**
 * A newer attempt at the same prompt that was *not* used as the cell.
 *
 * Model mode shows the latest result that actually produced an answer, so an
 * endpoint that was down during the most recent run cannot hide a perfectly
 * good older result. That silent fallback has to be visible, hence this.
 */
export interface SupersededAttempt {
  runId: number;
  status: RunResultStatus;
  createdAt: number;
}

/** One cell of the matrix: a single `run_results` row. */
export interface CompareCellView {
  id: number;
  runId: number;
  /** `runs.created_at` — what "most recent result" is ordered by. */
  runCreatedAt: number;
  /**
   * Opaque workspace key. Phase 3: `''` for every cell; Phase 5: the customer
   * id. The deleted-prompt text fallback only matches within one key, so two
   * customers' identical prompts can never collapse into one row.
   */
  scopeKey: string;
  promptId: number | null;
  sortOrder: number;
  groupName: string;
  promptTitle: string;
  promptText: string;
  /** Effective system prompt frozen into the row; part of the drift check. */
  systemPromptText: string | null;
  /** Raw `tools_snapshot` JSON, compared verbatim for drift. */
  toolsSnapshot: string | null;
  toolMode: ToolMode;
  toolChoice: ToolChoice | null;
  maxTurns: number;
  /** Raw `runs.params` JSON (temperature / max_tokens), or null for defaults. */
  runParams: string | null;
  status: RunResultStatus;
  responseText: string | null;
  error: string | null;
  durationMs: number | null;
  ttftMs: number | null;
  completionTokens: number | null;
  tokensPerSec: number | null;
  tokensEstimated: boolean;
  rating: Rating | null;
  ratingNote: string | null;
  /** Null on an ordinary prompt; set for a tool run. */
  turnCount: number | null;
  toolCallCount: number | null;
  /**
   * The tool names the model called, in order, with repeats — the quickest way
   * to see whether a model picked the right tools for a prompt.
   */
  toolCallNames: string[];
  /** Set by {@link buildModelMatrix} when a newer attempt was skipped. */
  superseded?: SupersededAttempt | null;
}

/** One row of the matrix: a prompt, plus one cell (or `null`) per column. */
export interface CompareRowView {
  /** Stable React key — `prompt:<id>` or `text:<n>` for deleted prompts. */
  key: string;
  promptId: number | null;
  groupName: string;
  promptTitle: string;
  promptText: string;
  /** Same length and order as the selected run ids; `null` = prompt missing. */
  cells: (CompareCellView | null)[];
}

/**
 * Parse a `?runs=1,5,7` selection into a clean list of run ids.
 *
 * Ignores non-numeric junk, de-duplicates, and truncates to {@link MAX_COMPARE_RUNS}
 * so the URL can never blow up the table. Order is the order given in the URL.
 */
export function parseRunIds(
  raw: string | string[] | undefined,
  max = MAX_COMPARE_RUNS,
): number[] {
  return parseIdList(raw, max);
}

/** Shared `1,5,7` / repeated-param id parsing. See {@link parseRunIds}. */
export function parseIdList(raw: string | string[] | undefined, max: number): number[] {
  const value = Array.isArray(raw) ? raw.join(',') : raw;
  if (!value) return [];

  const ids: number[] = [];
  for (const part of value.split(',')) {
    const id = Number(part.trim());
    if (!Number.isInteger(id) || id <= 0) continue;
    if (ids.includes(id)) continue;
    ids.push(id);
    if (ids.length >= max) break;
  }
  return ids;
}

/** Serialize a selection back into the `runs` search param. */
export function serializeRunIds(ids: number[]): string {
  return ids.join(',');
}

/** Normalizes prompt text so trivial whitespace differences still match. */
function textKey(promptText: string): string {
  return promptText.replace(/\s+/g, ' ').trim();
}

interface RowBuilder extends CompareRowView {
  /** Column index the row was first seen in — drives row ordering. */
  firstColumn: number;
  /** `sortOrder` within that first column. */
  firstSortOrder: number;
}

/**
 * Pivot flat `run_results` rows into a prompt × run matrix.
 *
 * Row matching is primarily by `promptId`. Results whose prompt was deleted
 * (`promptId === null`) fall back to matching on identical prompt text, which
 * lets them line up with rows that still carry a prompt id.
 *
 * Rows are ordered by first appearance: every prompt of the first column in its
 * run order, then prompts only present in the second column, and so on. If a
 * single run somehow maps two results onto the same row, the first one wins.
 */
export function buildCompareMatrix(
  runIds: number[],
  results: CompareCellView[],
): CompareRowView[] {
  const rows: RowBuilder[] = [];
  const byPromptId = new Map<number, RowBuilder>();
  const byText = new Map<string, RowBuilder>();

  const columnOf = new Map<number, number>();
  runIds.forEach((runId, index) => columnOf.set(runId, index));

  const ordered = results
    .filter((result) => columnOf.has(result.runId))
    .sort((a, b) => {
      const columnDiff = columnOf.get(a.runId)! - columnOf.get(b.runId)!;
      if (columnDiff !== 0) return columnDiff;
      if (a.sortOrder !== b.sortOrder) return a.sortOrder - b.sortOrder;
      return a.id - b.id;
    });

  for (const result of ordered) {
    const column = columnOf.get(result.runId)!;
    // Prompt ids are global, so only the text fallback needs the scope key.
    const key = `${result.scopeKey} ${textKey(result.promptText)}`;

    let row: RowBuilder | undefined;
    if (result.promptId === null) {
      // Deleted prompt: the text is all we have to go on.
      row = byText.get(key);
    } else {
      row = byPromptId.get(result.promptId);
      // Adopt a row that was created by a deleted-prompt result with the same
      // text, so both halves of the pair land in one row.
      const textRow = byText.get(key);
      if (!row && textRow && textRow.promptId === null) row = textRow;
    }

    if (!row) {
      row = {
        key:
          result.promptId !== null ? `prompt:${result.promptId}` : `text:${rows.length}`,
        promptId: result.promptId,
        groupName: result.groupName,
        promptTitle: result.promptTitle,
        promptText: result.promptText,
        cells: runIds.map(() => null),
        firstColumn: column,
        firstSortOrder: result.sortOrder,
      };
      rows.push(row);
    }

    if (result.promptId !== null && !byPromptId.has(result.promptId)) {
      byPromptId.set(result.promptId, row);
      if (row.promptId === null) row.promptId = result.promptId;
    }
    if (!byText.has(key)) byText.set(key, row);

    if (row.cells[column] === null) row.cells[column] = result;
  }

  return rows
    .sort((a, b) =>
      a.firstColumn !== b.firstColumn
        ? a.firstColumn - b.firstColumn
        : a.firstSortOrder - b.firstSortOrder,
    )
    .map((row) => ({
      key: row.key,
      promptId: row.promptId,
      groupName: row.groupName,
      promptTitle: row.promptTitle,
      promptText: row.promptText,
      cells: row.cells,
    }));
}

// ---------------------------------------------------------------------------
// Model mode: prompts as the base, one column per model
// ---------------------------------------------------------------------------

/**
 * Stable identity of a model column: `<machineId>|<modelId>`.
 *
 * Keyed on the machine *id* rather than its name so renaming an endpoint does
 * not split a column, and including the machine at all because `tokens_per_sec`
 * is a property of the hardware — one model on two boxes must stay two columns
 * or the speed numbers become noise. A deleted machine collapses to id `0`.
 */
export function modelColumnKey(machineId: number | null, modelId: string): string {
  return `${machineId ?? 0}|${modelId}`;
}

/** Inverse of {@link modelColumnKey}; `null` for anything malformed. */
export function splitModelColumnKey(
  key: string,
): { machineId: number | null; modelId: string } | null {
  const separator = key.indexOf('|');
  if (separator <= 0) return null;
  const machineId = Number(key.slice(0, separator));
  const modelId = key.slice(separator + 1);
  if (!Number.isInteger(machineId) || machineId < 0 || modelId.length === 0) return null;
  return { machineId: machineId === 0 ? null : machineId, modelId };
}

/**
 * Parse a model-column selection.
 *
 * Always repeated params (`?model=1|a&model=2|b`) rather than one comma-joined
 * value, because a model id is free-form text and must never need escaping.
 */
export function parseModelColumnKeys(
  raw: string | string[] | undefined,
  max = MAX_COMPARE_MODELS,
): string[] {
  if (raw === undefined) return [];
  const values = Array.isArray(raw) ? raw : [raw];
  const keys: string[] = [];
  for (const value of values) {
    if (splitModelColumnKey(value) === null) continue;
    if (keys.includes(value)) continue;
    keys.push(value);
    if (keys.length >= max) break;
  }
  return keys;
}

/** One selectable model column, as shown in the picker and column headers. */
export interface ModelColumnView {
  key: string;
  modelId: string;
  machineName: string;
  /** Non-archived runs that produced at least one usable result for this pair. */
  runCount: number;
  latestRunAt: number;
  /** Distinct prompts this model has a usable result for. */
  promptCount: number;
  good: number;
  meh: number;
  bad: number;
  avgRate: number | null;
}

/** The `runs` fields {@link buildModelColumns} needs. */
export interface ModelColumnRun {
  id: number;
  machineId: number | null;
  machineName: string;
  modelId: string;
  createdAt: number;
  archived: boolean;
}

/** The `run_results` fields {@link buildModelColumns} needs. */
export interface ModelColumnResult {
  runId: number;
  promptId: number | null;
  status: string;
  rating: string | null;
  tokensPerSec: number | null;
}

/**
 * Group every non-archived run into model columns.
 *
 * Only `ok` results count: a column exists because the model answered something,
 * not because a run was created. Archived runs are excluded outright — unlike
 * run mode there is no per-run selection that could ask for one back.
 */
export function buildModelColumns(
  runRows: readonly ModelColumnRun[],
  resultRows: readonly ModelColumnResult[],
): ModelColumnView[] {
  const resultsByRun = new Map<number, ModelColumnResult[]>();
  for (const result of resultRows) {
    const bucket = resultsByRun.get(result.runId);
    if (bucket) bucket.push(result);
    else resultsByRun.set(result.runId, [result]);
  }

  interface Accumulator extends ModelColumnView {
    promptIds: Set<number>;
    rates: number[];
  }
  const columns = new Map<string, Accumulator>();

  for (const run of runRows) {
    if (run.archived) continue;
    const okResults = (resultsByRun.get(run.id) ?? []).filter(
      (result) => result.status === 'ok',
    );
    if (okResults.length === 0) continue;

    const key = modelColumnKey(run.machineId, run.modelId);
    let column = columns.get(key);
    if (!column) {
      column = {
        key,
        modelId: run.modelId,
        machineName: run.machineName,
        runCount: 0,
        latestRunAt: run.createdAt,
        promptCount: 0,
        good: 0,
        meh: 0,
        bad: 0,
        avgRate: null,
        promptIds: new Set(),
        rates: [],
      };
      columns.set(key, column);
    }

    column.runCount += 1;
    if (run.createdAt >= column.latestRunAt) {
      column.latestRunAt = run.createdAt;
      // A deleted machine has no live name; the newest snapshot is the best one.
      column.machineName = run.machineName;
    }
    for (const result of okResults) {
      // A deleted prompt can never be a model-mode row (rows are anchored to
      // live prompts), so it is not part of the coverage count either.
      if (result.promptId !== null) column.promptIds.add(result.promptId);
      if (typeof result.tokensPerSec === 'number') column.rates.push(result.tokensPerSec);
    }
    const counts = countColumnRatings(okResults);
    column.good += counts.good;
    column.meh += counts.meh;
    column.bad += counts.bad;
  }

  return Array.from(columns.values())
    .map(({ promptIds, rates, ...column }) => ({
      ...column,
      promptCount: promptIds.size,
      avgRate:
        rates.length > 0 ? rates.reduce((total, rate) => total + rate, 0) / rates.length : null,
    }))
    .sort(
      (a, b) =>
        a.modelId.localeCompare(b.modelId) || a.machineName.localeCompare(b.machineName),
    );
}

function countColumnRatings(results: readonly { rating: string | null }[]) {
  const counts = { good: 0, meh: 0, bad: 0 };
  for (const result of results) {
    if (result.rating === 'good' || result.rating === 'meh' || result.rating === 'bad') {
      counts[result.rating] += 1;
    }
  }
  return counts;
}

/** A live prompt, which is what a model-mode row is anchored to. */
export interface ComparePromptView {
  id: number;
  groupId: number;
  groupName: string;
  title: string;
  text: string;
}

/** A result tagged with the model column it belongs to. */
export interface ModelCompareCell extends CompareCellView {
  columnKey: string;
}

export interface ModelMatrix {
  rows: CompareRowView[];
  /** Prompts in scope that none of the selected models has answered yet. */
  uncoveredPrompts: number;
}

function isNewer(candidate: ModelCompareCell, current: ModelCompareCell | undefined): boolean {
  if (!current) return true;
  if (candidate.runCreatedAt !== current.runCreatedAt) {
    return candidate.runCreatedAt > current.runCreatedAt;
  }
  return candidate.id > current.id;
}

/**
 * Pivot results into a prompt × model matrix, newest usable result per cell.
 *
 * "Usable" means `status === 'ok'`: a newer run whose row errored (endpoint
 * down, OOM) must not blank out a good older answer, but it is recorded as
 * {@link CompareCellView.superseded} so the fallback is never silent. Rows keep
 * the order of `promptRows`, and prompts nobody answered are counted rather than
 * rendered as an all-empty row.
 */
export function buildModelMatrix(
  columnKeys: readonly string[],
  promptRows: readonly ComparePromptView[],
  cells: readonly ModelCompareCell[],
): ModelMatrix {
  const columnOf = new Map<string, number>();
  columnKeys.forEach((key, index) => columnOf.set(key, index));
  const inScope = new Set(promptRows.map((prompt) => prompt.id));

  const best = new Map<string, ModelCompareCell>();
  const newest = new Map<string, ModelCompareCell>();

  for (const cell of cells) {
    if (cell.promptId === null || !inScope.has(cell.promptId)) continue;
    const column = columnOf.get(cell.columnKey);
    if (column === undefined) continue;

    const key = `${column}:${cell.promptId}`;
    if (isNewer(cell, newest.get(key))) newest.set(key, cell);
    if (cell.status === 'ok' && isNewer(cell, best.get(key))) best.set(key, cell);
  }

  const rows: CompareRowView[] = [];
  let uncoveredPrompts = 0;

  for (const prompt of promptRows) {
    const rowCells = columnKeys.map((_, column) => {
      const chosen = best.get(`${column}:${prompt.id}`);
      if (!chosen) return null;
      const latest = newest.get(`${column}:${prompt.id}`);
      const superseded =
        latest && latest.id !== chosen.id
          ? { runId: latest.runId, status: latest.status, createdAt: latest.runCreatedAt }
          : null;
      return { ...chosen, superseded };
    });

    if (rowCells.every((cell) => cell === null)) {
      uncoveredPrompts += 1;
      continue;
    }

    rows.push({
      key: `prompt:${prompt.id}`,
      promptId: prompt.id,
      groupName: prompt.groupName,
      promptTitle: prompt.title,
      promptText: prompt.text,
      cells: rowCells,
    });
  }

  return { rows, uncoveredPrompts };
}

// ---------------------------------------------------------------------------
// Drift
// ---------------------------------------------------------------------------

/** Recursively key-sorted JSON, so formatting differences are not drift. */
function stableJson(raw: string | null): string {
  if (raw === null) return '';
  try {
    return JSON.stringify(sortKeys(JSON.parse(raw) as unknown));
  } catch {
    return raw;
  }
}

function sortKeys(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(sortKeys);
  if (value === null || typeof value !== 'object') return value;
  const entries = Object.entries(value as Record<string, unknown>).sort(([a], [b]) =>
    a.localeCompare(b),
  );
  return Object.fromEntries(entries.map(([key, inner]) => [key, sortKeys(inner)]));
}

/**
 * Name the conditions that are *not* held constant across a row.
 *
 * The whole point of a comparison row is that only the model differs. Once a
 * column is "the latest result" rather than "one run", its cells can come from
 * runs with different system prompts, tools or temperatures — and a difference
 * in the answers would then be config, not model. Rather than hide that, say it.
 *
 * `livePromptText` (model mode, where the row is anchored to a live prompt)
 * additionally catches a prompt edited after every compared run.
 */
export function describeRowDrift(
  cells: readonly (CompareCellView | null)[],
  livePromptText?: string,
): string[] {
  const present = cells.filter((cell): cell is CompareCellView => cell !== null);
  if (present.length === 0) return [];

  const aspects: { label: string; of: (cell: CompareCellView) => string }[] = [
    { label: 'prompt text', of: (cell) => textKey(cell.promptText) },
    { label: 'system prompt', of: (cell) => textKey(cell.systemPromptText ?? '') },
    { label: 'tools', of: (cell) => stableJson(cell.toolsSnapshot) },
    { label: 'tool mode', of: (cell) => cell.toolMode },
    { label: 'tool choice', of: (cell) => cell.toolChoice ?? '(unset)' },
    { label: 'params', of: (cell) => stableJson(cell.runParams) },
  ];

  // max_turns only means anything once tools are in play.
  if (present.some((cell) => cell.toolMode === 'execute')) {
    aspects.push({ label: 'max turns', of: (cell) => String(cell.maxTurns) });
  }

  const drift = aspects
    .filter((aspect) => new Set(present.map(aspect.of)).size > 1)
    .map((aspect) => aspect.label);

  if (
    livePromptText !== undefined &&
    !drift.includes('prompt text') &&
    textKey(present[0].promptText) !== textKey(livePromptText)
  ) {
    drift.push('prompt edited since');
  }

  return drift;
}
