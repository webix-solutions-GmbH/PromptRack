'use client';

import { useState, useTransition } from 'react';
import { useRouter } from 'next/navigation';
import { deleteMachine } from '@/actions/machines';

export function DeleteMachineButton({ id, name }: { id: number; name: string }) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);

  function handleClick() {
    if (!window.confirm(`Delete machine "${name}"? This cannot be undone.`)) {
      return;
    }
    setError(null);
    startTransition(async () => {
      try {
        await deleteMachine(id);
        router.push('/machines');
      } catch {
        setError('Failed to delete machine.');
      }
    });
  }

  return (
    <div className="flex items-center gap-3">
      <button
        type="button"
        onClick={handleClick}
        disabled={pending}
        className="rounded-md border border-red-300 px-3 py-2 text-sm font-medium text-red-600 transition-colors hover:bg-red-50 disabled:opacity-50 dark:border-red-900 dark:text-red-400 dark:hover:bg-red-950"
      >
        {pending ? 'Deleting…' : 'Delete machine'}
      </button>
      {error && <span className="text-sm text-red-600 dark:text-red-400">{error}</span>}
    </div>
  );
}
