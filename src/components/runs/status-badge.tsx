import type { RunResultStatus, RunStatus } from '@/lib/run-events';

const STYLES: Record<string, string> = {
  pending: 'bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400',
  running: 'animate-pulse bg-blue-100 text-blue-700 dark:bg-blue-950 dark:text-blue-300',
  ok: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-400',
  completed: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-400',
  error: 'bg-red-100 text-red-700 dark:bg-red-950 dark:text-red-400',
  failed: 'bg-red-100 text-red-700 dark:bg-red-950 dark:text-red-400',
  archived: 'bg-amber-100 text-amber-700 dark:bg-amber-950 dark:text-amber-400',
};

export function StatusBadge({
  status,
  className = '',
}: {
  status: RunStatus | RunResultStatus | string;
  className?: string;
}) {
  const style = STYLES[status] ?? STYLES.pending;

  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${style} ${className}`}
    >
      {status}
    </span>
  );
}
