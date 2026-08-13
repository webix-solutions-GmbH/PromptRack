// Small formatting helpers shared across views. Grows as later tasks need
// more (durations, token rates, …) — ported from the old `src/lib/format.ts`
// only as far as this task needs it.

export function formatDateTime(value: string | number | Date): string {
  return new Date(value).toLocaleString(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
  })
}

/** Compact wall-clock duration: `840ms`, `3.4s`, `1m 12s`. Port of
 * `git show master:src/lib/format.ts`'s `formatDuration`. */
export function formatDuration(ms: number | null | undefined): string {
  if (typeof ms !== 'number' || !Number.isFinite(ms)) return '—'
  if (ms < 1000) return `${Math.round(ms)}ms`
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`

  const minutes = Math.floor(ms / 60_000)
  const seconds = Math.round((ms % 60_000) / 1000)
  return `${minutes}m ${seconds}s`
}

export function formatRate(tokensPerSec: number | null | undefined): string {
  if (typeof tokensPerSec !== 'number' || !Number.isFinite(tokensPerSec)) return '—'
  return `${tokensPerSec.toFixed(1)} tok/s`
}

/**
 * Generation throughput for a single turn: completion tokens over the
 * *generation* window (duration minus the prefill/TTFT). Port of
 * `backend/app/services/llm.py`'s `compute_tokens_per_sec` — the run detail
 * page needs this client-side because `TurnMetrics` carries the raw
 * timings but not a precomputed rate (only the aggregate `tokens_per_sec`
 * on the finished result is precomputed server-side).
 */
export function computeTokensPerSec(
  completionTokens: number | null,
  durationMs: number | null,
  ttftMs: number | null,
): number | null {
  if (completionTokens === null || !Number.isFinite(completionTokens) || completionTokens <= 0) {
    return null
  }
  if (durationMs === null || !Number.isFinite(durationMs)) return null

  const prefill = ttftMs !== null && Number.isFinite(ttftMs) ? ttftMs : 0
  const generationMs = durationMs - prefill
  if (!Number.isFinite(generationMs) || generationMs <= 0) return null

  const rate = completionTokens / (generationMs / 1000)
  return Number.isFinite(rate) ? rate : null
}

/** Renders a run's `params` (temperature/max_tokens) for display. */
export function formatParams(params: Record<string, unknown> | null): string {
  if (!params) return 'server defaults'
  const entries = Object.entries(params)
  if (entries.length === 0) return 'server defaults'
  return entries.map(([key, value]) => `${key}=${String(value)}`).join(', ')
}
