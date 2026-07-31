'use client';

import { useState } from 'react';
import Link from 'next/link';
import { formatDateTime, formatDuration, formatRate } from '@/lib/format';
import { splitThinking } from '@/lib/thinking';
import { MarkdownResponse } from '@/components/markdown-response';
import { RatingBadge } from '@/components/runs/rating-badge';
import { describeRowDrift, type CompareCellView, type CompareRowView } from '@/lib/compare';

/** Characters of a response shown before it gets clamped. */
const CLAMP = 320;

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
  showProvenance,
}: {
  cell: CompareCellView;
  expanded: boolean;
  onToggle: () => void;
  /** Model mode: the run behind a cell is no longer named by its column header. */
  showProvenance: boolean;
}) {
  const { thinking, answer } = splitThinking(cell.responseText ?? '');
  const text = answer;
  const isLong = text.length > CLAMP;
  const shown = expanded || !isLong ? text : `${text.slice(0, CLAMP)}…`;
  const tokenLabel =
    cell.completionTokens === null
      ? null
      : `${cell.tokensEstimated ? '~' : ''}${cell.completionTokens} tok`;

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2">
        <RatingBadge rating={cell.rating} showUnrated />
        {cell.status !== 'ok' && (
          <span className="text-xs font-medium text-zinc-500 dark:text-zinc-400">
            {cell.status}
          </span>
        )}
      </div>

      {thinking !== null && !cell.error && (
        <details className="text-xs text-zinc-500 dark:text-zinc-400">
          <summary className="cursor-pointer hover:text-zinc-800 dark:hover:text-zinc-200">
            Thinking
          </summary>
          <p className="mt-1 max-h-64 overflow-auto whitespace-pre-wrap break-words font-mono text-[11px] italic text-zinc-600 dark:text-zinc-400">
            {thinking}
          </p>
        </details>
      )}

      {cell.error ? (
        <div className="rounded-md border border-red-300 bg-red-50 p-2 text-xs text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-300">
          {cell.error}
        </div>
      ) : text.length > 0 ? (
        <div className={expanded ? 'max-h-96 overflow-auto' : ''}>
          <MarkdownResponse text={shown} />
        </div>
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

      {cell.turnCount !== null && (
        <div className="flex flex-col gap-1 rounded-md border border-indigo-200 bg-indigo-50 p-2 dark:border-indigo-900 dark:bg-indigo-950/40">
          <span className="text-[11px] font-medium text-indigo-700 dark:text-indigo-300">
            {cell.turnCount} turn{cell.turnCount === 1 ? '' : 's'} · {cell.toolCallCount ?? 0} tool
            call{cell.toolCallCount === 1 ? '' : 's'}
          </span>
          {cell.toolCallNames.length > 0 && (
            <span className="break-words font-mono text-[11px] text-indigo-900 dark:text-indigo-200">
              {cell.toolCallNames.join(' → ')}
            </span>
          )}
        </div>
      )}

      <div className="flex flex-wrap gap-1">
        <Chip>{formatRate(cell.tokensPerSec)}</Chip>
        <Chip>{formatDuration(cell.durationMs)}</Chip>
        {tokenLabel && <Chip>{tokenLabel}</Chip>}
      </div>

      {cell.ratingNote && (
        <p className="text-xs italic text-zinc-500 dark:text-zinc-400">{cell.ratingNote}</p>
      )}

      {showProvenance && (
        <div className="flex flex-col gap-1 text-[11px] text-zinc-500 dark:text-zinc-500">
          <span>
            <Link
              href={`/runs/${cell.runId}`}
              className="font-medium underline-offset-2 hover:underline"
            >
              run #{cell.runId}
            </Link>{' '}
            · {formatDateTime(cell.runCreatedAt)}
          </span>
          {cell.superseded && (
            <span className="text-amber-600 dark:text-amber-400">
              newer attempt ({cell.superseded.status}) in run #{cell.superseded.runId} skipped
            </span>
          )}
        </div>
      )}
    </div>
  );
}

/**
 * One prompt row of the compare matrix. Client-side only for the expand/collapse
 * state: the whole row can be expanded at once, or a single cell on its own.
 */
export function CompareRow({
  row,
  anchoredToLivePrompt = false,
}: {
  row: CompareRowView;
  /**
   * Model mode: `row.promptText` is the live prompt rather than a snapshot, so
   * "edited since every compared run" becomes detectable.
   */
  anchoredToLivePrompt?: boolean;
}) {
  const [expandAll, setExpandAll] = useState(false);
  const [expandedCells, setExpandedCells] = useState<Record<number, boolean>>({});

  const anyLong = row.cells.some((cell) => (cell?.responseText?.length ?? 0) > CLAMP);
  const drift = describeRowDrift(
    row.cells,
    anchoredToLivePrompt ? row.promptText : undefined,
  );
  // With a single cell there is nothing to differ *across*: the only drift the
  // check can report is the live prompt having moved on from that one result.
  const single = row.cells.filter((cell) => cell !== null).length < 2;

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
          {drift.length > 0 && (
            <p
              className="rounded-md border border-amber-300 bg-amber-50 px-2 py-1 text-[11px] text-amber-800 dark:border-amber-900 dark:bg-amber-950/50 dark:text-amber-300"
              title={
                single
                  ? 'This result was not produced under the conditions the prompt now carries.'
                  : 'These cells were not produced under identical conditions, so a difference between them may not be the model.'
              }
            >
              {single ? drift.join(', ') : `differs across cells: ${drift.join(', ')}`}
            </p>
          )}
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
              showProvenance={anchoredToLivePrompt}
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
