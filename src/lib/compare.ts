/**
 * Pure helpers for the compare matrix.
 *
 * Kept free of server-only imports so both the page (server) and the picker
 * (client) can share the selection parsing, and so the row-matching logic is
 * unit-testable.
 */

import type { RunResultStatus } from './run-events';

/** Hard cap on how many runs can be compared side by side. */
export const MAX_COMPARE_RUNS = 4;
/** Minimum selection before a matrix can be rendered. */
export const MIN_COMPARE_RUNS = 2;

/** One selectable run, as shown in the picker and in the column headers. */
export interface CompareRunView {
  id: number;
  modelId: string;
  machineName: string;
  status: string;
  createdAt: number;
  groupNames: string[];
  good: number;
  bad: number;
  ok: number;
  error: number;
  avgRate: number | null;
}

/** One cell of the matrix: a single `run_results` row. */
export interface CompareCellView {
  id: number;
  runId: number;
  promptId: number | null;
  sortOrder: number;
  groupName: string;
  promptTitle: string;
  promptText: string;
  status: RunResultStatus;
  responseText: string | null;
  error: string | null;
  durationMs: number | null;
  ttftMs: number | null;
  completionTokens: number | null;
  tokensPerSec: number | null;
  tokensEstimated: boolean;
  rating: 'good' | 'bad' | null;
  ratingNote: string | null;
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
    const key = textKey(result.promptText);

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
