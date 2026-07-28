import { describe, expect, it } from 'vitest';
import {
  buildCompareMatrix,
  buildModelColumns,
  buildModelMatrix,
  describeRowDrift,
  modelColumnKey,
  parseCompareMode,
  parseModelColumnKeys,
  parseRunIds,
  serializeRunIds,
  splitModelColumnKey,
  type CompareCellView,
  type ComparePromptView,
  type ModelColumnResult,
  type ModelColumnRun,
  type ModelCompareCell,
} from './compare';

let nextId = 1;

function cell(overrides: Partial<CompareCellView> & { runId: number }): CompareCellView {
  return {
    id: nextId++,
    runCreatedAt: 1_000,
    promptId: null,
    sortOrder: 0,
    groupName: 'group',
    promptTitle: 'title',
    promptText: 'text',
    systemPromptText: null,
    toolsSnapshot: null,
    toolMode: 'none',
    toolChoice: null,
    maxTurns: 6,
    runParams: null,
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
    turnCount: null,
    toolCallCount: null,
    toolCallNames: [],
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

// ---------------------------------------------------------------------------
// Model mode
// ---------------------------------------------------------------------------

function modelCell(
  overrides: Partial<ModelCompareCell> & { runId: number; columnKey: string },
): ModelCompareCell {
  return { ...cell(overrides), columnKey: overrides.columnKey };
}

function prompt(id: number, overrides: Partial<ComparePromptView> = {}): ComparePromptView {
  return {
    id,
    groupId: 1,
    groupName: 'group',
    title: `prompt ${id}`,
    text: `text ${id}`,
    ...overrides,
  };
}

describe('parseCompareMode', () => {
  it('accepts the two known pivots and rejects anything else', () => {
    expect(parseCompareMode('runs')).toBe('runs');
    expect(parseCompareMode('models')).toBe('models');
    expect(parseCompareMode('nonsense')).toBeNull();
    expect(parseCompareMode(undefined)).toBeNull();
  });
});

describe('modelColumnKey', () => {
  it('round-trips machine id and model id', () => {
    expect(splitModelColumnKey(modelColumnKey(3, 'qwen3:32b'))).toEqual({
      machineId: 3,
      modelId: 'qwen3:32b',
    });
  });

  it('keeps a model id containing the separator-adjacent characters intact', () => {
    expect(splitModelColumnKey(modelColumnKey(7, 'Qwen/Qwen3-32B-AWQ'))?.modelId).toBe(
      'Qwen/Qwen3-32B-AWQ',
    );
  });

  it('maps a deleted machine to id 0 and back to null', () => {
    expect(modelColumnKey(null, 'm')).toBe('0|m');
    expect(splitModelColumnKey('0|m')).toEqual({ machineId: null, modelId: 'm' });
  });

  it('rejects malformed keys', () => {
    expect(splitModelColumnKey('nope')).toBeNull();
    expect(splitModelColumnKey('|m')).toBeNull();
    expect(splitModelColumnKey('1|')).toBeNull();
  });
});

describe('parseModelColumnKeys', () => {
  it('reads repeated params, de-duplicating and keeping order', () => {
    expect(parseModelColumnKeys(['2|b', '1|a', '2|b'])).toEqual(['2|b', '1|a']);
  });

  it('accepts a single value and drops junk', () => {
    expect(parseModelColumnKeys('1|a')).toEqual(['1|a']);
    expect(parseModelColumnKeys(['junk', '1|a'])).toEqual(['1|a']);
    expect(parseModelColumnKeys(undefined)).toEqual([]);
  });

  it('truncates to the maximum', () => {
    expect(parseModelColumnKeys(['1|a', '2|b', '3|c'], 2)).toEqual(['1|a', '2|b']);
  });
});

describe('buildModelColumns', () => {
  const run = (overrides: Partial<ModelColumnRun> & { id: number }): ModelColumnRun => ({
    machineId: 1,
    machineName: 'box',
    modelId: 'model-a',
    createdAt: 1_000,
    archived: false,
    ...overrides,
  });
  const result = (
    overrides: Partial<ModelColumnResult> & { runId: number },
  ): ModelColumnResult => ({
    promptId: 1,
    status: 'ok',
    rating: null,
    tokensPerSec: null,
    ...overrides,
  });

  it('groups runs of the same model and machine into one column', () => {
    const columns = buildModelColumns(
      [run({ id: 1, createdAt: 1 }), run({ id: 2, createdAt: 2 })],
      [result({ runId: 1 }), result({ runId: 2, promptId: 2 })],
    );

    expect(columns).toHaveLength(1);
    expect(columns[0].key).toBe('1|model-a');
    expect(columns[0].runCount).toBe(2);
    expect(columns[0].promptCount).toBe(2);
    expect(columns[0].latestRunAt).toBe(2);
  });

  it('keeps the same model on two machines apart', () => {
    const columns = buildModelColumns(
      [run({ id: 1, machineId: 1 }), run({ id: 2, machineId: 2, machineName: 'other' })],
      [result({ runId: 1 }), result({ runId: 2 })],
    );

    expect(columns.map((column) => column.key)).toEqual(['1|model-a', '2|model-a']);
  });

  it('ignores archived runs and runs without a usable result', () => {
    const columns = buildModelColumns(
      [
        run({ id: 1, archived: true }),
        run({ id: 2, modelId: 'model-b' }),
        run({ id: 3, modelId: 'model-c' }),
      ],
      [
        result({ runId: 1 }),
        result({ runId: 2, status: 'error' }),
        result({ runId: 3, status: 'ok' }),
      ],
    );

    expect(columns.map((column) => column.key)).toEqual(['1|model-c']);
  });

  it('averages speed and tallies ratings over usable results only', () => {
    const columns = buildModelColumns(
      [run({ id: 1 })],
      [
        result({ runId: 1, promptId: 1, rating: 'good', tokensPerSec: 10 }),
        result({ runId: 1, promptId: 2, rating: 'bad', tokensPerSec: 20 }),
        result({ runId: 1, promptId: 3, status: 'error', rating: 'bad', tokensPerSec: 999 }),
      ],
    );

    expect(columns[0].avgRate).toBe(15);
    expect([columns[0].good, columns[0].meh, columns[0].bad]).toEqual([1, 0, 1]);
  });

  it('names a column after its most recent run snapshot', () => {
    const columns = buildModelColumns(
      [
        run({ id: 1, createdAt: 1, machineName: 'old name' }),
        run({ id: 2, createdAt: 2, machineName: 'new name' }),
      ],
      [result({ runId: 1 }), result({ runId: 2 })],
    );

    expect(columns[0].machineName).toBe('new name');
  });
});

describe('buildModelMatrix', () => {
  it('fills each cell with the most recent result of that model', () => {
    const { rows } = buildModelMatrix(
      ['1|a', '1|b'],
      [prompt(10)],
      [
        modelCell({ runId: 1, columnKey: '1|a', promptId: 10, runCreatedAt: 1, responseText: 'old' }),
        modelCell({ runId: 2, columnKey: '1|a', promptId: 10, runCreatedAt: 2, responseText: 'new' }),
        modelCell({ runId: 3, columnKey: '1|b', promptId: 10, responseText: 'other model' }),
      ],
    );

    expect(rows).toHaveLength(1);
    expect(rows[0].cells[0]?.responseText).toBe('new');
    expect(rows[0].cells[1]?.responseText).toBe('other model');
  });

  it('falls back to the newest usable result and reports the skipped attempt', () => {
    const { rows } = buildModelMatrix(
      ['1|a'],
      [prompt(10)],
      [
        modelCell({ runId: 1, columnKey: '1|a', promptId: 10, runCreatedAt: 1, responseText: 'good one' }),
        modelCell({
          runId: 2,
          columnKey: '1|a',
          promptId: 10,
          runCreatedAt: 2,
          status: 'error',
          responseText: null,
          error: 'connection refused',
        }),
      ],
    );

    expect(rows[0].cells[0]?.responseText).toBe('good one');
    expect(rows[0].cells[0]?.superseded).toEqual({ runId: 2, status: 'error', createdAt: 2 });
  });

  it('leaves no superseded marker when the newest result is the one shown', () => {
    const { rows } = buildModelMatrix(
      ['1|a'],
      [prompt(10)],
      [modelCell({ runId: 1, columnKey: '1|a', promptId: 10 })],
    );

    expect(rows[0].cells[0]?.superseded).toBeNull();
  });

  it('keeps prompt order and counts prompts nobody answered', () => {
    const { rows, uncoveredPrompts } = buildModelMatrix(
      ['1|a'],
      [prompt(10), prompt(11), prompt(12)],
      [modelCell({ runId: 1, columnKey: '1|a', promptId: 12 })],
    );

    expect(rows.map((row) => row.promptId)).toEqual([12]);
    expect(uncoveredPrompts).toBe(2);
  });

  it('uses the live prompt text for the row, not the snapshot', () => {
    const { rows } = buildModelMatrix(
      ['1|a'],
      [prompt(10, { text: 'live version' })],
      [modelCell({ runId: 1, columnKey: '1|a', promptId: 10, promptText: 'old version' })],
    );

    expect(rows[0].promptText).toBe('live version');
    expect(rows[0].cells[0]?.promptText).toBe('old version');
  });

  it('ignores unselected columns, out-of-scope prompts and deleted prompts', () => {
    const { rows } = buildModelMatrix(
      ['1|a'],
      [prompt(10)],
      [
        modelCell({ runId: 1, columnKey: '9|z', promptId: 10 }),
        modelCell({ runId: 2, columnKey: '1|a', promptId: 99 }),
        modelCell({ runId: 3, columnKey: '1|a', promptId: null, promptText: 'text 10' }),
      ],
    );

    expect(rows).toEqual([]);
  });

  it('produces one cell slot per column, in the given order', () => {
    const { rows } = buildModelMatrix(
      ['1|a', '1|b', '1|c'],
      [prompt(10)],
      [modelCell({ runId: 7, columnKey: '1|c', promptId: 10 })],
    );

    expect(rows[0].cells.map((c) => c?.runId ?? null)).toEqual([null, null, 7]);
  });
});

describe('describeRowDrift', () => {
  it('reports nothing when the cells only differ by model', () => {
    expect(
      describeRowDrift([
        cell({ runId: 1, promptText: 'same', systemPromptText: 'sys' }),
        cell({ runId: 2, promptText: 'same', systemPromptText: 'sys' }),
      ]),
    ).toEqual([]);
  });

  it('names each condition that is not held constant', () => {
    const drift = describeRowDrift([
      cell({ runId: 1, promptText: 'a', systemPromptText: 'sys', runParams: '{"temperature":0}' }),
      cell({ runId: 2, promptText: 'b', systemPromptText: null, runParams: '{"temperature":1}' }),
    ]);

    expect(drift).toEqual(['prompt text', 'system prompt', 'params']);
  });

  it('ignores whitespace and json key order', () => {
    expect(
      describeRowDrift([
        cell({ runId: 1, promptText: 'hello  world', runParams: '{"a":1,"b":2}' }),
        cell({ runId: 2, promptText: ' hello world\n', runParams: '{"b":2, "a":1}' }),
      ]),
    ).toEqual([]);
  });

  it('reports tool differences, but max turns only when tools actually run', () => {
    expect(
      describeRowDrift([
        cell({ runId: 1, toolMode: 'none', maxTurns: 6 }),
        cell({ runId: 2, toolMode: 'none', maxTurns: 9 }),
      ]),
    ).toEqual([]);

    expect(
      describeRowDrift([
        cell({ runId: 1, toolMode: 'execute', maxTurns: 6, toolsSnapshot: '[{"a":1}]' }),
        cell({ runId: 2, toolMode: 'execute', maxTurns: 9, toolsSnapshot: '[{"a":2}]' }),
      ]),
    ).toEqual(['tools', 'max turns']);
  });

  it('flags a prompt edited after every compared run', () => {
    expect(
      describeRowDrift(
        [cell({ runId: 1, promptText: 'v1' }), cell({ runId: 2, promptText: 'v1' })],
        'v2',
      ),
    ).toEqual(['prompt edited since']);
  });

  it('does not flag an edit when the live text matches', () => {
    expect(describeRowDrift([cell({ runId: 1, promptText: 'v1' })], 'v1')).toEqual([]);
  });

  it('ignores empty columns', () => {
    expect(describeRowDrift([null, null])).toEqual([]);
    expect(describeRowDrift([null, cell({ runId: 1 })])).toEqual([]);
  });
});
