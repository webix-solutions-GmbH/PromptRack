'use client';

import { useState } from 'react';

type TestResult =
  | { ok: true; status: number; latencyMs: number }
  | { ok: false; error: string; status?: number };

export function TestConnectionButton({ machineId }: { machineId: number }) {
  const [pending, setPending] = useState(false);
  const [result, setResult] = useState<TestResult | null>(null);

  async function handleClick() {
    setPending(true);
    setResult(null);
    try {
      const res = await fetch(`/api/machines/${machineId}/test`, { method: 'POST' });
      const data = (await res.json()) as TestResult;
      setResult(data);
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
        className="rounded-md border border-zinc-300 px-3 py-2 text-sm font-medium text-zinc-700 transition-colors hover:bg-zinc-100 disabled:opacity-50 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800"
      >
        {pending ? 'Testing…' : 'Test connection'}
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
            ? `Reachable — HTTP ${result.status} in ${result.latencyMs}ms`
            : result.error}
        </span>
      )}
    </div>
  );
}
