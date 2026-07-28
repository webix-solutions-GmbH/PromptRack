import { describe, expect, it } from 'vitest';
import {
  buildToolDefinitions,
  collectToolNameCollisions,
  emptyParameterSchema,
  formatToolArguments,
  normalizeMaxTurns,
  parseParameterSchema,
  parseToolArguments,
  parseToolsSnapshot,
  snapshotDefinitions,
  snapshotToolNames,
  validateParameterSchema,
  validateToolName,
  MAX_TURNS_LIMIT,
  DEFAULT_MAX_TURNS,
} from './tools';

describe('parseParameterSchema', () => {
  it('parses a JSON Schema object', () => {
    expect(parseParameterSchema('{"type":"object","properties":{"q":{"type":"string"}}}')).toEqual({
      type: 'object',
      properties: { q: { type: 'string' } },
    });
  });

  it('falls back to the no-argument schema for empty input', () => {
    expect(parseParameterSchema('')).toEqual(emptyParameterSchema());
    expect(parseParameterSchema('   ')).toEqual(emptyParameterSchema());
    expect(parseParameterSchema(null)).toEqual(emptyParameterSchema());
  });

  it('falls back rather than throwing on malformed JSON', () => {
    expect(parseParameterSchema('{oops')).toEqual(emptyParameterSchema());
  });

  it('rejects a JSON array — a schema must be an object', () => {
    expect(parseParameterSchema('[1,2,3]')).toEqual(emptyParameterSchema());
  });
});

describe('validateParameterSchema', () => {
  it('accepts empty input', () => {
    expect(validateParameterSchema('  ')).toBeNull();
  });

  it('accepts a JSON object', () => {
    expect(validateParameterSchema('{"type":"object"}')).toBeNull();
  });

  it('rejects malformed JSON', () => {
    expect(validateParameterSchema('{')).toBe('Parameters must be valid JSON.');
  });

  it('rejects a non-object', () => {
    expect(validateParameterSchema('"a string"')).toMatch(/JSON object/);
  });
});

describe('validateToolName', () => {
  it('accepts the OpenAI-legal character set', () => {
    expect(validateToolName('search_products-v2')).toBeNull();
  });

  it('rejects an empty name', () => {
    expect(validateToolName('   ')).toBe('Tool name is required.');
  });

  it('rejects spaces and dots', () => {
    expect(validateToolName('search products')).toMatch(/only contain/);
    expect(validateToolName('odoo.search')).toMatch(/only contain/);
  });

  it('rejects names longer than 64 characters', () => {
    expect(validateToolName('a'.repeat(65))).toMatch(/only contain/);
    expect(validateToolName('a'.repeat(64))).toBeNull();
  });
});

describe('buildToolDefinitions', () => {
  it('builds an OpenAI-shaped tools array', () => {
    expect(
      buildToolDefinitions([
        {
          name: 'search',
          description: 'Search the web',
          parametersJson: '{"type":"object","properties":{"q":{"type":"string"}}}',
        },
      ]),
    ).toEqual([
      {
        type: 'function',
        function: {
          name: 'search',
          description: 'Search the web',
          parameters: { type: 'object', properties: { q: { type: 'string' } } },
        },
      },
    ]);
  });

  it('omits an empty description rather than sending an empty string', () => {
    const [definition] = buildToolDefinitions([{ name: 'noop', description: '   ' }]);
    expect(definition.function).not.toHaveProperty('description');
  });

  it('skips disabled tools', () => {
    const definitions = buildToolDefinitions([
      { name: 'kept', enabled: true },
      { name: 'dropped', enabled: false },
    ]);
    expect(definitions.map((definition) => definition.function.name)).toEqual(['kept']);
  });

  it('defaults missing parameters to the no-argument schema', () => {
    const [definition] = buildToolDefinitions([{ name: 'ping' }]);
    expect(definition.function.parameters).toEqual(emptyParameterSchema());
  });
});

describe('collectToolNameCollisions', () => {
  it('returns nothing when every name is unique', () => {
    expect(collectToolNameCollisions([{ name: 'a' }, { name: 'b' }])).toEqual([]);
  });

  it('reports each duplicated name once, sorted', () => {
    expect(
      collectToolNameCollisions([
        { name: 'search' },
        { name: 'search' },
        { name: 'search' },
        { name: 'create' },
        { name: 'create' },
      ]),
    ).toEqual(['create', 'search']);
  });

  it('ignores disabled tools — they are never sent, so they cannot collide', () => {
    expect(
      collectToolNameCollisions([
        { name: 'search' },
        { name: 'search', enabled: false },
      ]),
    ).toEqual([]);
  });
});

describe('parseToolArguments', () => {
  it('parses an object', () => {
    expect(parseToolArguments('{"q":"laptops","limit":5}')).toEqual({
      ok: true,
      value: { q: 'laptops', limit: 5 },
    });
  });

  it('treats an empty string as a no-argument call', () => {
    expect(parseToolArguments('')).toEqual({ ok: true, value: {} });
    expect(parseToolArguments('   ')).toEqual({ ok: true, value: {} });
    expect(parseToolArguments(null)).toEqual({ ok: true, value: {} });
  });

  it('reports malformed JSON instead of throwing', () => {
    const result = parseToolArguments('{"q": "unterminated');
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.error).toMatch(/not valid JSON/);
  });

  it('rejects a non-object payload', () => {
    expect(parseToolArguments('[1,2]')).toEqual({
      ok: false,
      error: 'Arguments must be a JSON object.',
    });
  });
});

describe('formatToolArguments', () => {
  it('pretty-prints valid arguments', () => {
    expect(formatToolArguments('{"q":"x"}')).toBe('{\n  "q": "x"\n}');
  });

  it('shows malformed arguments verbatim so the failure is visible', () => {
    expect(formatToolArguments('  {"q": broken  ')).toBe('{"q": broken');
  });
});

describe('normalizeMaxTurns', () => {
  it('defaults when unset or not a number', () => {
    expect(normalizeMaxTurns(null)).toBe(DEFAULT_MAX_TURNS);
    expect(normalizeMaxTurns(undefined)).toBe(DEFAULT_MAX_TURNS);
    expect(normalizeMaxTurns(Number.NaN)).toBe(DEFAULT_MAX_TURNS);
  });

  it('clamps to at least one turn', () => {
    expect(normalizeMaxTurns(0)).toBe(1);
    expect(normalizeMaxTurns(-5)).toBe(1);
  });

  it('clamps to the hard limit', () => {
    expect(normalizeMaxTurns(1000)).toBe(MAX_TURNS_LIMIT);
  });

  it('floors fractional values', () => {
    expect(normalizeMaxTurns(3.9)).toBe(3);
  });
});

describe('parseToolsSnapshot', () => {
  const snapshot = [
    {
      definition: buildToolDefinitions([{ name: 'search', description: 'Search' }])[0],
      source: 'manual' as const,
      toolsetId: 1,
      toolsetName: 'Web',
      mockResponse: '{"hits":[]}',
    },
  ];

  it('round-trips a snapshot', () => {
    expect(parseToolsSnapshot(JSON.stringify(snapshot))).toEqual(snapshot);
  });

  it('returns an empty list for null, junk, or a non-array', () => {
    expect(parseToolsSnapshot(null)).toEqual([]);
    expect(parseToolsSnapshot('not json')).toEqual([]);
    expect(parseToolsSnapshot('{"definition":{"function":{"name":"x"}}}')).toEqual([]);
  });

  it('drops entries whose definition has no function name', () => {
    expect(
      parseToolsSnapshot(
        '[{"definition":{"type":"function"}},{"definition":{"function":{"name":"ok"}}}]',
      ),
    ).toEqual([{ definition: { function: { name: 'ok' } } }]);
  });

  it('exposes the wire definitions and the tool names', () => {
    expect(snapshotDefinitions(snapshot)).toEqual([snapshot[0].definition]);
    expect(snapshotToolNames(snapshot)).toEqual(['search']);
  });
});
