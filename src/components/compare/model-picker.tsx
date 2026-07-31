'use client';

import { useCallback } from 'react';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import {
  MAX_COMPARE_MODELS,
  parseModelColumnKeys,
  type ModelColumnView,
} from '@/lib/compare';
import { formatDateTime, formatRate } from '@/lib/format';
import { RATING_META } from '@/lib/rating';

/**
 * Checkbox list of model columns (model × machine). The selection lives in
 * repeated `?model=` params, so a view stays linkable — and `mode` is pinned
 * here so the page never falls back to run mode on a click.
 *
 * One model is a complete selection (see `MIN_COMPARE_MODELS`), so nothing here
 * pushes towards picking a second.
 */
export function ModelPicker({ columns }: { columns: ModelColumnView[] }) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const selected = parseModelColumnKeys(searchParams.getAll('model'));

  const push = useCallback(
    (keys: string[]) => {
      const params = new URLSearchParams(searchParams.toString());
      params.set('mode', 'models');
      params.delete('model');
      for (const key of keys) params.append('model', key);
      router.push(`${pathname}?${params.toString()}`, { scroll: false });
    },
    [pathname, router, searchParams],
  );

  const toggle = useCallback(
    (key: string) => {
      if (selected.includes(key)) {
        push(selected.filter((selectedKey) => selectedKey !== key));
      } else if (selected.length < MAX_COMPARE_MODELS) {
        push([...selected, key]);
      }
    },
    [push, selected],
  );

  if (columns.length === 0) {
    return (
      <div className="rounded-lg border border-zinc-200 p-6 text-sm text-zinc-500 dark:border-zinc-800 dark:text-zinc-400">
        No results yet — finish a run first, then come back.
      </div>
    );
  }

  return (
    <section className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-sm font-medium text-zinc-700 dark:text-zinc-300">
          Select one model to review, or several to compare
        </h2>
        <div className="flex items-center gap-3 text-xs text-zinc-500 dark:text-zinc-400">
          <span>
            {selected.length} of {MAX_COMPARE_MODELS} selected
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
              <th className="px-4 py-3">Show</th>
              <th className="px-4 py-3">Model</th>
              <th className="px-4 py-3">Machine</th>
              <th className="px-4 py-3" title="Distinct prompts with a usable result">
                Prompts
              </th>
              <th className="px-4 py-3">Runs</th>
              <th className="px-4 py-3">Latest run</th>
              <th className="px-4 py-3" title="good / meh / bad over all results">
                Rating
              </th>
              <th className="px-4 py-3">Avg speed</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-zinc-200 dark:divide-zinc-800">
            {columns.map((column) => {
              const isSelected = selected.includes(column.key);
              const isDisabled = !isSelected && selected.length >= MAX_COMPARE_MODELS;

              return (
                <tr
                  key={column.key}
                  onClick={() => !isDisabled && toggle(column.key)}
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
                      onChange={() => toggle(column.key)}
                      onClick={(event) => event.stopPropagation()}
                      aria-label={`Show ${column.modelId} on ${column.machineName}`}
                      className="h-4 w-4 accent-zinc-900 dark:accent-zinc-100"
                    />
                  </td>
                  <td className="px-4 py-3 font-mono text-xs font-medium text-zinc-900 dark:text-zinc-50">
                    {column.modelId}
                  </td>
                  <td className="px-4 py-3 text-zinc-600 dark:text-zinc-400">
                    {column.machineName}
                  </td>
                  <td className="px-4 py-3 text-zinc-600 dark:text-zinc-400">
                    {column.promptCount}
                  </td>
                  <td className="px-4 py-3 text-zinc-600 dark:text-zinc-400">
                    {column.runCount}
                  </td>
                  <td className="px-4 py-3 text-zinc-600 dark:text-zinc-400">
                    {formatDateTime(column.latestRunAt)}
                  </td>
                  <td className="px-4 py-3">
                    <span className={RATING_META.good.text}>{column.good}</span>
                    <span className="text-zinc-400 dark:text-zinc-500">/</span>
                    <span className={RATING_META.meh.text}>{column.meh}</span>
                    <span className="text-zinc-400 dark:text-zinc-500">/</span>
                    <span className={RATING_META.bad.text}>{column.bad}</span>
                  </td>
                  <td className="px-4 py-3 text-zinc-600 dark:text-zinc-400">
                    {formatRate(column.avgRate)}
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
