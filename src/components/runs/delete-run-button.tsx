'use client';

import { useState, useTransition } from 'react';
import { useRouter } from 'next/navigation';
import { deleteRun } from '@/actions/runs';

/**
 * Confirm-then-delete for a run. `compact` renders the small text-link variant
 * used inside the runs table; the default variant is the bordered button on the
 * run detail page, which navigates back to the list after deleting.
 */
export function DeleteRunButton({ runId, compact = false }: { runId: number; compact?: boolean }) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);

  function handleClick() {
    if (!window.confirm(`Delete run #${runId} and all its results? This cannot be undone.`)) {
      return;
    }
    setError(null);
    startTransition(async () => {
      try {
        await deleteRun(runId);
        if (!compact) {
          router.push('/runs');
        }
      } catch {
        setError('Failed to delete run.');
      }
    });
  }

  if (compact) {
    return (
      <button
        type="button"
        onClick={handleClick}
        disabled={pending}
        aria-label={`Delete run #${runId}`}
        title={error ?? 'Delete run'}
        className={`rounded p-1 transition-colors hover:bg-red-50 disabled:opacity-50 dark:hover:bg-red-950 ${
          error ? 'text-red-600 dark:text-red-400' : 'text-zinc-400 hover:text-red-600 dark:text-zinc-500 dark:hover:text-red-400'
        } ${pending ? 'animate-pulse' : ''}`}
      >
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
          <path d="M3 6h18" />
          <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6" />
          <path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
          <line x1="10" y1="11" x2="10" y2="17" />
          <line x1="14" y1="11" x2="14" y2="17" />
        </svg>
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
        className="rounded-md border border-red-300 px-3 py-2 text-sm font-medium text-red-600 transition-colors hover:bg-red-50 disabled:opacity-50 dark:border-red-900 dark:text-red-400 dark:hover:bg-red-950"
      >
        {pending ? 'Deleting…' : 'Delete run'}
      </button>
    </div>
  );
}
