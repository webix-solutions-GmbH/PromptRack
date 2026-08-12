import { describe, expect, it } from 'vitest';
import { optionalId, optionalNumber, optionalString, requiredString } from './form-data';

function fd(entries: Record<string, string>): FormData {
  const data = new FormData();
  for (const [key, value] of Object.entries(entries)) data.append(key, value);
  return data;
}

describe('optionalString', () => {
  it('is null when the key is absent', () => {
    expect(optionalString(fd({}), 'name')).toBeNull();
  });

  it('is null for a blank or whitespace-only value', () => {
    expect(optionalString(fd({ name: '' }), 'name')).toBeNull();
    expect(optionalString(fd({ name: '   \n\t ' }), 'name')).toBeNull();
  });

  it('trims what it returns', () => {
    expect(optionalString(fd({ name: '  ki01  ' }), 'name')).toBe('ki01');
  });
});

describe('requiredString', () => {
  it('trims what it returns', () => {
    expect(requiredString(fd({ name: '  ki01  ' }), 'name')).toBe('ki01');
  });

  it('names the key it was missing', () => {
    expect(() => requiredString(fd({}), 'content')).toThrow('content is required.');
    expect(() => requiredString(fd({ content: '  ' }), 'content')).toThrow('content is required.');
  });
});

describe('optionalId', () => {
  it('is null when absent or blank', () => {
    expect(optionalId(fd({}), 'groupId')).toBeNull();
    expect(optionalId(fd({ groupId: '' }), 'groupId')).toBeNull();
  });

  it('reads an integer', () => {
    expect(optionalId(fd({ groupId: ' 42 ' }), 'groupId')).toBe(42);
  });

  it('is null for a non-integer rather than throwing', () => {
    expect(optionalId(fd({ groupId: '1.5' }), 'groupId')).toBeNull();
    expect(optionalId(fd({ groupId: 'abc' }), 'groupId')).toBeNull();
  });
});

describe('optionalNumber', () => {
  it('is null when absent or blank', () => {
    expect(optionalNumber(fd({}), 'temperature', 'Temperature')).toBeNull();
    expect(optionalNumber(fd({ temperature: '  ' }), 'temperature', 'Temperature')).toBeNull();
  });

  it('accepts a fractional value', () => {
    expect(optionalNumber(fd({ temperature: '0.25' }), 'temperature', 'Temperature')).toBe(0.25);
  });

  it('reports the label, not the key', () => {
    expect(() => optionalNumber(fd({ temperature: 'hot' }), 'temperature', 'Temperature')).toThrow(
      'Temperature must be a number.',
    );
  });
});
