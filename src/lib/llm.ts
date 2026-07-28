import { describeFetchError } from './fetch-error';
import type { ToolChoice, ToolDefinition } from './tools';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/** One function call requested by the model, as it goes back on the wire. */
export interface ToolCall {
  id: string;
  type: 'function';
  function: { name: string; arguments: string };
}

export type ChatMessage =
  | { role: 'system' | 'user'; content: string }
  | { role: 'assistant'; content: string; tool_calls?: ToolCall[] }
  | { role: 'tool'; content: string; tool_call_id: string; name?: string };

export type LlmErrorKind =
  /** The endpoint could not be reached at all (DNS, refused, reset, ...). */
  | 'connection'
  /** The request exceeded `timeoutMs`. */
  | 'timeout'
  /** The endpoint answered with a non-2xx status. */
  | 'http'
  /** The endpoint answered, but the stream was malformed or reported an error. */
  | 'stream'
  /** The caller aborted the request through `signal`. */
  | 'aborted';

export class LlmError extends Error {
  readonly kind: LlmErrorKind;
  readonly status?: number;

  constructor(message: string, kind: LlmErrorKind, status?: number) {
    super(message);
    this.name = 'LlmError';
    this.kind = kind;
    this.status = status;
  }

  /**
   * True when the failure means "the machine was not reachable" rather than
   * "the model/request failed". Used to mark a whole run as failed.
   */
  get isConnectionLevel(): boolean {
    return this.kind === 'connection' || this.kind === 'timeout';
  }
}

export interface StreamMetrics {
  text: string;
  /**
   * Time to the first piece of output, in ms — a content token or the first
   * fragment of a tool call. Null if nothing was streamed.
   */
  ttftMs: number | null;
  durationMs: number;
  promptTokens: number | null;
  completionTokens: number;
  /** True when the endpoint sent no usage block and tokens were estimated. */
  tokensEstimated: boolean;
  /** Function calls the model asked for, in the order it emitted them. */
  toolCalls: ToolCall[];
  /** `stop`, `tool_calls`, `length`, ... as reported by the endpoint. */
  finishReason: string | null;
}

export interface StreamChatCompletionOptions {
  baseUrl: string;
  apiKey?: string | null;
  model: string;
  messages: ChatMessage[];
  /** Tool definitions to offer. Omitted from the request when empty. */
  tools?: ToolDefinition[] | null;
  /** Omitted from the request when null — the server keeps its own default. */
  toolChoice?: ToolChoice | null;
  /** Extra body fields merged into the request (temperature, max_tokens, ...). */
  params?: Record<string, unknown> | null;
  /** Hard limit for the whole request. Defaults to 5 minutes. */
  timeoutMs?: number;
  onDelta?: (delta: string, textSoFar: string) => void;
  signal?: AbortSignal;
}

export const DEFAULT_TIMEOUT_MS = 300_000;
export const DONE_SENTINEL = '[DONE]';

// ---------------------------------------------------------------------------
// SSE parsing (pure, unit-testable)
// ---------------------------------------------------------------------------

export interface SSEParseResult {
  /** Payloads of complete `data:` lines found in this chunk. */
  events: string[];
  /** Trailing partial line that must be prepended to the next chunk. */
  buffer: string;
}

/**
 * Feeds one network chunk into the SSE line parser.
 *
 * Network reads can split a line anywhere (even mid-JSON), so the unfinished
 * tail is handed back as `buffer` and prepended to the next call. Comment
 * lines (`: ping`) and non-`data:` fields are ignored.
 */
export function parseSSEChunk(buffer: string, chunk: string): SSEParseResult {
  const lines = (buffer + chunk).split('\n');
  const rest = lines.pop() ?? '';
  const events: string[] = [];

  for (const rawLine of lines) {
    const line = rawLine.endsWith('\r') ? rawLine.slice(0, -1) : rawLine;
    if (line.length === 0 || line.startsWith(':')) continue;
    if (line.startsWith('data:')) {
      events.push(line.slice(5).trim());
    }
  }

  return { events, buffer: rest };
}

interface UsageLike {
  promptTokens: number | null;
  completionTokens: number | null;
}

function readUsage(payload: unknown): UsageLike | null {
  if (!payload || typeof payload !== 'object') return null;
  const usage = (payload as { usage?: unknown }).usage;
  if (!usage || typeof usage !== 'object') return null;

  const prompt = (usage as { prompt_tokens?: unknown }).prompt_tokens;
  const completion = (usage as { completion_tokens?: unknown }).completion_tokens;

  return {
    promptTokens: typeof prompt === 'number' ? prompt : null,
    completionTokens: typeof completion === 'number' ? completion : null,
  };
}

function readDeltaContent(payload: unknown): string | null {
  if (!payload || typeof payload !== 'object') return null;
  const choices = (payload as { choices?: unknown }).choices;
  if (!Array.isArray(choices) || choices.length === 0) return null;

  const first = choices[0];
  if (!first || typeof first !== 'object') return null;

  const delta = (first as { delta?: unknown }).delta;
  if (!delta || typeof delta !== 'object') return null;

  const content = (delta as { content?: unknown }).content;
  return typeof content === 'string' && content.length > 0 ? content : null;
}

/** One `delta.tool_calls[]` entry, before fragments are stitched together. */
interface ToolCallFragment {
  /** Slot the fragment belongs to. Null when the endpoint omits `index`. */
  index: number | null;
  id: string | null;
  name: string | null;
  argumentsFragment: string | null;
}

function readDeltaToolCalls(payload: unknown): ToolCallFragment[] {
  const choices = (payload as { choices?: unknown }).choices;
  if (!Array.isArray(choices) || choices.length === 0) return [];

  const first = choices[0];
  if (!first || typeof first !== 'object') return [];

  const delta = (first as { delta?: unknown }).delta;
  if (!delta || typeof delta !== 'object') return [];

  const entries = (delta as { tool_calls?: unknown }).tool_calls;
  if (!Array.isArray(entries)) return [];

  const fragments: ToolCallFragment[] = [];
  for (const entry of entries) {
    if (!entry || typeof entry !== 'object') continue;

    const index = (entry as { index?: unknown }).index;
    const id = (entry as { id?: unknown }).id;
    const fn = (entry as { function?: unknown }).function;
    const name =
      fn && typeof fn === 'object' ? (fn as { name?: unknown }).name : undefined;
    const args =
      fn && typeof fn === 'object' ? (fn as { arguments?: unknown }).arguments : undefined;

    fragments.push({
      index: typeof index === 'number' ? index : null,
      id: typeof id === 'string' && id.length > 0 ? id : null,
      name: typeof name === 'string' && name.length > 0 ? name : null,
      argumentsFragment: typeof args === 'string' ? args : null,
    });
  }
  return fragments;
}

interface PartialToolCall {
  index: number;
  id: string | null;
  name: string;
  arguments: string;
}

/**
 * Stitches streamed `tool_calls` fragments back into whole calls.
 *
 * Endpoints differ as much here as they do over usage. vLLM streams one entry
 * per call keyed by `index`, with `function.arguments` arriving as string
 * fragments that can be split anywhere — including mid-JSON. Others send a
 * finished call in a single chunk, and a few omit `index` entirely, in which
 * case the call's `id` (or arrival order) has to stand in for it.
 */
class ToolCallAccumulator {
  private readonly slots = new Map<number, PartialToolCall>();
  private readonly indexById = new Map<string, number>();

  get size(): number {
    return this.slots.size;
  }

  add(fragment: ToolCallFragment): void {
    const index = this.slotFor(fragment);
    let slot = this.slots.get(index);
    if (!slot) {
      slot = { index, id: null, name: '', arguments: '' };
      this.slots.set(index, slot);
    }

    if (fragment.id !== null) {
      slot.id = fragment.id;
      this.indexById.set(fragment.id, index);
    }
    // A name can also arrive in fragments, so append rather than replace.
    if (fragment.name !== null) slot.name += fragment.name;
    if (fragment.argumentsFragment !== null) slot.arguments += fragment.argumentsFragment;
  }

  private slotFor(fragment: ToolCallFragment): number {
    if (fragment.index !== null) return fragment.index;
    if (fragment.id !== null) {
      const known = this.indexById.get(fragment.id);
      if (known !== undefined) return known;
      return this.slots.size;
    }
    // No index and no id: the only sane reading is "the call in flight".
    return this.slots.size === 0 ? 0 : this.slots.size - 1;
  }

  /** Materializes the calls in index order, synthesizing any missing ids. */
  toToolCalls(): ToolCall[] {
    return [...this.slots.values()]
      .sort((a, b) => a.index - b.index)
      .filter((slot) => slot.name.length > 0)
      .map((slot) => ({
        id: slot.id ?? `call_${slot.index}`,
        type: 'function' as const,
        function: { name: slot.name, arguments: slot.arguments },
      }));
  }
}

/** Characters a tool call contributes when tokens have to be estimated. */
function toolCallChars(calls: ToolCall[]): number {
  return calls.reduce(
    (total, call) => total + call.function.name.length + call.function.arguments.length,
    0,
  );
}

function readFinishReason(payload: unknown): string | null {
  const choices = (payload as { choices?: unknown }).choices;
  if (!Array.isArray(choices) || choices.length === 0) return null;

  const first = choices[0];
  if (!first || typeof first !== 'object') return null;

  const reason = (first as { finish_reason?: unknown }).finish_reason;
  return typeof reason === 'string' && reason.length > 0 ? reason : null;
}

function readStreamedError(payload: unknown): string | null {
  if (!payload || typeof payload !== 'object') return null;
  const error = (payload as { error?: unknown }).error;
  if (!error) return null;
  if (typeof error === 'string') return error;
  if (typeof error === 'object') {
    const message = (error as { message?: unknown }).message;
    if (typeof message === 'string') return message;
  }
  return 'The server reported an error mid-stream.';
}

export interface ConsumeStreamOptions {
  /** Wall clock reading taken right before the request was sent. */
  startedAt: number;
  now?: () => number;
  onDelta?: (delta: string, textSoFar: string) => void;
}

/**
 * Consumes an OpenAI-compatible chat-completions SSE stream and measures it.
 *
 * Deliberately decoupled from `fetch` so tests can feed recorded chunk
 * sequences (vLLM / Ollama / LM Studio all differ slightly in where they put
 * the usage block, and some never send one at all).
 */
export async function consumeChatCompletionStream(
  chunks: AsyncIterable<string> | Iterable<string>,
  options: ConsumeStreamOptions,
): Promise<StreamMetrics> {
  const now = options.now ?? (() => Date.now());

  let buffer = '';
  let text = '';
  let ttftMs: number | null = null;
  let usage: UsageLike | null = null;
  let finishReason: string | null = null;
  let done = false;
  const toolCalls = new ToolCallAccumulator();

  /** What a single `data:` payload contributed beyond text and tool calls. */
  interface EventOutcome {
    usage: UsageLike | null;
    finishReason: string | null;
  }

  /**
   * Processes one `data:` payload. Usage and finish reason are returned rather
   * than assigned to the closed-over variables so the caller owns those
   * assignments — TypeScript keeps narrowing a `let` that is only written from
   * inside a callback, which would type the post-loop read as `null`.
   */
  const handleEvent = (payload: string): EventOutcome | null => {
    if (payload === DONE_SENTINEL) {
      done = true;
      return null;
    }
    if (payload.length === 0) return null;

    let parsed: unknown;
    try {
      parsed = JSON.parse(payload);
    } catch {
      // Ignore keep-alives and anything that is not JSON — a malformed line
      // should not throw away an otherwise good response.
      return null;
    }

    const streamedError = readStreamedError(parsed);
    if (streamedError !== null) {
      throw new LlmError(streamedError, 'stream');
    }

    const content = readDeltaContent(parsed);
    if (content !== null) {
      if (ttftMs === null) {
        ttftMs = now() - options.startedAt;
      }
      text += content;
      options.onDelta?.(content, text);
    }

    // A tool-call-only response never streams content, so the first fragment of
    // a call is what TTFT has to measure in that case.
    for (const fragment of readDeltaToolCalls(parsed)) {
      if (ttftMs === null) {
        ttftMs = now() - options.startedAt;
      }
      toolCalls.add(fragment);
    }

    // Usage may arrive on a final choices-less chunk (vLLM, LM Studio with
    // stream_options) or piggybacked on the last content chunk (Ollama).
    return { usage: readUsage(parsed), finishReason: readFinishReason(parsed) };
  };

  // Both loops assign `usage`/`finishReason` inline rather than through a
  // helper: an assignment made inside another closure would put TypeScript's
  // narrowing right back where the `handleEvent` return value avoids it.
  for await (const chunk of chunks) {
    const result = parseSSEChunk(buffer, chunk);
    buffer = result.buffer;
    for (const event of result.events) {
      const outcome = handleEvent(event);
      if (outcome) {
        usage = outcome.usage ?? usage;
        finishReason = outcome.finishReason ?? finishReason;
      }
      if (done) break;
    }
    if (done) break;
  }

  if (!done && buffer.length > 0) {
    // Stream ended without a trailing newline — flush whatever is left.
    const result = parseSSEChunk(buffer, '\n');
    for (const event of result.events) {
      const outcome = handleEvent(event);
      if (outcome) {
        usage = outcome.usage ?? usage;
        finishReason = outcome.finishReason ?? finishReason;
      }
      if (done) break;
    }
    buffer = '';
  }

  const durationMs = now() - options.startedAt;
  const reportedCompletion = usage?.completionTokens ?? null;
  const tokensEstimated = reportedCompletion === null;
  const calls = toolCalls.toToolCalls();

  return {
    text,
    ttftMs,
    durationMs,
    promptTokens: usage?.promptTokens ?? null,
    completionTokens: tokensEstimated
      ? Math.ceil((text.length + toolCallChars(calls)) / 4)
      : (reportedCompletion as number),
    tokensEstimated,
    toolCalls: calls,
    finishReason,
  };
}

// ---------------------------------------------------------------------------
// Metrics math
// ---------------------------------------------------------------------------

/**
 * Generation throughput: completion tokens divided by the time spent
 * *generating* (total duration minus the time-to-first-token prefill).
 * Returns null whenever the numbers cannot produce a meaningful rate.
 */
export function computeTokensPerSec(
  completionTokens: number | null | undefined,
  durationMs: number | null | undefined,
  ttftMs: number | null | undefined,
): number | null {
  if (typeof completionTokens !== 'number' || !Number.isFinite(completionTokens)) return null;
  if (completionTokens <= 0) return null;
  if (typeof durationMs !== 'number' || !Number.isFinite(durationMs)) return null;

  const generationMs = durationMs - (typeof ttftMs === 'number' ? ttftMs : 0);
  if (!Number.isFinite(generationMs) || generationMs <= 0) return null;

  const rate = completionTokens / (generationMs / 1000);
  return Number.isFinite(rate) ? rate : null;
}

// ---------------------------------------------------------------------------
// HTTP client
// ---------------------------------------------------------------------------

const CONNECTION_CODES = new Set([
  'ECONNREFUSED',
  'ENOTFOUND',
  'ETIMEDOUT',
  'ECONNRESET',
  'EHOSTUNREACH',
  'ENETUNREACH',
  'EAI_AGAIN',
  'UND_ERR_SOCKET',
  'UND_ERR_CONNECT_TIMEOUT',
]);

function classify(err: unknown, externalSignal?: AbortSignal): LlmError {
  if (err instanceof LlmError) return err;

  if (externalSignal?.aborted) {
    return new LlmError('Request aborted.', 'aborted');
  }

  if (err instanceof Error) {
    if (err.name === 'TimeoutError') {
      return new LlmError('Request timed out.', 'timeout');
    }
    if (err.name === 'AbortError') {
      return new LlmError('Request aborted.', 'aborted');
    }

    const cause = (err as { cause?: unknown }).cause;
    const code =
      cause && typeof cause === 'object' && 'code' in cause
        ? (cause as { code?: unknown }).code
        : undefined;

    if (typeof code === 'string' && CONNECTION_CODES.has(code)) {
      return new LlmError(describeFetchError(err), 'connection');
    }
    if (err instanceof TypeError) {
      // undici surfaces every transport failure as `TypeError: fetch failed`.
      return new LlmError(describeFetchError(err), 'connection');
    }

    return new LlmError(err.message, 'stream');
  }

  return new LlmError('Unknown error.', 'stream');
}

async function* readTextChunks(body: ReadableStream<Uint8Array>): AsyncGenerator<string> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      if (value) yield decoder.decode(value, { stream: true });
    }
    const tail = decoder.decode();
    if (tail.length > 0) yield tail;
  } finally {
    reader.releaseLock();
  }
}

function excerpt(body: string, max = 400): string {
  const trimmed = body.trim().replace(/\s+/g, ' ');
  if (trimmed.length === 0) return '(empty response body)';
  return trimmed.length > max ? `${trimmed.slice(0, max)}…` : trimmed;
}

/**
 * Streams a chat completion from an OpenAI-compatible endpoint and returns the
 * accumulated text plus timing/token metrics. Raw `fetch`, no SDK.
 */
export async function streamChatCompletion(
  opts: StreamChatCompletionOptions,
): Promise<StreamMetrics> {
  const baseUrl = opts.baseUrl.trim().replace(/\/+$/, '');
  const url = `${baseUrl}/chat/completions`;
  const timeoutMs = opts.timeoutMs ?? DEFAULT_TIMEOUT_MS;

  const controller = new AbortController();
  const abortWithTimeout = () =>
    controller.abort(new DOMException('Request timed out.', 'TimeoutError'));
  const abortExternal = () =>
    controller.abort(new DOMException('Request aborted.', 'AbortError'));

  const timeout = setTimeout(abortWithTimeout, timeoutMs);
  if (opts.signal) {
    if (opts.signal.aborted) abortExternal();
    else opts.signal.addEventListener('abort', abortExternal, { once: true });
  }

  const startedAt = Date.now();

  try {
    let response: Response;
    try {
      response = await fetch(url, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Accept: 'text/event-stream',
          ...(opts.apiKey ? { Authorization: `Bearer ${opts.apiKey}` } : {}),
        },
        body: JSON.stringify({
          model: opts.model,
          messages: opts.messages,
          stream: true,
          stream_options: { include_usage: true },
          // Sending an empty `tools` array makes some servers unhappy, and
          // `tool_choice` is meaningless without it — omit both unless asked.
          ...(opts.tools && opts.tools.length > 0
            ? {
                tools: opts.tools,
                ...(opts.toolChoice ? { tool_choice: opts.toolChoice } : {}),
              }
            : {}),
          ...(opts.params ?? {}),
        }),
        signal: controller.signal,
      });
    } catch (err) {
      throw classify(err, opts.signal);
    }

    if (!response.ok) {
      let body = '';
      try {
        body = await response.text();
      } catch {
        body = '';
      }
      const hint =
        response.status === 401 || response.status === 403
          ? ' (unauthorized — check the API key)'
          : '';
      throw new LlmError(
        `HTTP ${response.status} ${response.statusText}${hint} — ${excerpt(body)}`,
        'http',
        response.status,
      );
    }

    if (!response.body) {
      throw new LlmError('The server returned an empty response body.', 'stream');
    }

    try {
      return await consumeChatCompletionStream(readTextChunks(response.body), {
        startedAt,
        onDelta: opts.onDelta,
      });
    } catch (err) {
      throw classify(err, opts.signal);
    }
  } finally {
    clearTimeout(timeout);
    opts.signal?.removeEventListener('abort', abortExternal);
  }
}
