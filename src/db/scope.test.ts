import { describe, expect, it } from 'vitest';
import { eq } from 'drizzle-orm';
import { runs } from './schema';
import { combine, currentScope, scopeValues, systemScope, whereScoped } from './scope';

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
  it('is a no-op in this phase — one implicit workspace, no customer_id column', async () => {
    const scope = await currentScope();
    expect(whereScoped(scope, runs)).toBeUndefined();
  });

  it('passes the caller conditions through unchanged while the scope is a no-op', async () => {
    const scope = await currentScope();
    expect(whereScoped(scope, runs, a)).toBe(a);
    expect(whereScoped(scope, runs, a, b)).toBeDefined();
  });
});

describe('scopes', () => {
  it('hands out the same implicit scope on every call', async () => {
    expect(await currentScope()).toBe(await currentScope());
  });

  it('records where a scope came from', async () => {
    expect((await currentScope()).origin).toBe('session');
    expect(systemScope('x').origin).toBe('system');
  });

  it('contributes no columns to an insert in this phase', async () => {
    expect(Object.keys(scopeValues(await currentScope()))).toHaveLength(0);
  });
});
