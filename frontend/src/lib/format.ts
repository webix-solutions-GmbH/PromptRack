// Small formatting helpers shared across views. Grows as later tasks need
// more (durations, token rates, …).

/** ISO 8601 in local time, minutes precision: `2026-08-15 07:07`. */
export function formatDateTime(value: string | number | Date): string {
  const date = new Date(value)
  const pad = (n: number) => String(n).padStart(2, '0')
  const day = `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
  return `${day} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

/** Compact wall-clock duration: `840ms`, `3.4s`, `1m 12s`. */
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

/**
 * Token-count chip text: `7 in / 40 out` when the prompt-token count is
 * known, falling back to the bare completion count (still `~`-marked when
 * estimated) when it isn't — same as when a provider's usage block never
 * arrives (`backend/app/services/llm.py`). `null` when there is no
 * completion count to show at all, so callers can `v-if` the chip away.
 */
export function formatTokenLabel(
  promptTokens: number | null,
  completionTokens: number | null,
  estimated: boolean,
): string | null {
  if (completionTokens === null) return null
  const completion = `${estimated ? '~' : ''}${completionTokens}`
  return promptTokens !== null ? `${promptTokens} in / ${completion} out` : completion
}

/**
 * One-line preview of a longer text: whitespace collapsed to single spaces
 * and cut to `max` characters with an ellipsis. `—` for nothing at all, so a
 * table cell keeps its em-dash placeholder instead of going blank.
 */
export function excerpt(value: string | null | undefined, max = 60): string {
  if (!value) return '—'
  const flat = value.replace(/\s+/g, ' ').trim()
  return flat.length > max ? `${flat.slice(0, max)}…` : flat
}

/**
 * An endpoint's name as a run displays it. A run keeps its
 * `endpoint_snapshot`, so the fallback is only reached when the run predates
 * the snapshot or carries none — the endpoint row itself being deleted never
 * blanks a past run (see the snapshot model in CLAUDE.md).
 */
export function endpointLabel(name: string | null | undefined): string {
  return name ?? '(deleted endpoint)'
}

/** Renders a run's `params` (temperature/max_tokens) for display. */
export function formatParams(params: Record<string, unknown> | null): string {
  if (!params) return 'server defaults'
  const entries = Object.entries(params)
  if (entries.length === 0) return 'server defaults'
  return entries.map(([key, value]) => `${key}=${String(value)}`).join(', ')
}
