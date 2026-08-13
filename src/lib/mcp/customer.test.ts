import { describe, expect, it } from 'vitest';
import { McpToolError, parseRowRef } from './args';
import {
  CUSTOMER_HEADER,
  pickCustomerRef,
  resolveCustomerRef,
  scopeSourceFromHeaders,
  type McpScopeSource,
} from './customer';

const NONE: McpScopeSource = { header: null, tokenDefault: null };

const WORKSPACES = [
  { id: 1, name: 'Default' },
  { id: 2, name: 'Acme GmbH' },
];

describe('pickCustomerRef', () => {
  it('prefers an explicit argument over the header', () => {
    const source: McpScopeSource = { header: { kind: 'name', name: 'Default' }, tokenDefault: 9 };
    expect(pickCustomerRef({ customer: 'Acme GmbH' }, source)).toEqual({
      kind: 'name',
      name: 'Acme GmbH',
    });
  });

  it('prefers the header over the token default', () => {
    const source: McpScopeSource = { header: { kind: 'name', name: 'Default' }, tokenDefault: 9 };
    expect(pickCustomerRef({}, source)).toEqual({ kind: 'name', name: 'Default' });
  });

  it('falls back to the token default', () => {
    expect(pickCustomerRef({}, { header: null, tokenDefault: 9 })).toEqual({ kind: 'id', id: 9 });
  });

  it('has nothing to pick when none of the three is present', () => {
    expect(pickCustomerRef({}, NONE)).toBeNull();
  });

  it('treats a numeric string argument as an id, like every other ref', () => {
    expect(pickCustomerRef({ customer: '2' }, NONE)).toEqual({ kind: 'id', id: 2 });
  });
});

describe('scopeSourceFromHeaders', () => {
  it('parses the header as a row ref', () => {
    const source = scopeSourceFromHeaders(new Headers({ [CUSTOMER_HEADER]: 'Acme GmbH' }));
    expect(source.header).toEqual({ kind: 'name', name: 'Acme GmbH' });
  });

  it('treats a blank header as absent rather than as an empty name', () => {
    expect(scopeSourceFromHeaders(new Headers({ [CUSTOMER_HEADER]: '   ' })).header).toBeNull();
    expect(scopeSourceFromHeaders(new Headers()).header).toBeNull();
  });

  it('carries no token default yet', () => {
    expect(scopeSourceFromHeaders(new Headers()).tokenDefault).toBeNull();
  });
});

describe('resolveCustomerRef', () => {
  it('resolves a name case-insensitively', () => {
    expect(resolveCustomerRef(parseRowRef('acme gmbh', 'x'), WORKSPACES)).toBe(2);
  });

  it('resolves an id', () => {
    expect(resolveCustomerRef({ kind: 'id', id: 1 }, WORKSPACES)).toBe(1);
  });

  it('lists the known workspaces when the name is unknown', () => {
    expect(() => resolveCustomerRef({ kind: 'name', name: 'Nope' }, WORKSPACES)).toThrow(
      McpToolError,
    );
    expect(() => resolveCustomerRef({ kind: 'name', name: 'Nope' }, WORKSPACES)).toThrow(
      /Acme GmbH \(2\)/,
    );
  });

  it('refuses a call that names no workspace, and says how to name one', () => {
    let message = '';
    try {
      resolveCustomerRef(null, WORKSPACES);
    } catch (err) {
      message = err instanceof Error ? err.message : '';
    }
    expect(message).toContain('"customer"');
    expect(message).toContain(CUSTOMER_HEADER);
    expect(message).toContain('Default (1)');
  });
});
