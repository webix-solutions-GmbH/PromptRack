import { describe, expect, it } from 'vitest';
import {
  buildCompareMatrix,
  parseRunIds,
  serializeRunIds,
  type CompareCellView,
} from './compare';

let nextId = 1;

function cell(overrides: Partial<CompareCellView> & { runId: number }): CompareCellView {
  return {
    id: nextId++,
    promptId: null,
    sortOrder: 0,
    groupName: 'group',
    promptTitle: 'title',
    promptText: 'text',
    status: 'ok',
    responseText: 'response',
    error: null,
    durationMs: 1000,
    ttftMs: 100,
    completionTokens: 10,
    tokensPerSec: 10,
    tokensEstimated: false,
    rating: null,
    ratingNote: null,
    ...overrides,
  };
}

describe('parseRunIds', () => {
  it('parses a comma separated list', () => {
    expect(parseRunIds('1,5,7')).toEqual([1, 5, 7]);
  });

  it('returns an empty list for undefined or empty input', () => {
    expect(parseRunIds(undefined)).toEqual([]);
    expect(parseRunIds('')).toEqual([]);
  });

  it('skips junk, zero, and negative ids', () => {
    expect(parseRunIds('1,abc,,0,-3, 4 ')).toEqual([1, 4]);
  });

  it('de-duplicates while keeping the given order', () => {
    expect(parseRunIds('3,1,3,1')).toEqual([3, 1]);
  });

  it('truncates to the maximum', () => {
    expect(parseRunIds('1,2,3,4,5,6')).toEqual([1, 2, 3, 4]);
    expect(parseRunIds('1,2,3', 2)).toEqual([1, 2]);
  });

  it('joins repeated search params', () => {
    expect(parseRunIds(['1', '2'])).toEqual([1, 2]);
  });

  it('round-trips through serializeRunIds', () => {
    expect(parseRunIds(serializeRunIds([2, 9]))).toEqual([2, 9]);
  });
});

describe('buildCompareMatrix', () => {
  it('matches rows across runs by prompt id', () => {
    const rows = buildCompareMatrix(
      [1, 2],
      [
        cell({ runId: 1, promptId: 10, sortOrder: 0, promptTitle: 'A' }),
        cell({ runId: 1, promptId: 11, sortOrder: 1, promptTitle: 'B' }),
        cell({ runId: 2, promptId: 11, sortOrder: 0, promptTitle: 'B' }),
        cell({ runId: 2, promptId: 10, sortOrder: 1, promptTitle: 'A' }),
      ],
    );

    expect(rows).toHaveLength(2);
    expect(rows.map((row) => row.promptTitle)).toEqual(['A', 'B']);
    expect(rows.every((row) => row.cells.every((c) => c !== null))).toBe(true);
  });

  it('leaves an empty cell for prompts missing from a run', () => {
    const rows = buildCompareMatrix(
      [1, 2],
      [
        cell({ runId: 1, promptId: 10, sortOrder: 0, promptTitle: 'shared' }),
        cell({ runId: 1, promptId: 12, sortOrder: 1, promptTitle: 'only in A' }),
        cell({ runId: 2, promptId: 10, sortOrder: 0, promptTitle: 'shared' }),
        cell({ runId: 2, promptId: 13, sortOrder: 1, promptTitle: 'only in B' }),
      ],
    );

    expect(rows.map((row) => row.promptTitle)).toEqual([
      'shared',
      'only in A',
      'only in B',
    ]);
    expect(rows[1].cells[1]).toBeNull();
    expect(rows[2].cells[0]).toBeNull();
  });

  it('matches a deleted prompt (null id) against identical prompt text', () => {
    const rows = buildCompareMatrix(
      [1, 2],
      [
        cell({ runId: 1, promptId: null, promptText: 'What is 2+2?', promptTitle: 'Math' }),
        cell({ runId: 2, promptId: 42, promptText: 'What is 2+2?', promptTitle: 'Math' }),
      ],
    );

    expect(rows).toHaveLength(1);
    expect(rows[0].cells[0]).not.toBeNull();
    expect(rows[0].cells[1]).not.toBeNull();
  });

  it('matches in the other direction too (id first, null second)', () => {
    const rows = buildCompareMatrix(
      [1, 2],
      [
        cell({ runId: 1, promptId: 42, promptText: 'What is 2+2?' }),
        cell({ runId: 2, promptId: null, promptText: 'What is 2+2?' }),
      ],
    );

    expect(rows).toHaveLength(1);
    expect(rows[0].cells.filter(Boolean)).toHaveLength(2);
  });

  it('ignores insignificant whitespace when matching by text', () => {
    const rows = buildCompareMatrix(
      [1, 2],
      [
        cell({ runId: 1, promptId: null, promptText: 'hello   world' }),
        cell({ runId: 2, promptId: null, promptText: ' hello world\n' }),
      ],
    );

    expect(rows).toHaveLength(1);
  });

  it('keeps different prompt ids apart even with identical text', () => {
    const rows = buildCompareMatrix(
      [1, 2],
      [
        cell({ runId: 1, promptId: 1, promptText: 'same' }),
        cell({ runId: 2, promptId: 2, promptText: 'same' }),
      ],
    );

    expect(rows).toHaveLength(2);
  });

  it('produces one cell slot per selected run, in column order', () => {
    const rows = buildCompareMatrix(
      [3, 1, 2],
      [cell({ runId: 2, promptId: 10 }), cell({ runId: 3, promptId: 10 })],
    );

    expect(rows[0].cells).toHaveLength(3);
    expect(rows[0].cells[0]?.runId).toBe(3);
    expect(rows[0].cells[1]).toBeNull();
    expect(rows[0].cells[2]?.runId).toBe(2);
  });

  it('drops results belonging to runs that are not selected', () => {
    const rows = buildCompareMatrix(
      [1],
      [cell({ runId: 1, promptId: 10 }), cell({ runId: 99, promptId: 11 })],
    );

    expect(rows).toHaveLength(1);
    expect(rows[0].cells).toHaveLength(1);
  });

  it('keeps the first result when a run maps two results onto one row', () => {
    const first = cell({ runId: 1, promptId: 10, sortOrder: 0, responseText: 'first' });
    const second = cell({ runId: 1, promptId: 10, sortOrder: 1, responseText: 'second' });
    const rows = buildCompareMatrix([1], [second, first]);

    expect(rows).toHaveLength(1);
    expect(rows[0].cells[0]?.responseText).toBe('first');
  });

  it('returns no rows when there are no results', () => {
    expect(buildCompareMatrix([1, 2], [])).toEqual([]);
  });
});
