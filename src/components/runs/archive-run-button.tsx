'use client';

import { useState, useTransition } from 'react';
import { useRouter } from 'next/navigation';
import { setRunArchived } from '@/actions/runs';

/**
 * Archive / unarchive toggle for a run, sitting next to Delete.
 *
 * `compact` renders the small icon variant used inside the runs table; the
 * default variant is the bordered button on the run detail page. Unlike delete
 * this needs no confirmation — it is reversible from the same button.
 */
export function ArchiveRunButton({
  runId,
  archived,
  compact = false,
}: {
  runId: number;
  archived: boolean;
  compact?: boolean;
}) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);

  const label = archived ? 'Unarchive run' : 'Archive run';

  function handleClick() {
    setError(null);
    startTransition(async () => {
      try {
        await setRunArchived(runId, !archived);
        router.refresh();
      } catch (err) {
        setError(err instanceof Error ? err.message : `Failed to ${label.toLowerCase()}.`);
      }
    });
  }

  if (compact) {
    return (
      <button
        type="button"
        onClick={handleClick}
        disabled={pending}
        aria-label={`${label} #${runId}`}
        title={error ?? label}
        className={`rounded p-1 transition-colors hover:bg-zinc-100 disabled:opacity-50 dark:hover:bg-zinc-800 ${
          error
            ? 'text-red-600 dark:text-red-400'
            : archived
              ? 'text-amber-600 dark:text-amber-400'
              : 'text-zinc-400 hover:text-zinc-700 dark:text-zinc-500 dark:hover:text-zinc-300'
        } ${pending ? 'animate-pulse' : ''}`}
      >
        {archived ? (
          // Box with an up arrow — restore.
          <svg
            xmlns="http://www.w3.org/2000/svg"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            className="h-4 w-4"
            aria-hidden
          >
            <rect x="2" y="4" width="20" height="4" rx="1" />
            <path d="M4 8v11a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1V8" />
            <path d="M12 17v-6" />
            <path d="M9.5 13.5 12 11l2.5 2.5" />
          </svg>
        ) : (
          // Box with a down arrow — file away.
          <svg
            xmlns="http://www.w3.org/2000/svg"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            className="h-4 w-4"
            aria-hidden
          >
            <rect x="2" y="4" width="20" height="4" rx="1" />
            <path d="M4 8v11a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1V8" />
            <path d="M12 11v6" />
            <path d="M9.5 14.5 12 17l2.5-2.5" />
          </svg>
        )}
      </button>
    );
  }

  return (
    <div className="flex items-center gap-3">
      {error && <span className="text-sm text-red-600 dark:text-red-400">{error}</span>}
      <button
        type="button"
        onClick={handleClick}
        disabled={pending}
        className="rounded-md border border-zinc-300 px-3 py-2 text-sm font-medium text-zinc-700 transition-colors hover:bg-zinc-100 disabled:opacity-50 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800"
      >
        {pending ? 'Saving…' : label}
      </button>
    </div>
  );
}
