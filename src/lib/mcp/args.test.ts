import { describe, expect, it } from 'vitest';
import {
  McpToolError,
  hasKey,
  optionalBoolean,
  optionalEnum,
  optionalInteger,
  optionalRowRef,
  optionalRowRefList,
  optionalString,
  optionalText,
  parseRowRef,
  requireInteger,
  requireRowRef,
  requireString,
  requireText,
  resolveRowRef,
  toolArgs,
  truncate,
} from './args';

describe('toolArgs', () => {
  it('treats a missing or non-object arguments field as empty', () => {
    expect(toolArgs(undefined)).toEqual({});
    expect(toolArgs(null)).toEqual({});
    expect(toolArgs('nope')).toEqual({});
    expect(toolArgs([1, 2])).toEqual({});
  });
});

describe('string arguments', () => {
  it('trims and treats whitespace as absent', () => {
    expect(optionalString({ a: '  x  ' }, 'a')).toBe('x');
    expect(optionalString({ a: '   ' }, 'a')).toBeNull();
    expect(optionalString({}, 'a')).toBeNull();
  });

  it('rejects the wrong type instead of coercing', () => {
    expect(() => optionalString({ a: 5 }, 'a')).toThrow(McpToolError);
  });

  it('requireString names the missing field', () => {
    expect(() => requireString({}, 'title')).toThrow('"title" is required.');
  });

  it('requireText keeps the author\'s own whitespace', () => {
    expect(requireText({ a: '  line\n\n  more  ' }, 'a')).toBe('  line\n\n  more  ');
    expect(() => requireText({ a: '\n \t ' }, 'a')).toThrow(McpToolError);
  });

  it('optionalText is null only when there is nothing but whitespace', () => {
    expect(optionalText({ a: '\n' }, 'a')).toBeNull();
    expect(optionalText({ a: ' x\n' }, 'a')).toBe(' x\n');
  });
});

describe('number arguments', () => {
  it('accepts numbers written as strings', () => {
    expect(optionalInteger({ n: '12' }, 'n')).toBe(12);
    expect(optionalInteger({ n: 12 }, 'n')).toBe(12);
  });

  it('rejects fractions and junk', () => {
    expect(() => optionalInteger({ n: 1.5 }, 'n')).toThrow(McpToolError);
    expect(() => optionalInteger({ n: 'abc' }, 'n')).toThrow(McpToolError);
  });

  it('distinguishes absent from zero', () => {
    expect(optionalInteger({}, 'n')).toBeNull();
    expect(optionalInteger({ n: 0 }, 'n')).toBe(0);
    expect(() => requireInteger({}, 'n')).toThrow('"n" is required.');
  });
});

describe('optionalBoolean', () => {
  it('falls back when absent and accepts stringified booleans', () => {
    expect(optionalBoolean({}, 'b', true)).toBe(true);
    expect(optionalBoolean({ b: false }, 'b', true)).toBe(false);
    expect(optionalBoolean({ b: 'false' }, 'b', true)).toBe(false);
    expect(() => optionalBoolean({ b: 'maybe' }, 'b', true)).toThrow(McpToolError);
  });
});

describe('optionalEnum', () => {
  it('lists the allowed values in the error', () => {
    expect(optionalEnum({ m: 'execute' }, 'm', ['none', 'execute'] as const)).toBe('execute');
    expect(optionalEnum({}, 'm', ['none'] as const)).toBeNull();
    expect(() => optionalEnum({ m: 'run' }, 'm', ['none', 'execute'] as const)).toThrow(
      '"m" must be one of: none, execute.',
    );
  });
});

describe('parseRowRef', () => {
  it('reads a number as an id and a word as a name', () => {
    expect(parseRowRef(7, 'group')).toEqual({ kind: 'id', id: 7 });
    expect(parseRowRef(' Helpdesk ', 'group')).toEqual({ kind: 'name', name: 'Helpdesk' });
  });

  it('treats a numeric string as an id, not a name', () => {
    expect(parseRowRef('12', 'group')).toEqual({ kind: 'id', id: 12 });
  });

  it('rejects empty and non-scalar refs', () => {
    expect(() => parseRowRef('   ', 'group')).toThrow(McpToolError);
    expect(() => parseRowRef({}, 'group')).toThrow(McpToolError);
    expect(() => parseRowRef(1.5, 'group')).toThrow(McpToolError);
  });

  it('distinguishes an absent ref from an explicit null', () => {
    expect(optionalRowRef({}, 'group')).toBeNull();
    expect(optionalRowRef({ group: null }, 'group')).toBeNull();
    expect(hasKey({ group: null }, 'group')).toBe(true);
    expect(hasKey({}, 'group')).toBe(false);
    expect(() => requireRowRef({ group: null }, 'group')).toThrow('"group" is required.');
  });
});

describe('optionalRowRefList', () => {
  it('accepts a single value as a one-element list', () => {
    expect(optionalRowRefList({ toolsets: 'Demo' }, 'toolsets')).toEqual([
      { kind: 'name', name: 'Demo' },
    ]);
  });

  it('keeps an explicit empty list distinct from an absent one', () => {
    expect(optionalRowRefList({ toolsets: [] }, 'toolsets')).toEqual([]);
    expect(optionalRowRefList({}, 'toolsets')).toBeNull();
  });
});

describe('resolveRowRef', () => {
  const rows = [
    { id: 1, name: 'Helpdesk' },
    { id: 2, name: 'helpdesk' },
    { id: 3, name: 'Sales' },
  ];

  it('matches an id exactly', () => {
    expect(resolveRowRef({ kind: 'id', id: 3 }, rows, 'group').name).toBe('Sales');
    expect(() => resolveRowRef({ kind: 'id', id: 9 }, rows, 'group')).toThrow(
      'No group with id 9.',
    );
  });

  it('matches a name case-insensitively', () => {
    expect(resolveRowRef({ kind: 'name', name: 'sales' }, rows, 'group').id).toBe(3);
  });

  it('refuses an ambiguous name rather than guessing', () => {
    expect(() => resolveRowRef({ kind: 'name', name: 'HELPDESK' }, rows, 'group')).toThrow(
      /ids 1, 2/,
    );
  });

  it('lists what exists when a name misses', () => {
    expect(() => resolveRowRef({ kind: 'name', name: 'Nope' }, rows, 'group')).toThrow(
      /Known: Helpdesk \(1\)/,
    );
  });
});

describe('truncate', () => {
  it('marks that it cut and leaves short text alone', () => {
    expect(truncate('abcdef', 3)).toEqual({ text: 'abc…', truncated: true });
    expect(truncate('abc', 3)).toEqual({ text: 'abc', truncated: false });
    expect(truncate('abc', 0)).toEqual({ text: 'abc', truncated: false });
    expect(truncate(null, 3)).toEqual({ text: null, truncated: false });
  });
});
