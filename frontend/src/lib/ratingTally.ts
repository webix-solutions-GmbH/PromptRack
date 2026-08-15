// The rating-delta rule, shared by both `/results` pivots: a model-mode
// `ColumnTally` and a run-mode `CompareRunView`/`available_runs` entry are
// both just a count of `good`/`meh`/`bad` over some set of cells, and a
// single `PATCH .../rating` only ever moves one cell from one bucket to
// another (or in/out of unrated). Keeping that arithmetic here, pure and
// Vue-free, is what lets `ResultsView.vue` update the header tallies and the
// run picker in the same immutable assignment that patches the cell —
// refetching the matrix instead would re-render the table and lose whatever
// `<details>` a reviewer has open or the rubric popover they've pinned
// mid-rating-pass.
import type { Rating } from './rating'

/** Adjusts the three verdict counters on `tally` for a rating that moved
 * from `oldRating` to `newRating`, decrementing the old bucket and
 * incrementing the new one. Returns a new object — never mutates `tally`,
 * matching the spread-and-replace style the matrix state already uses. */
export function applyTallyDelta<T extends { good: number; meh: number; bad: number }>(
  tally: T,
  oldRating: Rating | null,
  newRating: Rating | null,
): T {
  const next = { ...tally }
  // Anything unrecognised counts as unrated, never as a verdict — mirrors
  // `app.services.compare`'s `_tallies()`: a legacy rating value must not
  // vanish into `bad` (nor be invented into `good`), so only the three
  // literal verdicts move a counter here.
  if (oldRating === 'good' || oldRating === 'meh' || oldRating === 'bad') {
    next[oldRating] = Math.max(0, next[oldRating] - 1)
  }
  if (newRating === 'good' || newRating === 'meh' || newRating === 'bad') {
    next[newRating] = next[newRating] + 1
  }
  return next
}

/** `applyTallyDelta` plus `unrated`'s re-derivation, for a model-mode
 * `ColumnTally` specifically. `answered` is untouched — a rating change
 * never changes how many on-screen cells have a result, only how they're
 * rated. */
export function applyColumnTallyDelta<
  T extends { answered: number; good: number; meh: number; bad: number; unrated: number },
>(tally: T, oldRating: Rating | null, newRating: Rating | null): T {
  const next = applyTallyDelta(tally, oldRating, newRating)
  return { ...next, unrated: next.answered - next.good - next.meh - next.bad }
}
