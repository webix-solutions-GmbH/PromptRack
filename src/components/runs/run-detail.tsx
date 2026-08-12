'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { apiPath } from '@/lib/base-path';
import { formatDateTime, formatDuration, formatRate } from '@/lib/format';
import { countRatings, RATING_META, type Rating } from '@/lib/rating';
import { isRunEvent, type RunEvent, type RunStatus } from '@/lib/run-events';
import type { TranscriptMessage } from '@/lib/tool-loop';
import { ArchiveRunButton } from './archive-run-button';
import { DeleteRunButton } from './delete-run-button';
import { ResultCard } from './result-card';
import { RunComment } from './run-comment';
import { StatusBadge } from './status-badge';
import type { ResultView, RunView } from './types';

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-xs uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
        {label}
      </span>
      <span className="text-sm text-zinc-800 dark:text-zinc-200">{value}</span>
    </div>
  );
}

/**
 * Applies a live event to a tool run's growing transcript.
 *
 * Watching an agent work is most of the value of a tool test, so the transcript
 * is assembled from the stream rather than waiting for the finished row. The
 * authoritative version replaces it on `resultDone`.
 */
function patchTranscript(
  current: TranscriptMessage[] | null,
  patch:
    | { kind: 'turnStart'; turn: number }
    | { kind: 'delta'; turn: number; text: string }
    | { kind: 'toolCall'; turn: number; calls: NonNullable<TranscriptMessage['toolCalls']> }
    | { kind: 'toolResult'; message: TranscriptMessage },
): TranscriptMessage[] {
  const messages = current ? [...current] : [];

  if (patch.kind === 'toolResult') {
    messages.push(patch.message);
    return messages;
  }

  // Find the assistant message of this turn, creating it on first sight.
  let index = messages.findIndex(
    (message) => message.role === 'assistant' && message.turn === patch.turn,
  );
  if (index === -1) {
    messages.push({ role: 'assistant', content: '', turn: patch.turn });
    index = messages.length - 1;
  }

  if (patch.kind === 'delta') {
    messages[index] = { ...messages[index], content: patch.text };
  } else if (patch.kind === 'toolCall') {
    messages[index] = { ...messages[index], toolCalls: patch.calls };
  }

  return messages;
}

function formatParams(params: Record<string, unknown> | null): string {
  if (!params) return 'server defaults';
  const entries = Object.entries(params);
  if (entries.length === 0) return 'server defaults';
  return entries.map(([key, value]) => `${key}=${String(value)}`).join(', ');
}

export function RunDetail({
  run,
  results: initialResults,
  canWrite,
}: {
  run: RunView;
  results: ResultView[];
  canWrite: boolean;
}) {
  // Server-rendered rows seed the view; once the driver starts, its events are
  // the source of truth for this page (every event is already persisted).
  const [results, setResults] = useState(initialResults);
  const [runStatus, setRunStatus] = useState<RunStatus>(run.status);
  const [finishedAt, setFinishedAt] = useState<number | null>(run.finishedAt);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // React Strict Mode mounts effects twice in development; the ref makes the
  // auto-start fire exactly once per real mount.
  const autoStartedRef = useRef(false);
  const abortRef = useRef<AbortController | null>(null);

  const applyEvent = useCallback((event: RunEvent) => {
    switch (event.type) {
      case 'runStart':
        setRunStatus('running');
        break;
      case 'resultStart':
        setResults((current) =>
          current.map((result) =>
            result.id === event.resultId
              ? {
                  ...result,
                  status: 'running',
                  responseText: '',
                  error: null,
                  transcript: null,
                  turns: [],
                  turnCount: null,
                  toolCallCount: null,
                  stoppedReason: null,
                }
              : result,
          ),
        );
        break;
      case 'turnStart':
        setResults((current) =>
          current.map((result) =>
            result.id === event.resultId
              ? {
                  ...result,
                  responseText: '',
                  transcript: patchTranscript(result.transcript, {
                    kind: 'turnStart',
                    turn: event.turn,
                  }),
                }
              : result,
          ),
        );
        break;
      case 'delta':
        setResults((current) =>
          current.map((result) =>
            result.id === event.resultId
              ? {
                  ...result,
                  responseText: event.text,
                  transcript:
                    event.turn === undefined
                      ? result.transcript
                      : patchTranscript(result.transcript, {
                          kind: 'delta',
                          turn: event.turn,
                          text: event.text,
                        }),
                }
              : result,
          ),
        );
        break;
      case 'toolCall':
        setResults((current) =>
          current.map((result) =>
            result.id === event.resultId
              ? {
                  ...result,
                  transcript: patchTranscript(result.transcript, {
                    kind: 'toolCall',
                    turn: event.turn,
                    calls: event.calls,
                  }),
                }
              : result,
          ),
        );
        break;
      case 'toolResult':
        setResults((current) =>
          current.map((result) =>
            result.id === event.resultId
              ? {
                  ...result,
                  transcript: patchTranscript(result.transcript, {
                    kind: 'toolResult',
                    message: event.message,
                  }),
                }
              : result,
          ),
        );
        break;
      case 'resultDone':
        setResults((current) =>
          current.map((result) =>
            result.id === event.resultId
              ? {
                  ...result,
                  status: 'ok',
                  responseText: event.text,
                  error: null,
                  ...event.metrics,
                  // The finished row is authoritative; the live transcript was
                  // only ever an approximation assembled from the stream.
                  transcript: event.transcript ?? result.transcript,
                  turns: event.turns ?? result.turns,
                  stoppedReason: event.stoppedReason ?? result.stoppedReason,
                }
              : result,
          ),
        );
        break;
      case 'resultError':
        setResults((current) =>
          current.map((result) =>
            result.id === event.resultId
              ? { ...result, status: 'error', error: event.error }
              : result,
          ),
        );
        break;
      case 'aborted':
        setResults((current) =>
          current.map((result) =>
            result.id === event.resultId
              ? {
                  ...result,
                  status: 'pending',
                  responseText: null,
                  error: null,
                  transcript: null,
                  turns: [],
                  turnCount: null,
                  toolCallCount: null,
                  stoppedReason: null,
                }
              : result,
          ),
        );
        break;
      case 'runDone':
        setRunStatus(event.status);
        setFinishedAt(event.status === 'pending' ? null : Date.now());
        break;
      case 'runError':
        setError(event.error);
        break;
    }
  }, []);

  const handleRatingChange = useCallback(
    (
      resultId: number,
      patch: { rating?: Rating | null; ratingNote?: string | null },
    ) => {
      setResults((current) =>
        current.map((result) => (result.id === resultId ? { ...result, ...patch } : result)),
      );
    },
    [],
  );

  const start = useCallback(async () => {
    setError(null);
    setRunning(true);

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const response = await fetch(apiPath(`/api/runs/${run.id}/execute`), {
        method: 'POST',
        signal: controller.signal,
      });

      if (response.status === 409) {
        setError('This run is already executing in another tab.');
        return;
      }
      if (!response.ok || !response.body) {
        setError(`Execution request failed (HTTP ${response.status}).`);
        return;
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        const lines = buffer.split('\n');
        buffer = lines.pop() ?? '';
        for (const line of lines) {
          if (line.trim().length === 0) continue;
          try {
            const parsed: unknown = JSON.parse(line);
            if (isRunEvent(parsed)) applyEvent(parsed);
          } catch {
            // Ignore a line we cannot parse rather than killing the stream.
          }
        }
      }
    } catch (err) {
      if (!(err instanceof DOMException && err.name === 'AbortError')) {
        setError(err instanceof Error ? err.message : 'Execution failed.');
      }
    } finally {
      abortRef.current = null;
      setRunning(false);
    }
  }, [applyEvent, run.id]);

  useEffect(() => {
    if (autoStartedRef.current) return;
    autoStartedRef.current = true;
    if (initialResults.some((result) => result.status === 'pending')) {
      // Subscribing to an external system (the NDJSON stream) is exactly what
      // an effect is for; the driver owns run state from here on, and the ref
      // above keeps Strict Mode's double mount from starting it twice.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      void start();
    }
    // Mount-only on purpose: later pending work is resumed via the button.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const pendingCount = results.filter((result) => result.status === 'pending').length;
  // Rows stuck in 'running' while this tab is not driving are leftovers from a
  // crashed process; the executor reclaims them as 'pending' on the next start,
  // so offer Resume for them too (a live run in another tab answers with 409).
  const staleRunningCount = running
    ? 0
    : results.filter((result) => result.status === 'running').length;
  const resumableCount = pendingCount + staleRunningCount;
  const okCount = results.filter((result) => result.status === 'ok').length;
  const errorCount = results.filter((result) => result.status === 'error').length;
  const ratings = countRatings(results.map((result) => result.rating));
  const rates = results
    .map((result) => result.tokensPerSec)
    .filter((rate): rate is number => typeof rate === 'number');
  const avgRate =
    rates.length > 0 ? rates.reduce((total, rate) => total + rate, 0) / rates.length : null;
  const totalDuration = results.reduce(
    (total, result) => total + (result.durationMs ?? 0),
    0,
  );

  return (
    <div className="flex flex-1 flex-col gap-6 p-8">
      <section className="flex flex-col gap-4 rounded-lg border border-zinc-200 p-6 dark:border-zinc-800">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="flex flex-col gap-1">
            <div className="flex items-center gap-3">
              <h1 className="text-2xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
                Run #{run.id}
              </h1>
              <StatusBadge status={runStatus} />
              {run.archivedAt !== null && <StatusBadge status="archived" />}
            </div>
            <p className="font-mono text-sm text-zinc-600 dark:text-zinc-400">
              {run.modelId} @ {run.machineName}
            </p>
          </div>

          <div className="flex items-center gap-2">
            {!canWrite ? null : running ? (
              <button
                type="button"
                onClick={() => abortRef.current?.abort()}
                className="rounded-md border border-zinc-300 px-3 py-2 text-sm font-medium text-zinc-700 transition-colors hover:bg-zinc-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800"
              >
                Stop
              </button>
            ) : (
              <>
                {resumableCount > 0 && (
                  <button
                    type="button"
                    onClick={() => void start()}
                    className="rounded-md bg-zinc-900 px-4 py-2 text-sm font-medium text-zinc-50 transition-colors hover:bg-zinc-700 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-300"
                  >
                    Resume ({resumableCount} pending)
                  </button>
                )}
                <ArchiveRunButton runId={run.id} archived={run.archivedAt !== null} />
                <DeleteRunButton runId={run.id} />
              </>
            )}
          </div>
        </div>

        <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">
          <Field label="Base URL" value={run.baseUrl ?? '—'} />
          <Field label="CPU" value={run.cpu ?? '—'} />
          <Field label="RAM" value={run.ram ?? '—'} />
          <Field label="GPU" value={run.gpu ?? '—'} />
          <Field label="Groups" value={run.groupNames.join(', ') || '—'} />
          <Field label="Params" value={formatParams(run.params)} />
          <Field label="Created" value={formatDateTime(run.createdAt)} />
          <Field label="Finished" value={finishedAt ? formatDateTime(finishedAt) : '—'} />
        </div>

        {run.llmInfo && (
          <details className="border-t border-zinc-200 pt-4 dark:border-zinc-800">
            <summary className="cursor-pointer text-xs font-medium uppercase tracking-wide text-zinc-500 transition-colors hover:text-zinc-700 dark:text-zinc-400 dark:hover:text-zinc-200">
              LLM info
              {run.llmInfo.server &&
                ` — ${run.llmInfo.server}${run.llmInfo.version ? ` ${run.llmInfo.version}` : ''}`}
            </summary>
            <div className="mt-3 grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">
              {run.llmInfo.server && (
                <Field
                  label="Server"
                  value={`${run.llmInfo.server}${run.llmInfo.version ? ` ${run.llmInfo.version}` : ''}`}
                />
              )}
              {Object.entries(run.llmInfo.details).map(([key, value]) => (
                <Field key={key} label={key.replace(/_/g, ' ')} value={value} />
              ))}
            </div>
          </details>
        )}

        <div className="flex flex-wrap items-center gap-3 border-t border-zinc-200 pt-4 text-xs text-zinc-500 dark:border-zinc-800 dark:text-zinc-400">
          <span>{okCount} ok</span>
          <span aria-hidden>·</span>
          <span>{errorCount} error</span>
          <span aria-hidden>·</span>
          <span>{pendingCount} pending</span>
          <span aria-hidden>·</span>
          <span className={RATING_META.good.text}>{ratings.good} good</span>
          <span aria-hidden>·</span>
          <span className={RATING_META.meh.text}>{ratings.meh} meh</span>
          <span aria-hidden>·</span>
          <span className={RATING_META.bad.text}>{ratings.bad} bad</span>
          <span aria-hidden>·</span>
          <span>{ratings.unrated} unrated</span>
          <span aria-hidden>·</span>
          <span>avg {formatRate(avgRate)}</span>
          <span aria-hidden>·</span>
          <span>total {formatDuration(totalDuration)}</span>
        </div>

        <div className="border-t border-zinc-200 pt-4 dark:border-zinc-800">
          <RunComment runId={run.id} comment={run.comment} canWrite={canWrite} />
        </div>

        {error && (
          <p className="text-sm text-red-600 dark:text-red-400" role="alert">
            {error}
          </p>
        )}
      </section>

      <section className="flex flex-col gap-4">
        {results.map((result, index) => (
          <ResultCard
            key={result.id}
            result={result}
            index={index + 1}
            canWrite={canWrite}
            onRatingChange={handleRatingChange}
          />
        ))}
      </section>
    </div>
  );
}
