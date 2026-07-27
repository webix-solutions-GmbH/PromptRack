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
        title={error ?? undefined}
        className="text-xs font-medium text-red-600 transition-colors hover:underline disabled:opacity-50 dark:text-red-400"
      >
        {pending ? 'Deleting…' : error ? 'Delete failed — retry' : 'Delete'}
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
