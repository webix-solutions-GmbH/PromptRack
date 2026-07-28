'use client';

import { MarkdownResponse } from '@/components/markdown-response';
import { formatDuration, formatRate } from '@/lib/format';
import { computeTokensPerSec } from '@/lib/llm';
import type { StoppedReason, TranscriptMessage, TurnMetrics } from '@/lib/tool-loop';
import { formatToolArguments } from '@/lib/tools';
import { splitThinking } from '@/lib/thinking';

const preClass =
  'max-h-96 overflow-auto whitespace-pre-wrap break-words rounded-md border border-zinc-200 bg-zinc-50 p-3 font-mono text-xs text-zinc-700 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-300';

const STOPPED_LABELS: Record<StoppedReason, string> = {
  stop: 'the model answered',
  definitions_only: 'definitions only — nothing was executed',
  max_turns: 'turn budget exhausted, still asking for tools',
};

function Chip({ label, value }: { label: string; value: string }) {
  return (
    <span className="inline-flex items-center gap-1 rounded-md border border-zinc-200 px-2 py-0.5 text-xs text-zinc-600 dark:border-zinc-800 dark:text-zinc-400">
      <span className="text-zinc-400 dark:text-zinc-500">{label}</span>
      <span className="font-mono text-zinc-700 dark:text-zinc-300">{value}</span>
    </span>
  );
}

/** Assistant prose, with a leading `<think>` block tucked away as elsewhere. */
function AssistantText({ text }: { text: string }) {
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
      {answer.length > 0 && (
        <div className="max-h-96 overflow-auto rounded-md border border-zinc-200 bg-zinc-50 p-3 text-xs dark:border-zinc-800 dark:bg-zinc-900">
          <MarkdownResponse text={answer} />
        </div>
      )}
    </>
  );
}

function ToolCallBlock({
  name,
  args,
}: {
  name: string;
  args: string;
}) {
  return (
    <div className="flex flex-col gap-1 rounded-md border border-indigo-200 bg-indigo-50 p-3 dark:border-indigo-900 dark:bg-indigo-950/40">
      <span className="text-xs font-medium text-indigo-700 dark:text-indigo-300">
        → calls <span className="font-mono">{name}</span>
      </span>
      <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-words font-mono text-xs text-indigo-900 dark:text-indigo-200">
        {formatToolArguments(args)}
      </pre>
    </div>
  );
}

function ToolResultBlock({ message }: { message: TranscriptMessage }) {
  const failed = message.toolIsError === true;

  return (
    <details
      className={`rounded-md border p-3 ${
        failed
          ? 'border-red-300 bg-red-50 dark:border-red-900 dark:bg-red-950/40'
          : 'border-emerald-200 bg-emerald-50 dark:border-emerald-900 dark:bg-emerald-950/40'
      }`}
    >
      <summary
        className={`cursor-pointer text-xs font-medium ${
          failed
            ? 'text-red-700 dark:text-red-300'
            : 'text-emerald-700 dark:text-emerald-300'
        }`}
      >
        ← <span className="font-mono">{message.name ?? 'tool'}</span> returned
        {failed ? ' an error' : ''}
        {typeof message.toolDurationMs === 'number'
          ? ` · ${formatDuration(message.toolDurationMs)}`
          : ''}
      </summary>
      <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap break-words font-mono text-xs text-zinc-700 dark:text-zinc-300">
        {message.content.length > 0 ? message.content : '(empty)'}
      </pre>
    </details>
  );
}

function TurnHeader({ turn, metrics }: { turn: number; metrics?: TurnMetrics }) {
  return (
    <div className="flex flex-wrap items-center gap-2 pt-1">
      <span className="text-xs font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
        Turn {turn + 1}
      </span>
      {metrics && (
        <>
          <Chip label="ttft" value={formatDuration(metrics.ttftMs)} />
          <Chip label="duration" value={formatDuration(metrics.durationMs)} />
          <Chip
            label="tokens"
            value={`${metrics.tokensEstimated ? '~' : ''}${metrics.completionTokens}${
              metrics.promptTokens !== null ? ` / ${metrics.promptTokens} in` : ''
            }`}
          />
          <Chip
            label="speed"
            value={formatRate(
              computeTokensPerSec(metrics.completionTokens, metrics.durationMs, metrics.ttftMs),
            )}
          />
          {metrics.finishReason && <Chip label="finish" value={metrics.finishReason} />}
        </>
      )}
    </div>
  );
}

/**
 * The conversation a tool run actually had: assistant turn, the calls it asked
 * for, what each tool returned, and the metrics of every turn.
 *
 * Rendered from the stored transcript rather than re-derived, so it shows what
 * happened even after the toolset behind it has been edited or deleted.
 */
export function ToolTranscript({
  transcript,
  turns,
  stoppedReason,
}: {
  transcript: TranscriptMessage[];
  turns: TurnMetrics[];
  stoppedReason: StoppedReason | null;
}) {
  // The system and user messages are already shown in the card's prompt block.
  const conversation = transcript.filter(
    (message) => message.role === 'assistant' || message.role === 'tool',
  );

  if (conversation.length === 0) {
    return <pre className={preClass}>—</pre>;
  }

  const turnByIndex = new Map(turns.map((turn) => [turn.index, turn]));

  // Decide where the turn headers go in one pass up front — a "have I already
  // shown this turn?" counter cannot be threaded through the render itself.
  const rows = conversation.map((message, index) => {
    const turn = message.turn ?? 0;
    const previous = conversation
      .slice(0, index)
      .findLast((earlier) => earlier.role === 'assistant');

    return {
      message,
      turn,
      showHeader: message.role === 'assistant' && (previous?.turn ?? -1) !== turn,
    };
  });

  return (
    <div className="flex flex-col gap-3">
      {rows.map(({ message, turn, showHeader }, index) => {
        return (
          <div key={index} className="flex flex-col gap-2">
            {showHeader && <TurnHeader turn={turn} metrics={turnByIndex.get(turn)} />}

            {message.role === 'assistant' ? (
              <>
                {message.content.length > 0 && <AssistantText text={message.content} />}
                {(message.toolCalls ?? []).map((call) => (
                  <ToolCallBlock
                    key={call.id}
                    name={call.function.name}
                    args={call.function.arguments}
                  />
                ))}
                {message.content.length === 0 && (message.toolCalls ?? []).length === 0 && (
                  <pre className={preClass}>(empty turn)</pre>
                )}
              </>
            ) : (
              <ToolResultBlock message={message} />
            )}
          </div>
        );
      })}

      {stoppedReason !== null && (
        <p className="text-xs text-zinc-500 dark:text-zinc-400">
          Stopped: {STOPPED_LABELS[stoppedReason]}
        </p>
      )}
    </div>
  );
}
