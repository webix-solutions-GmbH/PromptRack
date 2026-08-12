'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { authClient } from '@/lib/auth-client';
import { ROLE_LABELS, type Role } from '@/lib/auth/policy';

export function UserMenu({
  name,
  email,
  role,
}: {
  name: string;
  email: string;
  role: Role;
}) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);

  async function signOut() {
    setBusy(true);
    await authClient.signOut();
    router.push('/login');
    router.refresh();
  }

  return (
    <div className="mt-auto flex flex-col gap-2 border-t border-zinc-200 p-4 dark:border-zinc-800">
      <div className="flex flex-col">
        <span className="truncate text-sm font-medium text-zinc-900 dark:text-zinc-50">{name}</span>
        <span className="truncate text-xs text-zinc-500 dark:text-zinc-400">{email}</span>
        <span className="mt-1 w-fit rounded-full bg-zinc-100 px-2 py-0.5 text-[11px] font-medium text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300">
          {ROLE_LABELS[role]}
        </span>
      </div>
      <button
        type="button"
        onClick={signOut}
        disabled={busy}
        className="w-fit text-xs font-medium text-zinc-500 underline-offset-2 transition-colors hover:text-zinc-900 hover:underline disabled:opacity-60 dark:text-zinc-400 dark:hover:text-zinc-50"
      >
        Sign out
      </button>
    </div>
  );
}
