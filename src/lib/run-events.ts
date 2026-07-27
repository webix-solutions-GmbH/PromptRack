/**
 * Wire format for the NDJSON stream produced by
 * `POST /api/runs/[id]/execute`.
 *
 * Kept free of any server-only imports so client components can use these
 * types (and the type guard) without pulling the database into the bundle.
 */

export type RunStatus = 'pending' | 'running' | 'completed' | 'failed';
export type RunResultStatus = 'pending' | 'running' | 'ok' | 'error';

export interface RunResultMetrics {
  durationMs: number | null;
  ttftMs: number | null;
  promptTokens: number | null;
  completionTokens: number | null;
  tokensPerSec: number | null;
  tokensEstimated: boolean;
}

export type RunEvent =
  | { type: 'runStart'; runId: number; pending: number; total: number }
  | { type: 'resultStart'; resultId: number; index: number; total: number }
  /** Throttled progress update. `text` is the full response so far. */
  | { type: 'delta'; resultId: number; text: string }
  | {
      type: 'resultDone';
      resultId: number;
      text: string;
      metrics: RunResultMetrics;
    }
  | { type: 'resultError'; resultId: number; error: string }
  /** The HTTP request was cancelled; `resultId` was reset to pending. */
  | { type: 'aborted'; resultId: number | null }
  | {
      type: 'runDone';
      runId: number;
      status: RunStatus;
      nothingPending?: boolean;
    }
  /** Execution could not start / crashed outside of a single result. */
  | { type: 'runError'; runId: number; error: string };

export function isRunEvent(value: unknown): value is RunEvent {
  return (
    typeof value === 'object' &&
    value !== null &&
    typeof (value as { type?: unknown }).type === 'string'
  );
}
