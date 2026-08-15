// The manual rating vocabulary, in one place. Port of
// `git show legacy-nextjs:src/lib/rating.ts` — the wire values are unchanged
// (`good` / `meh` / `bad`), only the styling moved from Tailwind class
// strings to the small severity/emoji lookup the Vue components below use.
//
// `meh` is the middle verdict: the response is not wrong, but it is not one
// you would ship — often a sign the *test case* needs work rather than the
// model. Named `meh` rather than `ok` deliberately: `ok` already means
// "completed without an error" as a `run_results.status`, and one word
// meaning two things in the same table invites exactly the confusion this
// rating exists to remove.

export type Rating = 'good' | 'meh' | 'bad'

/** Best-to-worst — the order buttons and legends should follow. */
export const RATINGS: readonly Rating[] = ['good', 'meh', 'bad']

export function isRating(value: unknown): value is Rating {
  return value === 'good' || value === 'meh' || value === 'bad'
}

/** Narrows a raw API value; anything unexpected reads as unrated. */
export function parseRating(value: unknown): Rating | null {
  return isRating(value) ? value : null
}

export interface RatingMeta {
  label: string
  emoji: string
  description: string
  /** PrimeVue `Tag` severity for the badge. */
  severity: 'success' | 'warn' | 'danger'
}

export const RATING_META: Record<Rating, RatingMeta> = {
  good: { label: 'good', emoji: '👍', description: 'Good — would ship this', severity: 'success' },
  meh: {
    label: 'meh',
    emoji: '😐',
    description: 'Meh — not wrong, but not good enough',
    severity: 'warn',
  },
  bad: { label: 'bad', emoji: '👎', description: 'Bad — wrong or unusable', severity: 'danger' },
}

/** Per-rating tallies over a set of results, plus the unrated remainder. */
export interface RatingCounts {
  good: number
  meh: number
  bad: number
  unrated: number
}

export function countRatings(values: readonly (string | null)[]): RatingCounts {
  const counts: RatingCounts = { good: 0, meh: 0, bad: 0, unrated: 0 }
  for (const value of values) {
    const rating = parseRating(value)
    if (rating === null) counts.unrated += 1
    else counts[rating] += 1
  }
  return counts
}
