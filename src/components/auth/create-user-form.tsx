'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { createUser } from '@/actions/users';
import { ROLES, ROLE_LABELS } from '@/lib/auth/policy';

const inputClass =
  'w-full rounded-md border border-zinc-300 bg-white px-3 py-2 text-sm text-zinc-900 placeholder:text-zinc-400 focus:border-zinc-500 focus:outline-none dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-50 dark:placeholder:text-zinc-500';
const labelClass = 'text-xs font-medium text-zinc-600 dark:text-zinc-400';

export function CreateUserForm() {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const formData = new FormData(form);
    setError(null);
    setBusy(true);
    try {
      await createUser(formData);
      form.reset();
      router.refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create the user.');
    } finally {
      setBusy(false);
    }
  }

  return (
    <form
      onSubmit={handleSubmit}
      className="flex max-w-2xl flex-col gap-4 rounded-lg border border-zinc-200 p-6 dark:border-zinc-800"
    >
      <h2 className="text-lg font-semibold text-zinc-900 dark:text-zinc-50">New user</h2>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div className="flex flex-col gap-1">
          <label className={labelClass} htmlFor="user-name">
            Name *
          </label>
          <input id="user-name" name="name" required autoComplete="off" className={inputClass} />
        </div>
        <div className="flex flex-col gap-1">
          <label className={labelClass} htmlFor="user-email">
            Email *
          </label>
          <input
            id="user-email"
            name="email"
            type="email"
            required
            autoComplete="off"
            className={inputClass}
          />
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div className="flex flex-col gap-1">
          <label className={labelClass} htmlFor="user-password">
            Password *
          </label>
          <input
            id="user-password"
            name="password"
            type="password"
            required
            minLength={12}
            autoComplete="new-password"
            className={inputClass}
          />
          <p className="text-xs text-zinc-500 dark:text-zinc-400">At least 12 characters.</p>
        </div>
        <div className="flex flex-col gap-1">
          <label className={labelClass} htmlFor="user-role">
            Role
          </label>
          <select id="user-role" name="role" defaultValue="member" className={inputClass}>
            {ROLES.map((role) => (
              <option key={role} value={role}>
                {ROLE_LABELS[role]}
              </option>
            ))}
          </select>
        </div>
      </div>

      {error && <p className="text-sm text-red-600 dark:text-red-400">{error}</p>}

      <div>
        <button
          type="submit"
          disabled={busy}
          className="rounded-md bg-zinc-900 px-4 py-2 text-sm font-medium text-zinc-50 transition-colors hover:bg-zinc-700 disabled:opacity-60 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-300"
        >
          {busy ? 'Creating…' : 'Create user'}
        </button>
      </div>
    </form>
  );
}
