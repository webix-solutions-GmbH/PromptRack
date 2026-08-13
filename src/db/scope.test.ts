import { describe, expect, it } from 'vitest';
import { eq } from 'drizzle-orm';
import { runs } from './schema';
import {
  combine,
  requireCustomerId,
  resolveActiveCustomerId,
  scopeFromCustomerId,
  scopeValues,
  systemScope,
  whereScoped,
  type CustomerOption,
} from './scope';

const a = eq(runs.id, 1);
const b = eq(runs.status, 'completed');

describe('combine', () => {
  it('collapses an empty list to undefined', () => {
    expect(combine([])).toBeUndefined();
  });

  it('collapses a list of only undefined to undefined', () => {
    expect(combine([undefined, undefined])).toBeUndefined();
  });

  it('returns a single condition unwrapped', () => {
    expect(combine([a])).toBe(a);
    expect(combine([undefined, a, undefined])).toBe(a);
  });

  it('ands several conditions together', () => {
    expect(combine([a, b])).toBeDefined();
    expect(combine([a, b])).not.toBe(a);
  });
});

describe('whereScoped', () => {
  it('restricts a root table to the scope customer', () => {
    const where = whereScoped(scopeFromCustomerId(7), runs);
    expect(where).toBeDefined();
    // The predicate is built from the table's own `customer_id` column, which is
    // what makes it impossible to scope a query against the wrong table.
    expect(where?.queryChunks).toContain(runs.customerId);
  });

  it('ands the caller conditions onto the scope predicate', () => {
    const where = whereScoped(scopeFromCustomerId(7), runs, a);
    expect(where).toBeDefined();
    expect(where).not.toBe(a);
  });

  it('is a no-op under the system scope, which spans every workspace', () => {
    expect(whereScoped(systemScope('admin'), runs)).toBeUndefined();
    expect(whereScoped(systemScope('admin'), runs, a)).toBe(a);
  });
});

describe('scopes', () => {
  it('records where a scope came from', () => {
    expect(scopeFromCustomerId(1).origin).toBe('row');
    expect(systemScope('x').origin).toBe('system');
  });

  it('contributes the customer column to an insert', () => {
    expect(scopeValues(scopeFromCustomerId(3))).toEqual({ customerId: 3 });
  });

  it('refuses to insert under the system scope — a row needs one workspace', () => {
    expect(() => scopeValues(systemScope('backfill'))).toThrow(/system scope/);
    expect(() => requireCustomerId(systemScope('backfill'))).toThrow(/system scope/);
  });
});

describe('resolveActiveCustomerId', () => {
  const options = (...entries: [number, boolean][]): CustomerOption[] =>
    entries.map(([id, archived]) => ({ id, name: `w${id}`, archived }));

  it('keeps a preferred workspace that is live', () => {
    expect(resolveActiveCustomerId(2, options([1, false], [2, false]))).toBe(2);
  });

  it('falls back to the oldest live workspace when the preferred one is archived', () => {
    expect(resolveActiveCustomerId(2, options([1, false], [2, true]))).toBe(1);
  });

  it('falls back when the preferred workspace no longer exists', () => {
    expect(resolveActiveCustomerId(99, options([1, false], [2, false]))).toBe(1);
  });

  it('falls back when nothing is preferred', () => {
    expect(resolveActiveCustomerId(null, options([3, false], [4, false]))).toBe(3);
  });

  it('uses an archived workspace rather than leaving the app unusable', () => {
    expect(resolveActiveCustomerId(null, options([1, true]))).toBe(1);
    expect(resolveActiveCustomerId(1, options([1, true]))).toBe(1);
  });

  it('has nothing to resolve to when no workspace exists', () => {
    expect(resolveActiveCustomerId(null, [])).toBeNull();
    expect(resolveActiveCustomerId(5, [])).toBeNull();
  });
});
