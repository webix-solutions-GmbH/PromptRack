'use client';

import { useState, useTransition } from 'react';
import { useRouter } from 'next/navigation';
import { switchCustomer } from '@/actions/customers';

/**
 * What a deep link into another workspace renders instead of a bare 404.
 *
 * `/runs/42` shared between colleagues has to work, and "not found" would be a
 * lie — the row exists, it is just somewhere else. The switch still never
 * happens without a click, and the only thing this leaks is the workspace's
 * name, which the switcher already lists for every signed-in user.
 */
export function WrongWorkspaceNotice({
  what,
  workspace,
}: {
  /** The thing that lives elsewhere, e.g. "run" or "machine". */
  what: string;
  workspace: { id: number; name: string };
}) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);

  return (
    <div className="flex flex-1 flex-col gap-4 p-8">
      <h1 className="text-2xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
        Another workspace
      </h1>
      <p className="max-w-prose text-sm text-zinc-600 dark:text-zinc-400">
        This {what} belongs to workspace{' '}
        <strong className="font-medium text-zinc-900 dark:text-zinc-50">{workspace.name}</strong>,
        which is not the one you are in.
      </p>
      {error && <p className="text-sm text-red-600 dark:text-red-400">{error}</p>}
      <div>
        <button
          type="button"
          disabled={pending}
          onClick={() => {
            setError(null);
            startTransition(async () => {
              try {
                await switchCustomer(workspace.id);
                router.refresh();
              } catch {
                setError('Could not switch workspace.');
              }
            });
          }}
          className="rounded-md bg-zinc-900 px-4 py-2 text-sm font-medium text-zinc-50 transition-colors hover:bg-zinc-700 disabled:opacity-50 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-300"
        >
          {pending ? 'Switching…' : `Switch to ${workspace.name}`}
        </button>
      </div>
    </div>
  );
}
