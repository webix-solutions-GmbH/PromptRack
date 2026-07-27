import { describeFetchError } from './fetch-error';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface ChatMessage {
  role: 'system' | 'user' | 'assistant';
  content: string;
}

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
  /** Time to first *content* token, in ms. Null if nothing was streamed. */
  ttftMs: number | null;
  durationMs: number;
  promptTokens: number | null;
  completionTokens: number;
  /** True when the endpoint sent no usage block and tokens were estimated. */
  tokensEstimated: boolean;
}

export interface StreamChatCompletionOptions {
  baseUrl: string;
  apiKey?: string | null;
  model: string;
  messages: ChatMessage[];
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
  let done = false;

  /**
   * Processes one `data:` payload and returns the usage block it carried, if
   * any. Usage is returned rather than assigned to the closed-over `usage`
   * variable so the caller owns that assignment — TypeScript keeps narrowing a
   * `let` that is only written from inside a callback, which would type the
   * post-loop read as `null`.
   */
  const handleEvent = (payload: string): UsageLike | null => {
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

    // Usage may arrive on a final choices-less chunk (vLLM, LM Studio with
    // stream_options) or piggybacked on the last content chunk (Ollama).
    return readUsage(parsed);
  };

  for await (const chunk of chunks) {
    const result = parseSSEChunk(buffer, chunk);
    buffer = result.buffer;
    for (const event of result.events) {
      usage = handleEvent(event) ?? usage;
      if (done) break;
    }
    if (done) break;
  }

  if (!done && buffer.length > 0) {
    // Stream ended without a trailing newline — flush whatever is left.
    const result = parseSSEChunk(buffer, '\n');
    for (const event of result.events) {
      usage = handleEvent(event) ?? usage;
      if (done) break;
    }
    buffer = '';
  }

  const durationMs = now() - options.startedAt;
  const reportedCompletion = usage?.completionTokens ?? null;
  const tokensEstimated = reportedCompletion === null;

  return {
    text,
    ttftMs,
    durationMs,
    promptTokens: usage?.promptTokens ?? null,
    completionTokens: tokensEstimated
      ? Math.ceil(text.length / 4)
      : (reportedCompletion as number),
    tokensEstimated,
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
