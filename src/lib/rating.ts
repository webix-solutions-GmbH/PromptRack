/**
 * The manual rating vocabulary, in one place.
 *
 * `meh` is the middle verdict: the response is not wrong, but it is not one you
 * would ship — often a sign the prompt needs work rather than the model. Named
 * `meh` rather than `ok` deliberately: `ok` already means "completed without an
 * error" as a `run_results.status`, and one word meaning two things in the same
 * table invites exactly the confusion this rating exists to remove.
 *
 * Kept free of server-only imports so both the pages and the client rating
 * widget can share it.
 */

export type Rating = 'good' | 'meh' | 'bad';

/** Best-to-worst — the order buttons, columns and legends should follow. */
export const RATINGS: readonly Rating[] = ['good', 'meh', 'bad'];

export function isRating(value: unknown): value is Rating {
  return value === 'good' || value === 'meh' || value === 'bad';
}

/** Narrows a raw database/form value; anything unexpected reads as unrated. */
export function parseRating(value: unknown): Rating | null {
  return isRating(value) ? value : null;
}

export interface RatingMeta {
  /** Short label used in badges. */
  label: string;
  emoji: string;
  /** Tooltip / button aria-label. */
  description: string;
  /** Badge classes. */
  badge: string;
  /** Toggle-button classes, selected and unselected. */
  button: { active: string; inactive: string };
  /** Text colour for bare counts in tables and summaries. */
  text: string;
}

export const RATING_META: Record<Rating, RatingMeta> = {
  good: {
    label: 'good',
    emoji: '👍',
    description: 'Good — would ship this',
    badge: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-400',
    button: {
      active:
        'border-emerald-600 bg-emerald-600 text-white dark:border-emerald-500 dark:bg-emerald-500',
      inactive:
        'border-zinc-300 text-zinc-500 hover:border-emerald-400 hover:text-emerald-600 dark:border-zinc-700 dark:text-zinc-400 dark:hover:border-emerald-500 dark:hover:text-emerald-400',
    },
    text: 'text-emerald-600 dark:text-emerald-400',
  },
  meh: {
    label: 'meh',
    emoji: '😐',
    description: 'Meh — not wrong, but not good enough',
    badge: 'bg-amber-100 text-amber-700 dark:bg-amber-950 dark:text-amber-400',
    button: {
      active: 'border-amber-500 bg-amber-500 text-white dark:border-amber-500 dark:bg-amber-500',
      inactive:
        'border-zinc-300 text-zinc-500 hover:border-amber-400 hover:text-amber-600 dark:border-zinc-700 dark:text-zinc-400 dark:hover:border-amber-500 dark:hover:text-amber-400',
    },
    text: 'text-amber-600 dark:text-amber-400',
  },
  bad: {
    label: 'bad',
    emoji: '👎',
    description: 'Bad — wrong or unusable',
    badge: 'bg-red-100 text-red-700 dark:bg-red-950 dark:text-red-400',
    button: {
      active: 'border-red-600 bg-red-600 text-white dark:border-red-500 dark:bg-red-500',
      inactive:
        'border-zinc-300 text-zinc-500 hover:border-red-400 hover:text-red-600 dark:border-zinc-700 dark:text-zinc-400 dark:hover:border-red-500 dark:hover:text-red-400',
    },
    text: 'text-red-600 dark:text-red-400',
  },
};

/** Per-rating tallies over a set of results, plus the unrated remainder. */
export interface RatingCounts {
  good: number;
  meh: number;
  bad: number;
  unrated: number;
}

export function countRatings(values: readonly (string | null)[]): RatingCounts {
  const counts: RatingCounts = { good: 0, meh: 0, bad: 0, unrated: 0 };
  for (const value of values) {
    const rating = parseRating(value);
    if (rating === null) counts.unrated += 1;
    else counts[rating] += 1;
  }
  return counts;
}

/**
 * Single number for sorting a run's results best-to-worst.
 *
 * `meh` contributes nothing on purpose: it sits exactly between a good and a
 * bad, so a run of all-meh scores the same as an unrated one — which is the
 * honest reading of "nothing here stood out either way".
 */
export function ratingScore(counts: RatingCounts): number {
  return counts.good - counts.bad;
}
