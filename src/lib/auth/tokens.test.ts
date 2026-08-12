import { describe, expect, it } from 'vitest';
import { generateToken, hashToken, tokenDisplayPrefix, TOKEN_PREFIX } from './tokens';

describe('generateToken', () => {
  it('is recognisable and long enough to be a secret', () => {
    const token = generateToken();
    expect(token.startsWith(TOKEN_PREFIX)).toBe(true);
    expect(token.length).toBeGreaterThanOrEqual(40);
  });

  it('never repeats', () => {
    expect(generateToken()).not.toBe(generateToken());
  });
});

describe('hashToken', () => {
  it('is 64 hex characters and stable for the same input', () => {
    const token = generateToken();
    expect(hashToken(token)).toMatch(/^[0-9a-f]{64}$/);
    expect(hashToken(token)).toBe(hashToken(token));
  });

  it('ignores surrounding whitespace, which a copied header carries', () => {
    const token = generateToken();
    expect(hashToken(`  ${token}\n`)).toBe(hashToken(token));
  });

  it('differs for differing input', () => {
    expect(hashToken('amv_one')).not.toBe(hashToken('amv_two'));
  });
});

describe('tokenDisplayPrefix', () => {
  it('keeps only the first 12 characters, so a list can name a token', () => {
    const token = generateToken();
    const prefix = tokenDisplayPrefix(token);
    expect(prefix).toHaveLength(12);
    expect(token.startsWith(prefix)).toBe(true);
  });
});
