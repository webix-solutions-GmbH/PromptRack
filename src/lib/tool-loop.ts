/**
 * The agentic loop: one `run_results` row, one to N model turns.
 *
 * A tool-free prompt is just the degenerate case — a single turn with no tool
 * definitions — so `run-executor` runs everything through here and keeps one
 * code path for persistence, aborting and error handling.
 */

import {
  computeTokensPerSec,
  streamChatCompletion,
  type ChatMessage,
  type ToolCall,
} from './llm';
import {
  normalizeMaxTurns,
  parseToolArguments,
  snapshotDefinitions,
  type SnapshotTool,
  type ToolChoice,
  type ToolMode,
} from './tools';

/** Metrics for a single model turn. */
export interface TurnMetrics {
  /** 0-based turn number. */
  index: number;
  ttftMs: number | null;
  durationMs: number;
  promptTokens: number | null;
  completionTokens: number;
  tokensEstimated: boolean;
  finishReason: string | null;
  toolCallCount: number;
}

export type StoppedReason = 'stop' | 'max_turns' | 'definitions_only';

/**
 * A persisted message. Mirrors the wire shape, plus display-only annotations
 * (`turn`, tool timing) that are never sent to the model.
 */
export interface TranscriptMessage {
  role: 'system' | 'user' | 'assistant' | 'tool';
  content: string;
  toolCalls?: ToolCall[];
  toolCallId?: string;
  name?: string;
  /** Which model turn produced (or consumed) this message. */
  turn?: number;
  /** Wall time the tool itself took. */
  toolDurationMs?: number;
  /** The tool failed and its error text was fed back to the model. */
  toolIsError?: boolean;
}

export interface ToolRunResult {
  /** Final assistant text — what the existing `response_text` column holds. */
  text: string;
  transcript: TranscriptMessage[];
  turns: TurnMetrics[];
  stoppedReason: StoppedReason;
  // Aggregates, written into the pre-existing metric columns.
  ttftMs: number | null;
  durationMs: number;
  promptTokens: number | null;
  completionTokens: number;
  tokensEstimated: boolean;
  tokensPerSec: number | null;
  toolCallCount: number;
}

/** Outcome of running one tool call. An error is data, not an exception. */
export interface ToolExecutionOutcome {
  content: string;
  isError: boolean;
}

export type ToolExecutor = (
  call: ToolCall,
  signal?: AbortSignal,
) => Promise<ToolExecutionOutcome>;

export interface ToolLoopCallbacks {
  onTurnStart?: (turn: number) => void;
  /** Streaming text of the current turn. Throttling is the caller's business. */
  onDelta?: (turn: number, textSoFar: string) => void;
  onToolCalls?: (turn: number, calls: ToolCall[]) => void;
  onToolResult?: (turn: number, message: TranscriptMessage) => void;
}

export interface RunToolLoopOptions extends ToolLoopCallbacks {
  baseUrl: string;
  apiKey?: string | null;
  model: string;
  params?: Record<string, unknown> | null;
  signal?: AbortSignal;
  systemPrompt?: string | null;
  userMessage: string;
  /** Frozen tool configuration. Empty means a plain one-shot completion. */
  snapshot?: SnapshotTool[];
  toolMode?: ToolMode;
  toolChoice?: ToolChoice | null;
  maxTurns?: number;
  /** Runs one call. Required for `execute` mode, unused otherwise. */
  executeTool?: ToolExecutor;
}

/** Serializes a tool failure into something the model can read and react to. */
function errorPayload(message: string): string {
  return JSON.stringify({ error: message });
}

function parseJsonArray(raw: string | null | undefined): unknown[] {
  if (!raw) return [];
  try {
    const parsed: unknown = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

/** Reads a `transcript_json` column back. Null when the run had no transcript. */
export function parseTranscript(raw: string | null | undefined): TranscriptMessage[] | null {
  if (!raw) return null;

  const messages = parseJsonArray(raw).filter((entry): entry is TranscriptMessage => {
    if (!entry || typeof entry !== 'object') return false;
    const role = (entry as { role?: unknown }).role;
    return (
      role === 'system' ||
      role === 'user' ||
      role === 'assistant' ||
      role === 'tool'
    );
  });

  return messages.length > 0 ? messages : null;
}

/** Reads a `turns_json` column back. */
export function parseTurns(raw: string | null | undefined): TurnMetrics[] {
  return parseJsonArray(raw).filter((entry): entry is TurnMetrics => {
    if (!entry || typeof entry !== 'object') return false;
    return typeof (entry as { index?: unknown }).index === 'number';
  });
}

export async function runToolLoop(options: RunToolLoopOptions): Promise<ToolRunResult> {
  const snapshot = options.snapshot ?? [];
  const definitions = snapshotDefinitions(snapshot);
  const toolMode: ToolMode = definitions.length > 0 ? (options.toolMode ?? 'none') : 'none';
  const active = toolMode !== 'none';
  const maxTurns = active ? normalizeMaxTurns(options.maxTurns) : 1;

  const messages: ChatMessage[] = [];
  const transcript: TranscriptMessage[] = [];

  if (options.systemPrompt && options.systemPrompt.trim().length > 0) {
    messages.push({ role: 'system', content: options.systemPrompt });
    transcript.push({ role: 'system', content: options.systemPrompt });
  }
  messages.push({ role: 'user', content: options.userMessage });
  transcript.push({ role: 'user', content: options.userMessage });

  const turns: TurnMetrics[] = [];
  let stoppedReason: StoppedReason = 'stop';
  let finalText = '';
  let toolCallCount = 0;

  for (let turn = 0; turn < maxTurns; turn += 1) {
    options.onTurnStart?.(turn);

    const metrics = await streamChatCompletion({
      baseUrl: options.baseUrl,
      apiKey: options.apiKey,
      model: options.model,
      messages,
      tools: active ? definitions : null,
      toolChoice: active ? (options.toolChoice ?? null) : null,
      params: options.params,
      signal: options.signal,
      onDelta: (_delta, textSoFar) => options.onDelta?.(turn, textSoFar),
    });

    turns.push({
      index: turn,
      ttftMs: metrics.ttftMs,
      durationMs: metrics.durationMs,
      promptTokens: metrics.promptTokens,
      completionTokens: metrics.completionTokens,
      tokensEstimated: metrics.tokensEstimated,
      finishReason: metrics.finishReason,
      toolCallCount: metrics.toolCalls.length,
    });

    messages.push({
      role: 'assistant',
      content: metrics.text,
      ...(metrics.toolCalls.length > 0 ? { tool_calls: metrics.toolCalls } : {}),
    });
    transcript.push({
      role: 'assistant',
      content: metrics.text,
      turn,
      ...(metrics.toolCalls.length > 0 ? { toolCalls: metrics.toolCalls } : {}),
    });

    // The last assistant text wins, but a turn that only asked for tools must
    // not blank out an answer the model gave alongside its calls.
    if (metrics.text.length > 0) finalText = metrics.text;

    if (metrics.toolCalls.length === 0) {
      stoppedReason = 'stop';
      break;
    }

    toolCallCount += metrics.toolCalls.length;
    options.onToolCalls?.(turn, metrics.toolCalls);

    if (toolMode === 'definitions') {
      stoppedReason = 'definitions_only';
      break;
    }

    // Out of budget: stop before running anything. Executing tools whose
    // results can never reach the model would mean side effects on a real
    // system for nothing.
    if (turn === maxTurns - 1) {
      stoppedReason = 'max_turns';
      break;
    }

    for (const call of metrics.toolCalls) {
      const startedAt = Date.now();
      const outcome = await executeOne(call, snapshot, options);
      const durationMs = Date.now() - startedAt;

      messages.push({
        role: 'tool',
        content: outcome.content,
        tool_call_id: call.id,
        name: call.function.name,
      });
      const message: TranscriptMessage = {
        role: 'tool',
        content: outcome.content,
        toolCallId: call.id,
        name: call.function.name,
        turn,
        toolDurationMs: durationMs,
        toolIsError: outcome.isError,
      };
      transcript.push(message);
      options.onToolResult?.(turn, message);
    }
  }

  return {
    text: finalText,
    transcript,
    turns,
    stoppedReason,
    ...aggregate(turns),
    toolCallCount,
  };
}

/**
 * Dispatches one call.
 *
 * A failure here is never a failed result: the error text goes back to the
 * model as the tool's output, which is exactly what a real agent sees and is
 * itself worth measuring. Only the LLM connection can fail a row.
 */
async function executeOne(
  call: ToolCall,
  snapshot: SnapshotTool[],
  options: RunToolLoopOptions,
): Promise<ToolExecutionOutcome> {
  const entry = snapshot.find((item) => item.definition.function.name === call.function.name);
  if (!entry) {
    return {
      content: errorPayload(
        `The model called "${call.function.name}", which was not one of the tools it was offered.`,
      ),
      isError: true,
    };
  }

  const parsed = parseToolArguments(call.function.arguments);
  if (!parsed.ok) {
    return { content: errorPayload(parsed.error), isError: true };
  }

  if (entry.source === 'manual') {
    // A manual tool with no canned response still has to answer something, or
    // the model is left waiting on a message that never comes.
    return {
      content: entry.mockResponse ?? errorPayload('This tool has no canned response configured.'),
      isError: entry.mockResponse === null,
    };
  }

  if (!options.executeTool) {
    return {
      content: errorPayload('No executor is configured for MCP tools in this run.'),
      isError: true,
    };
  }

  try {
    return await options.executeTool(call, options.signal);
  } catch (err) {
    return {
      content: errorPayload(err instanceof Error ? err.message : 'Tool execution failed.'),
      isError: true,
    };
  }
}

type Aggregates = Pick<
  ToolRunResult,
  'ttftMs' | 'durationMs' | 'promptTokens' | 'completionTokens' | 'tokensEstimated' | 'tokensPerSec'
>;

/**
 * Folds per-turn metrics into the columns that already exist on `run_results`.
 *
 * `durationMs` deliberately sums only the model turns and excludes the time
 * tools spent working — otherwise waiting on a slow ERP would show up as the
 * model being slow. Tool timings live per call in the transcript.
 *
 * The throughput denominator is the sum of each turn's own generation window,
 * so a multi-turn rate stays a real tokens-per-second figure instead of being
 * diluted by later prefills. For a single turn it reduces exactly to what
 * `computeTokensPerSec` produced before.
 */
export function aggregate(turns: TurnMetrics[]): Aggregates {
  if (turns.length === 0) {
    return {
      ttftMs: null,
      durationMs: 0,
      promptTokens: null,
      completionTokens: 0,
      tokensEstimated: true,
      tokensPerSec: null,
    };
  }

  const durationMs = turns.reduce((total, turn) => total + turn.durationMs, 0);
  const completionTokens = turns.reduce((total, turn) => total + turn.completionTokens, 0);
  const generationMs = turns.reduce(
    (total, turn) => total + Math.max(0, turn.durationMs - (turn.ttftMs ?? 0)),
    0,
  );

  const promptTurns = turns.filter((turn) => turn.promptTokens !== null);
  const promptTokens =
    promptTurns.length > 0
      ? promptTurns.reduce((total, turn) => total + (turn.promptTokens ?? 0), 0)
      : null;

  return {
    ttftMs: turns[0].ttftMs,
    durationMs,
    promptTokens,
    completionTokens,
    tokensEstimated: turns.some((turn) => turn.tokensEstimated),
    tokensPerSec: computeTokensPerSec(completionTokens, generationMs, 0),
  };
}
