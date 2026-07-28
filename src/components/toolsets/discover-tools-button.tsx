'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { apiPath } from '@/lib/base-path';

type DiscoverResult =
  | { ok: true; discovered: number; retired: number; tools: string[] }
  | { ok: false; error: string };

export function DiscoverToolsButton({ toolsetId }: { toolsetId: number }) {
  const router = useRouter();
  const [pending, setPending] = useState(false);
  const [result, setResult] = useState<DiscoverResult | null>(null);

  async function handleClick() {
    setPending(true);
    setResult(null);
    try {
      const res = await fetch(apiPath(`/api/toolsets/${toolsetId}/discover`), { method: 'POST' });
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
    <div className="flex flex-wrap items-center gap-2">
      <button
        type="button"
        onClick={handleClick}
        disabled={pending}
        className="rounded-md border border-zinc-300 px-3 py-1.5 text-sm font-medium text-zinc-700 transition-colors hover:bg-zinc-100 disabled:opacity-50 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800"
      >
        {pending ? 'Discovering…' : 'Discover tools'}
      </button>
      {result && (
        <span
          className={`text-xs ${
            result.ok
              ? 'text-emerald-600 dark:text-emerald-400'
              : 'text-red-600 dark:text-red-400'
          }`}
        >
          {result.ok
            ? `Found ${result.discovered} tool${result.discovered === 1 ? '' : 's'}${
                result.retired > 0 ? `, disabled ${result.retired} that vanished` : ''
              }`
            : result.error}
        </span>
      )}
    </div>
  );
}
