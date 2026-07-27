'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { apiPath } from '@/lib/base-path';

type DiscoverResult =
  | { ok: true; discovered: number; models: string[] }
  | { ok: false; error: string };

export function DiscoverModelsButton({ machineId }: { machineId: number }) {
  const router = useRouter();
  const [pending, setPending] = useState(false);
  const [result, setResult] = useState<DiscoverResult | null>(null);

  async function handleClick() {
    setPending(true);
    setResult(null);
    try {
      const res = await fetch(apiPath(`/api/machines/${machineId}/discover`), { method: 'POST' });
      const data = (await res.json()) as DiscoverResult;
      setResult(data);
      if (data.ok) {
        router.refresh();
      }
    } catch {
      setResult({ ok: false, error: 'Request failed unexpectedly.' });
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="flex flex-wrap items-center gap-3">
      <button
        type="button"
        onClick={handleClick}
        disabled={pending}
        className="rounded-md bg-zinc-900 px-3 py-2 text-sm font-medium text-zinc-50 transition-colors hover:bg-zinc-700 disabled:opacity-50 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-300"
      >
        {pending ? 'Discovering…' : 'Discover models'}
      </button>
      {result && (
        <span
          className={`text-sm ${
            result.ok
              ? 'text-emerald-600 dark:text-emerald-400'
              : 'text-red-600 dark:text-red-400'
          }`}
        >
          {result.ok
            ? `Found ${result.discovered} model${result.discovered === 1 ? '' : 's'}`
            : result.error}
        </span>
      )}
    </div>
  );
}
