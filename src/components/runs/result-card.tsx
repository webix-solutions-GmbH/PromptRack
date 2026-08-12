'use client';

import { formatDuration, formatRate } from '@/lib/format';
import { splitThinking } from '@/lib/thinking';
import { MarkdownResponse } from '@/components/markdown-response';
import { RatingBadge } from './rating-badge';
import { ResultRating } from './result-rating';
import { StatusBadge } from './status-badge';
import { ToolTranscript } from './tool-transcript';
import type { ResultView } from './types';
import type { Rating } from '@/lib/rating';

function Chip({ label, value }: { label: string; value: string }) {
  return (
    <span className="inline-flex items-center gap-1 rounded-md border border-zinc-200 px-2 py-0.5 text-xs text-zinc-600 dark:border-zinc-800 dark:text-zinc-400">
      <span className="text-zinc-400 dark:text-zinc-500">{label}</span>
      <span className="font-mono text-zinc-700 dark:text-zinc-300">{value}</span>
    </span>
  );
}

const preClass =
  'max-h-96 overflow-auto whitespace-pre-wrap break-words rounded-md border border-zinc-200 bg-zinc-50 p-3 font-mono text-xs text-zinc-700 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-300';

/**
 * Response text with any leading `<think>` block tucked behind a collapsed
 * toggle, so reasoning models don't drown the actual answer.
 */
function ResponseBlock({ text, running }: { text: string | null; running: boolean }) {
  if (text === null) {
    return <pre className={preClass}>{running ? '…' : '—'}</pre>;
  }

  const { thinking, answer, thinkingClosed } = splitThinking(text);

  return (
    <>
      {thinking !== null && (
        <details>
          <summary className="cursor-pointer text-xs font-medium text-zinc-500 hover:text-zinc-800 dark:text-zinc-400 dark:hover:text-zinc-200">
            Thinking{thinkingClosed ? '' : '…'}
          </summary>
          <pre className={`${preClass} mt-1 italic`}>{thinking}</pre>
        </details>
      )}
      {answer ? (
        <div className="max-h-96 overflow-auto rounded-md border border-zinc-200 bg-zinc-50 p-3 text-xs dark:border-zinc-800 dark:bg-zinc-900">
          <MarkdownResponse text={answer} />
        </div>
      ) : (
        <pre className={preClass}>
          {running ? '…' : thinking !== null ? '(empty answer)' : '—'}
        </pre>
      )}
    </>
  );
}

export function ResultCard({
  result,
  index,
  canWrite,
  onRatingChange,
}: {
  result: ResultView;
  index: number;
  /** A viewer still sees the verdict in the badge, just not the buttons. */
  canWrite: boolean;
  onRatingChange: (
    resultId: number,
    patch: { rating?: Rating | null; ratingNote?: string | null },
  ) => void;
}) {
  const hasMetrics =
    result.durationMs !== null ||
    result.ttftMs !== null ||
    result.completionTokens !== null ||
    result.tokensPerSec !== null;

  const tokenLabel =
    result.completionTokens === null
      ? null
      : `${result.tokensEstimated ? '~' : ''}${result.completionTokens}${
          result.promptTokens !== null ? ` / ${result.promptTokens} in` : ''
        }`;

  const isToolRun = result.toolMode !== 'none';
  const hasTranscript = isToolRun && (result.transcript?.length ?? 0) > 0;

  return (
    <article className="flex flex-col gap-3 rounded-lg border border-zinc-200 p-5 dark:border-zinc-800">
      <header className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex min-w-0 flex-col gap-0.5">
          <span className="text-xs uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
            {index}. {result.groupName}
          </span>
          <h3 className="truncate text-sm font-semibold text-zinc-900 dark:text-zinc-50">
            {result.promptTitle}
          </h3>
        </div>
        <div className="flex items-center gap-2">
          {isToolRun && (
            <span className="inline-flex items-center rounded-full bg-indigo-100 px-2 py-0.5 text-xs font-medium text-indigo-700 dark:bg-indigo-950 dark:text-indigo-300">
              tools: {result.toolMode}
            </span>
          )}
          <RatingBadge rating={result.rating} />
          <StatusBadge status={result.status} />
        </div>
      </header>

      {canWrite ? (
        <ResultRating
          resultId={result.id}
          rating={result.rating}
          ratingNote={result.ratingNote}
          onChange={(patch) => onRatingChange(result.id, patch)}
        />
      ) : (
        result.ratingNote && (
          <p className="text-xs text-zinc-500 dark:text-zinc-400">{result.ratingNote}</p>
        )
      )}

      <details className="text-sm">
        <summary className="cursor-pointer text-xs font-medium text-zinc-500 hover:text-zinc-800 dark:text-zinc-400 dark:hover:text-zinc-200">
          Prompt &amp; system prompt{isToolRun ? ' & tools' : ''}
        </summary>
        <div className="mt-2 flex flex-col gap-3">
          <div className="flex flex-col gap-1">
            <span className="text-xs font-medium text-zinc-600 dark:text-zinc-400">
              User message
            </span>
            <pre className={preClass}>{result.promptText}</pre>
          </div>
          <div className="flex flex-col gap-1">
            <span className="text-xs font-medium text-zinc-600 dark:text-zinc-400">
              Effective system prompt
            </span>
            <pre className={preClass}>
              {result.systemPromptText ?? '(no system message)'}
            </pre>
          </div>
          {isToolRun && (
            <div className="flex flex-col gap-1">
              <span className="text-xs font-medium text-zinc-600 dark:text-zinc-400">
                Tools offered ({result.toolsSnapshot.length}) · tool_choice{' '}
                {result.toolChoice ?? 'server default'} · max {result.maxTurns} turns
              </span>
              {/* The frozen definitions, exactly as the request carried them. */}
              <pre className={preClass}>
                {result.toolsSnapshot.length === 0
                  ? '(none)'
                  : JSON.stringify(
                      result.toolsSnapshot.map((entry) => entry.definition),
                      null,
                      2,
                    )}
              </pre>
            </div>
          )}
        </div>
      </details>

      {result.error && (
        <div className="rounded-md border border-red-300 bg-red-50 p-3 text-xs text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-300">
          {result.error}
        </div>
      )}

      {/*
        A tool run's answer only makes sense as a conversation, so the
        transcript replaces the single response block and expected output moves
        underneath it rather than beside it.
      */}
      {hasTranscript ? (
        <div className="flex flex-col gap-3">
          <div className="flex flex-col gap-1">
            <span className="text-xs font-medium text-zinc-600 dark:text-zinc-400">
              Conversation
            </span>
            <ToolTranscript
              transcript={result.transcript ?? []}
              turns={result.turns}
              stoppedReason={result.stoppedReason}
            />
          </div>
          {result.expectedOutput && (
            <div className="flex min-w-0 flex-col gap-1">
              <span className="text-xs font-medium text-zinc-600 dark:text-zinc-400">
                Expected output
              </span>
              <pre className={preClass}>{result.expectedOutput}</pre>
            </div>
          )}
        </div>
      ) : result.expectedOutput ? (
        <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
          <div className="flex min-w-0 flex-col gap-1">
            <span className="text-xs font-medium text-zinc-600 dark:text-zinc-400">
              Response
            </span>
            <ResponseBlock text={result.responseText} running={result.status === 'running'} />
          </div>
          <div className="flex min-w-0 flex-col gap-1">
            <span className="text-xs font-medium text-zinc-600 dark:text-zinc-400">
              Expected output
            </span>
            <pre className={preClass}>{result.expectedOutput}</pre>
          </div>
        </div>
      ) : (
        (result.responseText || result.status === 'running') && (
          <div className="flex flex-col gap-1">
            <span className="text-xs font-medium text-zinc-600 dark:text-zinc-400">
              Response
            </span>
            <ResponseBlock text={result.responseText} running={result.status === 'running'} />
          </div>
        )
      )}

      {hasMetrics && (
        <div className="flex flex-wrap gap-2">
          <Chip label="duration" value={formatDuration(result.durationMs)} />
          <Chip label="ttft" value={formatDuration(result.ttftMs)} />
          {tokenLabel && <Chip label="tokens" value={tokenLabel} />}
          <Chip label="speed" value={formatRate(result.tokensPerSec)} />
          {result.turnCount !== null && <Chip label="turns" value={String(result.turnCount)} />}
          {result.toolCallCount !== null && (
            <Chip label="tool calls" value={String(result.toolCallCount)} />
          )}
        </div>
      )}
    </article>
  );
}
