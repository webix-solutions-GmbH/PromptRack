import { describe, expect, it } from 'vitest';
import { resolveEffectiveSystemPrompt } from './system-prompt';

describe('resolveEffectiveSystemPrompt', () => {
  describe('mode: override', () => {
    it('returns the custom text when present', () => {
      expect(
        resolveEffectiveSystemPrompt({
          mode: 'override',
          baseContent: 'base content',
          customText: 'custom override',
        }),
      ).toBe('custom override');
    });

    it('ignores base content entirely', () => {
      expect(
        resolveEffectiveSystemPrompt({
          mode: 'override',
          baseContent: 'this should never appear',
          customText: 'only this',
        }),
      ).toBe('only this');
    });

    it('returns null when custom text is absent (null)', () => {
      expect(
        resolveEffectiveSystemPrompt({
          mode: 'override',
          baseContent: 'base content',
          customText: null,
        }),
      ).toBeNull();
    });

    it('returns null when custom text is whitespace-only', () => {
      expect(
        resolveEffectiveSystemPrompt({
          mode: 'override',
          baseContent: 'base content',
          customText: '   \n\t  ',
        }),
      ).toBeNull();
    });

    it('returns null when both base and custom are absent', () => {
      expect(
        resolveEffectiveSystemPrompt({
          mode: 'override',
          baseContent: null,
          customText: null,
        }),
      ).toBeNull();
    });
  });

  describe('mode: append', () => {
    it('joins base and custom with a blank line when both are present', () => {
      expect(
        resolveEffectiveSystemPrompt({
          mode: 'append',
          baseContent: 'You are a helpful assistant.',
          customText: 'Always answer in French.',
        }),
      ).toBe('You are a helpful assistant.\n\nAlways answer in French.');
    });

    it('returns only base content when custom text is absent (null)', () => {
      expect(
        resolveEffectiveSystemPrompt({
          mode: 'append',
          baseContent: 'You are a helpful assistant.',
          customText: null,
        }),
      ).toBe('You are a helpful assistant.');
    });

    it('returns only custom text when base content is absent (null)', () => {
      expect(
        resolveEffectiveSystemPrompt({
          mode: 'append',
          baseContent: null,
          customText: 'Always answer in French.',
        }),
      ).toBe('Always answer in French.');
    });

    it('returns null when neither base nor custom is present', () => {
      expect(
        resolveEffectiveSystemPrompt({
          mode: 'append',
          baseContent: null,
          customText: null,
        }),
      ).toBeNull();
    });

    it('treats whitespace-only base content as absent', () => {
      expect(
        resolveEffectiveSystemPrompt({
          mode: 'append',
          baseContent: '   ',
          customText: 'Always answer in French.',
        }),
      ).toBe('Always answer in French.');
    });

    it('treats whitespace-only custom text as absent', () => {
      expect(
        resolveEffectiveSystemPrompt({
          mode: 'append',
          baseContent: 'You are a helpful assistant.',
          customText: '\n\n  ',
        }),
      ).toBe('You are a helpful assistant.');
    });

    it('returns null when both base and custom are whitespace-only', () => {
      expect(
        resolveEffectiveSystemPrompt({
          mode: 'append',
          baseContent: '  ',
          customText: '\t',
        }),
      ).toBeNull();
    });

    it('trims surrounding whitespace from each part before joining', () => {
      expect(
        resolveEffectiveSystemPrompt({
          mode: 'append',
          baseContent: '  base  ',
          customText: '  custom  ',
        }),
      ).toBe('base\n\ncustom');
    });
  });
});
