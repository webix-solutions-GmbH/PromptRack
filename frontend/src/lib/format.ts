// Small formatting helpers shared across views. Grows as later tasks need
// more (durations, token rates, …) — ported from the old `src/lib/format.ts`
// only as far as this task needs it.

export function formatDateTime(value: string | number | Date): string {
  return new Date(value).toLocaleString(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
  })
}
