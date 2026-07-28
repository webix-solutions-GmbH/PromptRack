import { describe, expect, it } from 'vitest';
import {
  countRatings,
  isRating,
  parseRating,
  ratingScore,
  RATINGS,
  RATING_META,
} from './rating';

describe('parseRating / isRating', () => {
  it('accepts the three ratings', () => {
    expect(parseRating('good')).toBe('good');
    expect(parseRating('meh')).toBe('meh');
    expect(parseRating('bad')).toBe('bad');
  });

  it('treats null, empty and unknown values as unrated', () => {
    expect(parseRating(null)).toBeNull();
    expect(parseRating(undefined)).toBeNull();
    expect(parseRating('')).toBeNull();
    expect(parseRating('okay')).toBeNull();
    expect(parseRating(1)).toBeNull();
  });

  it('does not confuse the result status "ok" with a rating', () => {
    // `ok` is a run_results.status, which is exactly why the middle rating is
    // called `meh` and not `ok`.
    expect(isRating('ok')).toBe(false);
  });
});

describe('RATINGS / RATING_META', () => {
  it('is ordered best to worst', () => {
    expect([...RATINGS]).toEqual(['good', 'meh', 'bad']);
  });

  it('describes every rating', () => {
    for (const rating of RATINGS) {
      const meta = RATING_META[rating];
      expect(meta.label.length).toBeGreaterThan(0);
      expect(meta.emoji.length).toBeGreaterThan(0);
      expect(meta.badge).toContain('dark:');
      expect(meta.button.active).not.toBe(meta.button.inactive);
    }
  });
});

describe('countRatings', () => {
  it('tallies each rating and the unrated remainder', () => {
    expect(countRatings(['good', 'good', 'meh', 'bad', null, null, null])).toEqual({
      good: 2,
      meh: 1,
      bad: 1,
      unrated: 3,
    });
  });

  it('returns zeroes for an empty list', () => {
    expect(countRatings([])).toEqual({ good: 0, meh: 0, bad: 0, unrated: 0 });
  });

  it('counts an unrecognised stored value as unrated rather than dropping it', () => {
    // Guards the total: every result must land in exactly one bucket.
    const counts = countRatings(['good', 'legacy-value', null]);
    expect(counts).toEqual({ good: 1, meh: 0, bad: 0, unrated: 2 });
    expect(counts.good + counts.meh + counts.bad + counts.unrated).toBe(3);
  });
});

describe('ratingScore', () => {
  it('is good minus bad', () => {
    expect(ratingScore({ good: 5, meh: 0, bad: 2, unrated: 0 })).toBe(3);
  });

  it('treats meh as neutral, so it sits between good and bad', () => {
    const allMeh = ratingScore({ good: 0, meh: 8, bad: 0, unrated: 0 });
    const allGood = ratingScore({ good: 8, meh: 0, bad: 0, unrated: 0 });
    const allBad = ratingScore({ good: 0, meh: 0, bad: 8, unrated: 0 });

    expect(allMeh).toBe(0);
    expect(allGood).toBeGreaterThan(allMeh);
    expect(allMeh).toBeGreaterThan(allBad);
  });

  it('scores an all-meh run the same as an unrated one', () => {
    expect(ratingScore({ good: 0, meh: 4, bad: 0, unrated: 0 })).toBe(
      ratingScore({ good: 0, meh: 0, bad: 0, unrated: 4 }),
    );
  });
});
