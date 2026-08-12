export function formatDateTime(value: number | Date): string {
  return new Date(value).toLocaleString(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
  });
}

/** Compact local-time ISO stamp for tables: `2026-07-27 09:46`. */
export function formatIsoDateTime(value: number | Date): string {
  const date = new Date(value);
  const pad = (value: number) => String(value).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

/** Compact wall-clock duration: `840ms`, `3.4s`, `1m 12s`. */
export function formatDuration(ms: number | null | undefined): string {
  if (typeof ms !== 'number' || !Number.isFinite(ms)) return '—';
  if (ms < 1000) return `${Math.round(ms)}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;

  const minutes = Math.floor(ms / 60_000);
  const seconds = Math.round((ms % 60_000) / 1000);
  return `${minutes}m ${seconds}s`;
}

/** Machine name out of a run's frozen `machine_snapshot` JSON. */
export function snapshotMachineName(raw: string): string {
  try {
    const parsed: unknown = JSON.parse(raw);
    const name =
      parsed && typeof parsed === 'object' ? (parsed as { name?: unknown }).name : undefined;
    return typeof name === 'string' && name.length > 0 ? name : '(deleted machine)';
  } catch {
    return '(deleted machine)';
  }
}

export function formatRate(tokensPerSec: number | null | undefined): string {
  if (typeof tokensPerSec !== 'number' || !Number.isFinite(tokensPerSec)) return '—';
  return `${tokensPerSec.toFixed(1)} tok/s`;
}
