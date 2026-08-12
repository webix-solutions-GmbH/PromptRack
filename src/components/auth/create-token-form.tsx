'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { createApiToken } from '@/actions/api-tokens';

const inputClass =
  'w-full rounded-md border border-zinc-300 bg-white px-3 py-2 text-sm text-zinc-900 placeholder:text-zinc-400 focus:border-zinc-500 focus:outline-none dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-50 dark:placeholder:text-zinc-500';
const labelClass = 'text-xs font-medium text-zinc-600 dark:text-zinc-400';

export function CreateTokenForm({ mcpUrl }: { mcpUrl: string }) {
  const router = useRouter();
  const [token, setToken] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const formData = new FormData(form);
    setError(null);
    setBusy(true);
    try {
      const created = await createApiToken(formData);
      setToken(created.token);
      form.reset();
      router.refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create the token.');
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="flex max-w-2xl flex-col gap-4 rounded-lg border border-zinc-200 p-6 dark:border-zinc-800">
      <h2 className="text-lg font-semibold text-zinc-900 dark:text-zinc-50">New token</h2>

      <form onSubmit={handleSubmit} className="flex flex-col gap-4">
        <div className="flex flex-col gap-1">
          <label className={labelClass} htmlFor="token-name">
            Name *
          </label>
          <input
            id="token-name"
            name="name"
            required
            placeholder="claude-code on my laptop"
            className={inputClass}
          />
        </div>
        <div className="flex flex-col gap-1">
          <label className={labelClass} htmlFor="token-expiry">
            Expires in (days)
          </label>
          <input
            id="token-expiry"
            name="expiresInDays"
            type="number"
            min={1}
            max={3650}
            placeholder="never"
            className={`${inputClass} max-w-40`}
          />
        </div>

        {error && <p className="text-sm text-red-600 dark:text-red-400">{error}</p>}

        <div>
          <button
            type="submit"
            disabled={busy}
            className="rounded-md bg-zinc-900 px-4 py-2 text-sm font-medium text-zinc-50 transition-colors hover:bg-zinc-700 disabled:opacity-60 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-300"
          >
            {busy ? 'Creating…' : 'Create token'}
          </button>
        </div>
      </form>

      {token && (
        <div className="flex flex-col gap-2 rounded-md border border-emerald-300 bg-emerald-50 p-4 dark:border-emerald-900 dark:bg-emerald-950">
          <p className="text-sm font-medium text-emerald-800 dark:text-emerald-300">
            This is the only time the token is shown — copy it now.
          </p>
          <pre className="overflow-x-auto rounded-md border border-emerald-200 bg-white p-3 font-mono text-xs text-zinc-900 dark:border-emerald-900 dark:bg-zinc-900 dark:text-zinc-50">
            {token}
          </pre>
          <p className="text-xs text-emerald-800 dark:text-emerald-300">Try it:</p>
          <pre className="overflow-x-auto rounded-md border border-emerald-200 bg-white p-3 font-mono text-xs text-zinc-700 dark:border-emerald-900 dark:bg-zinc-900 dark:text-zinc-300">
            {`curl -X POST ${mcpUrl} \\\n  -H 'x-api-key: ${token}' \\\n  -H 'content-type: application/json' \\\n  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'`}
          </pre>
        </div>
      )}
    </section>
  );
}
