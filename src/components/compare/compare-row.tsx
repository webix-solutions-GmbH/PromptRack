'use client';

import { useState } from 'react';
import { formatDuration, formatRate } from '@/lib/format';
import type { CompareCellView, CompareRowView } from '@/lib/compare';

/** Characters of a response shown before it gets clamped. */
const CLAMP = 320;

function RatingBadge({ rating }: { rating: 'good' | 'bad' | null }) {
  const style =
    rating === 'good'
      ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-400'
      : rating === 'bad'
        ? 'bg-red-100 text-red-700 dark:bg-red-950 dark:text-red-400'
        : 'bg-zinc-100 text-zinc-500 dark:bg-zinc-800 dark:text-zinc-400';
  const label = rating === 'good' ? '👍 good' : rating === 'bad' ? '👎 bad' : 'unrated';

  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${style}`}
    >
      {label}
    </span>
  );
}

function Chip({ children }: { children: React.ReactNode }) {
  return (
    <span className="inline-flex items-center gap-1 rounded-md border border-zinc-200 px-1.5 py-0.5 font-mono text-[11px] text-zinc-600 dark:border-zinc-800 dark:text-zinc-400">
      {children}
    </span>
  );
}

function Cell({
  cell,
  expanded,
  onToggle,
}: {
  cell: CompareCellView;
  expanded: boolean;
  onToggle: () => void;
}) {
  const text = cell.responseText ?? '';
  const isLong = text.length > CLAMP;
  const shown = expanded || !isLong ? text : `${text.slice(0, CLAMP)}…`;
  const tokenLabel =
    cell.completionTokens === null
      ? null
      : `${cell.tokensEstimated ? '~' : ''}${cell.completionTokens} tok`;

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2">
        <RatingBadge rating={cell.rating} />
        {cell.status !== 'ok' && (
          <span className="text-xs font-medium text-zinc-500 dark:text-zinc-400">
            {cell.status}
          </span>
        )}
      </div>

      {cell.error ? (
        <div className="rounded-md border border-red-300 bg-red-50 p-2 text-xs text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-300">
          {cell.error}
        </div>
      ) : text.length > 0 ? (
        <p
          className={`whitespace-pre-wrap break-words font-mono text-xs text-zinc-700 dark:text-zinc-300 ${
            expanded ? 'max-h-96 overflow-auto' : ''
          }`}
        >
          {shown}
        </p>
      ) : (
        <p className="text-xs text-zinc-400 dark:text-zinc-500">(no response)</p>
      )}

      {isLong && (
        <button
          type="button"
          onClick={onToggle}
          className="self-start text-xs font-medium text-zinc-500 underline-offset-2 hover:underline dark:text-zinc-400"
        >
          {expanded ? 'Show less' : 'Show more'}
        </button>
      )}

      <div className="flex flex-wrap gap-1">
        <Chip>{formatRate(cell.tokensPerSec)}</Chip>
        <Chip>{formatDuration(cell.durationMs)}</Chip>
        {tokenLabel && <Chip>{tokenLabel}</Chip>}
      </div>

      {cell.ratingNote && (
        <p className="text-xs italic text-zinc-500 dark:text-zinc-400">{cell.ratingNote}</p>
      )}
    </div>
  );
}

/**
 * One prompt row of the compare matrix. Client-side only for the expand/collapse
 * state: the whole row can be expanded at once, or a single cell on its own.
 */
export function CompareRow({ row }: { row: CompareRowView }) {
  const [expandAll, setExpandAll] = useState(false);
  const [expandedCells, setExpandedCells] = useState<Record<number, boolean>>({});

  const anyLong = row.cells.some((cell) => (cell?.responseText?.length ?? 0) > CLAMP);

  return (
    <tr className="align-top">
      <th
        scope="row"
        className="sticky left-0 z-10 w-64 min-w-64 border-r border-zinc-200 bg-white px-4 py-4 text-left align-top font-normal dark:border-zinc-800 dark:bg-zinc-950"
      >
        <div className="flex flex-col gap-1">
          <span className="text-[11px] uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
            {row.groupName}
            {row.promptId === null && ' · deleted prompt'}
          </span>
          <span className="text-sm font-semibold text-zinc-900 dark:text-zinc-50">
            {row.promptTitle}
          </span>
          <details className="text-xs text-zinc-500 dark:text-zinc-400">
            <summary className="cursor-pointer hover:text-zinc-800 dark:hover:text-zinc-200">
              Prompt
            </summary>
            <p className="mt-1 whitespace-pre-wrap break-words font-mono text-[11px] text-zinc-600 dark:text-zinc-400">
              {row.promptText}
            </p>
          </details>
          {anyLong && (
            <button
              type="button"
              onClick={() => {
                setExpandAll((value) => !value);
                setExpandedCells({});
              }}
              className="self-start text-xs font-medium text-zinc-500 underline-offset-2 hover:underline dark:text-zinc-400"
            >
              {expandAll ? 'Collapse row' : 'Expand row'}
            </button>
          )}
        </div>
      </th>

      {row.cells.map((cell, index) => (
        <td
          key={index}
          className="w-96 min-w-80 max-w-md border-l border-zinc-200 px-4 py-4 align-top dark:border-zinc-800"
        >
          {cell === null ? (
            <span className="text-sm text-zinc-400 dark:text-zinc-600">—</span>
          ) : (
            <Cell
              cell={cell}
              expanded={expandAll !== Boolean(expandedCells[index])}
              onToggle={() =>
                setExpandedCells((current) => ({ ...current, [index]: !current[index] }))
              }
            />
          )}
        </td>
      ))}
    </tr>
  );
}
