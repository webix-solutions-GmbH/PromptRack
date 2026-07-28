/**
 * Wire format for the NDJSON stream produced by
 * `POST /api/runs/[id]/execute`.
 *
 * Kept free of any server-only imports so client components can use these
 * types (and the type guard) without pulling the database into the bundle —
 * the imports below are type-only and erase at compile time.
 */

import type { StoppedReason, TranscriptMessage, TurnMetrics } from './tool-loop';
import type { ToolCall } from './llm';

export type RunStatus = 'pending' | 'running' | 'completed' | 'failed';
export type RunResultStatus = 'pending' | 'running' | 'ok' | 'error';

export interface RunResultMetrics {
  durationMs: number | null;
  ttftMs: number | null;
  promptTokens: number | null;
  completionTokens: number | null;
  tokensPerSec: number | null;
  tokensEstimated: boolean;
  /** Null for the classic one-shot path; set for tool runs. */
  turnCount?: number | null;
  toolCallCount?: number | null;
}

export type RunEvent =
  | { type: 'runStart'; runId: number; pending: number; total: number }
  | { type: 'resultStart'; resultId: number; index: number; total: number }
  /** A new model turn began. Only emitted for tool runs. */
  | { type: 'turnStart'; resultId: number; turn: number }
  /** Throttled progress update. `text` is the full response of `turn` so far. */
  | { type: 'delta'; resultId: number; text: string; turn?: number }
  /** The model asked to call tools. */
  | { type: 'toolCall'; resultId: number; turn: number; calls: ToolCall[] }
  /** One tool finished and its output was fed back. */
  | {
      type: 'toolResult';
      resultId: number;
      turn: number;
      message: TranscriptMessage;
    }
  | {
      type: 'resultDone';
      resultId: number;
      text: string;
      metrics: RunResultMetrics;
      /** Present for tool runs so the card can render without a reload. */
      transcript?: TranscriptMessage[];
      turns?: TurnMetrics[];
      stoppedReason?: StoppedReason;
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
