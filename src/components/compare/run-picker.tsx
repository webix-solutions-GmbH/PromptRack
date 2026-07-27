'use client';

import { useCallback } from 'react';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import {
  MAX_COMPARE_RUNS,
  parseRunIds,
  serializeRunIds,
  type CompareRunView,
} from '@/lib/compare';
import { formatDateTime, formatRate } from '@/lib/format';
import { StatusBadge } from '@/components/runs/status-badge';

/**
 * Checkbox list of comparable runs. The selection lives in `?runs=1,5,7`, so a
 * comparison is always linkable/bookmarkable.
 */
export function RunPicker({ runs }: { runs: CompareRunView[] }) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const selected = parseRunIds(searchParams.get('runs') ?? undefined);

  const push = useCallback(
    (ids: number[]) => {
      const params = new URLSearchParams(searchParams.toString());
      if (ids.length > 0) {
        params.set('runs', serializeRunIds(ids));
      } else {
        params.delete('runs');
      }
      const query = params.toString();
      router.push(query.length > 0 ? `${pathname}?${query}` : pathname, { scroll: false });
    },
    [pathname, router, searchParams],
  );

  const toggle = useCallback(
    (id: number) => {
      if (selected.includes(id)) {
        push(selected.filter((selectedId) => selectedId !== id));
      } else if (selected.length < MAX_COMPARE_RUNS) {
        push([...selected, id]);
      }
    },
    [push, selected],
  );

  if (runs.length === 0) {
    return (
      <div className="rounded-lg border border-zinc-200 p-6 text-sm text-zinc-500 dark:border-zinc-800 dark:text-zinc-400">
        No completed runs yet — finish a run first, then come back to compare.
      </div>
    );
  }

  return (
    <section className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-sm font-medium text-zinc-700 dark:text-zinc-300">
          Select runs to compare
        </h2>
        <div className="flex items-center gap-3 text-xs text-zinc-500 dark:text-zinc-400">
          <span>
            {selected.length} of {MAX_COMPARE_RUNS} selected
          </span>
          {selected.length > 0 && (
            <button
              type="button"
              onClick={() => push([])}
              className="font-medium underline-offset-2 hover:underline"
            >
              Clear
            </button>
          )}
        </div>
      </div>

      <div className="max-h-80 overflow-auto rounded-lg border border-zinc-200 dark:border-zinc-800">
        <table className="w-full min-w-max text-left text-sm">
          <thead className="sticky top-0 border-b border-zinc-200 bg-zinc-50 text-xs font-medium uppercase tracking-wide text-zinc-500 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-400">
            <tr>
              <th className="px-4 py-3">Compare</th>
              <th className="px-4 py-3">Run</th>
              <th className="px-4 py-3">Model</th>
              <th className="px-4 py-3">Machine</th>
              <th className="px-4 py-3">Created</th>
              <th className="px-4 py-3">Groups</th>
              <th className="px-4 py-3">Status</th>
              <th className="px-4 py-3">Good/Bad</th>
              <th className="px-4 py-3">Avg speed</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-zinc-200 dark:divide-zinc-800">
            {runs.map((run) => {
              const isSelected = selected.includes(run.id);
              const isDisabled = !isSelected && selected.length >= MAX_COMPARE_RUNS;

              return (
                <tr
                  key={run.id}
                  onClick={() => !isDisabled && toggle(run.id)}
                  className={`cursor-pointer transition-colors ${
                    isSelected
                      ? 'bg-zinc-100 dark:bg-zinc-900'
                      : isDisabled
                        ? 'cursor-not-allowed opacity-50'
                        : 'hover:bg-zinc-50 dark:hover:bg-zinc-900'
                  }`}
                >
                  <td className="px-4 py-3">
                    <input
                      type="checkbox"
                      checked={isSelected}
                      disabled={isDisabled}
                      onChange={() => toggle(run.id)}
                      onClick={(event) => event.stopPropagation()}
                      aria-label={`Compare run #${run.id}`}
                      className="h-4 w-4 accent-zinc-900 dark:accent-zinc-100"
                    />
                  </td>
                  <td className="px-4 py-3 font-medium text-zinc-900 dark:text-zinc-50">
                    #{run.id}
                  </td>
                  <td className="px-4 py-3 font-mono text-xs text-zinc-600 dark:text-zinc-400">
                    {run.modelId}
                  </td>
                  <td className="px-4 py-3 text-zinc-600 dark:text-zinc-400">
                    {run.machineName}
                  </td>
                  <td className="px-4 py-3 text-zinc-600 dark:text-zinc-400">
                    {formatDateTime(run.createdAt)}
                  </td>
                  <td className="px-4 py-3 text-zinc-600 dark:text-zinc-400">
                    {run.groupNames.length > 0 ? run.groupNames.join(', ') : '—'}
                  </td>
                  <td className="px-4 py-3">
                    <StatusBadge status={run.status} />
                  </td>
                  <td className="px-4 py-3">
                    <span className="text-emerald-600 dark:text-emerald-400">{run.good}</span>
                    <span className="text-zinc-400 dark:text-zinc-500">/</span>
                    <span className="text-red-600 dark:text-red-400">{run.bad}</span>
                  </td>
                  <td className="px-4 py-3 text-zinc-600 dark:text-zinc-400">
                    {formatRate(run.avgRate)}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}
