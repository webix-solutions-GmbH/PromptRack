import { describe, expect, it } from 'vitest';
import { canAdminister, canWrite, parseRole, ROLES } from './policy';

describe('parseRole', () => {
  it('accepts every role it defines', () => {
    for (const role of ROLES) {
      expect(parseRole(role)).toBe(role);
    }
  });

  it('degrades anything unrecognised to viewer, never to admin', () => {
    expect(parseRole('')).toBe('viewer');
    expect(parseRole(null)).toBe('viewer');
    expect(parseRole(undefined)).toBe('viewer');
    expect(parseRole('owner')).toBe('viewer');
    // Case matters: a stored 'ADMIN' is not a role this app ever wrote.
    expect(parseRole('ADMIN')).toBe('viewer');
    expect(parseRole({ role: 'admin' })).toBe('viewer');
  });
});

describe('canWrite', () => {
  it('admits admins and members, refuses viewers', () => {
    expect(canWrite('admin')).toBe(true);
    expect(canWrite('member')).toBe(true);
    expect(canWrite('viewer')).toBe(false);
  });
});

describe('canAdminister', () => {
  it('admits admins only', () => {
    expect(canAdminister('admin')).toBe(true);
    expect(canAdminister('member')).toBe(false);
    expect(canAdminister('viewer')).toBe(false);
  });
});
