import { describe, expect, it } from 'vitest';
import {
  computeTokensPerSec,
  consumeChatCompletionStream,
  parseSSEChunk,
} from './llm';

// ---------------------------------------------------------------------------
// Fixture helpers
// ---------------------------------------------------------------------------

function sse(payload: unknown): string {
  return `data: ${JSON.stringify(payload)}\n\n`;
}

function contentChunk(content: string, extra: Record<string, unknown> = {}): string {
  return sse({
    id: 'chatcmpl-1',
    object: 'chat.completion.chunk',
    model: 'test-model',
    choices: [{ index: 0, delta: { content }, finish_reason: null }],
    ...extra,
  });
}

const DONE = 'data: [DONE]\n\n';

/**
 * Turns a fixture into an async iterable while advancing a fake clock by
 * `stepMs` before each chunk is yielded, so timings are deterministic.
 */
function streamOf(chunks: string[], stepMs = 100) {
  const clock = { t: 0 };
  async function* gen() {
    for (const chunk of chunks) {
      clock.t += stepMs;
      yield chunk;
    }
  }
  return { chunks: gen(), now: () => clock.t, clock };
}

// ---------------------------------------------------------------------------
// parseSSEChunk
// ---------------------------------------------------------------------------

describe('parseSSEChunk', () => {
  it('extracts complete data lines and keeps the trailing partial line', () => {
    const result = parseSSEChunk('', 'data: {"a":1}\n\ndata: {"b":2');
    expect(result.events).toEqual(['{"a":1}']);
    expect(result.buffer).toBe('data: {"b":2');
  });

  it('joins a line that was split across two reads', () => {
    const first = parseSSEChunk('', 'data: {"hel');
    expect(first.events).toEqual([]);
    const second = parseSSEChunk(first.buffer, 'lo":"world"}\n');
    expect(second.events).toEqual(['{"hello":"world"}']);
    expect(second.buffer).toBe('');
  });

  it('ignores comments, blank lines and non-data fields', () => {
    const result = parseSSEChunk('', ': keep-alive\n\nevent: ping\nid: 7\ndata: {"x":1}\n');
    expect(result.events).toEqual(['{"x":1}']);
  });

  it('handles CRLF line endings', () => {
    const result = parseSSEChunk('', 'data: {"x":1}\r\n\r\n');
    expect(result.events).toEqual(['{"x":1}']);
  });

  it('recognises the [DONE] sentinel as a normal data payload', () => {
    expect(parseSSEChunk('', 'data: [DONE]\n\n').events).toEqual(['[DONE]']);
  });
});

// ---------------------------------------------------------------------------
// consumeChatCompletionStream
// ---------------------------------------------------------------------------

describe('consumeChatCompletionStream', () => {
  it('handles the vLLM shape: content chunks then a choices-less usage chunk', async () => {
    const fixture = [
      sse({ choices: [{ index: 0, delta: { role: 'assistant' }, finish_reason: null }] }),
      contentChunk('Hello'),
      contentChunk(' world'),
      sse({ choices: [{ index: 0, delta: {}, finish_reason: 'stop' }] }),
      sse({ choices: [], usage: { prompt_tokens: 11, completion_tokens: 7, total_tokens: 18 } }),
      DONE,
    ];
    const { chunks, now } = streamOf(fixture);

    const result = await consumeChatCompletionStream(chunks, { startedAt: 0, now });

    expect(result.text).toBe('Hello world');
    expect(result.promptTokens).toBe(11);
    expect(result.completionTokens).toBe(7);
    expect(result.tokensEstimated).toBe(false);
    // First *content* chunk is the second fixture entry → clock at 200ms.
    expect(result.ttftMs).toBe(200);
    expect(result.durationMs).toBe(600);
  });

  it('handles the Ollama shape: usage on the last content chunk, no usage-only chunk', async () => {
    const fixture = [
      contentChunk('The '),
      contentChunk('answer'),
      contentChunk('.', {
        usage: { prompt_tokens: 25, completion_tokens: 3, total_tokens: 28 },
      }),
      DONE,
    ];
    const { chunks, now } = streamOf(fixture);

    const result = await consumeChatCompletionStream(chunks, { startedAt: 0, now });

    expect(result.text).toBe('The answer.');
    expect(result.promptTokens).toBe(25);
    expect(result.completionTokens).toBe(3);
    expect(result.tokensEstimated).toBe(false);
    expect(result.ttftMs).toBe(100);
  });

  it('reassembles payloads split across network reads', async () => {
    const full = contentChunk('split across reads');
    const cut1 = Math.floor(full.length / 3);
    const cut2 = Math.floor((full.length * 2) / 3);
    const fixture = [
      full.slice(0, cut1),
      full.slice(cut1, cut2),
      full.slice(cut2),
      sse({ choices: [], usage: { prompt_tokens: 4, completion_tokens: 4 } }),
      DONE,
    ];
    const { chunks, now } = streamOf(fixture);

    const result = await consumeChatCompletionStream(chunks, { startedAt: 0, now });

    expect(result.text).toBe('split across reads');
    expect(result.completionTokens).toBe(4);
    // TTFT only counts once the whole line has arrived (third read → 300ms).
    expect(result.ttftMs).toBe(300);
  });

  it('estimates completion tokens when no usage block is sent', async () => {
    const text = 'a'.repeat(41);
    const { chunks, now } = streamOf([contentChunk(text), DONE]);

    const result = await consumeChatCompletionStream(chunks, { startedAt: 0, now });

    expect(result.tokensEstimated).toBe(true);
    expect(result.completionTokens).toBe(Math.ceil(41 / 4));
    expect(result.promptTokens).toBeNull();
  });

  it('stops at [DONE] and ignores anything after it', async () => {
    const { chunks, now } = streamOf([
      contentChunk('kept'),
      DONE,
      contentChunk(' dropped'),
    ]);

    const result = await consumeChatCompletionStream(chunks, { startedAt: 0, now });

    expect(result.text).toBe('kept');
  });

  it('flushes a final line that has no trailing newline', async () => {
    const { chunks, now } = streamOf(['data: {"choices":[{"delta":{"content":"tail"}}]}']);

    const result = await consumeChatCompletionStream(chunks, { startedAt: 0, now });

    expect(result.text).toBe('tail');
  });

  it('forwards deltas with the running text so far', async () => {
    const seen: Array<[string, string]> = [];
    const { chunks, now } = streamOf([contentChunk('a'), contentChunk('b'), DONE]);

    await consumeChatCompletionStream(chunks, {
      startedAt: 0,
      now,
      onDelta: (delta, soFar) => seen.push([delta, soFar]),
    });

    expect(seen).toEqual([
      ['a', 'a'],
      ['b', 'ab'],
    ]);
  });

  it('skips malformed JSON lines instead of failing the whole response', async () => {
    const { chunks, now } = streamOf([
      'data: {not json}\n\n',
      contentChunk('still here'),
      DONE,
    ]);

    const result = await consumeChatCompletionStream(chunks, { startedAt: 0, now });

    expect(result.text).toBe('still here');
  });

  it('throws when the stream carries an error payload', async () => {
    const { chunks, now } = streamOf([
      sse({ error: { message: 'model not loaded' } }),
      DONE,
    ]);

    await expect(
      consumeChatCompletionStream(chunks, { startedAt: 0, now }),
    ).rejects.toThrow('model not loaded');
  });

  it('reports no ttft and empty text when nothing was streamed', async () => {
    const { chunks, now } = streamOf([DONE]);

    const result = await consumeChatCompletionStream(chunks, { startedAt: 0, now });

    expect(result.text).toBe('');
    expect(result.ttftMs).toBeNull();
    expect(result.completionTokens).toBe(0);
    expect(result.tokensEstimated).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// computeTokensPerSec
// ---------------------------------------------------------------------------

describe('computeTokensPerSec', () => {
  it('divides completion tokens by the generation window (duration minus ttft)', () => {
    // 100 tokens generated in 2000ms - 500ms = 1500ms → 66.67 tok/s
    expect(computeTokensPerSec(100, 2000, 500)).toBeCloseTo(66.6667, 3);
  });

  it('uses the full duration when ttft is unknown', () => {
    expect(computeTokensPerSec(50, 1000, null)).toBeCloseTo(50, 6);
  });

  it('returns null when the generation window is zero', () => {
    expect(computeTokensPerSec(10, 500, 500)).toBeNull();
  });

  it('returns null when ttft exceeds the duration', () => {
    expect(computeTokensPerSec(10, 400, 900)).toBeNull();
  });

  it('returns null when there are no completion tokens', () => {
    expect(computeTokensPerSec(0, 1000, 100)).toBeNull();
    expect(computeTokensPerSec(null, 1000, 100)).toBeNull();
    expect(computeTokensPerSec(undefined, 1000, 100)).toBeNull();
  });

  it('returns null for a missing or non-finite duration', () => {
    expect(computeTokensPerSec(10, null, 0)).toBeNull();
    expect(computeTokensPerSec(10, Number.NaN, 0)).toBeNull();
  });

  it('matches the metrics produced by a consumed stream', async () => {
    const { chunks, now } = streamOf([
      contentChunk('one '),
      contentChunk('two '),
      contentChunk('three'),
      sse({ choices: [], usage: { prompt_tokens: 5, completion_tokens: 30 } }),
      DONE,
    ]);

    const result = await consumeChatCompletionStream(chunks, { startedAt: 0, now });
    // ttft 100ms, duration 500ms → 400ms of generation for 30 tokens = 75 tok/s
    expect(computeTokensPerSec(result.completionTokens, result.durationMs, result.ttftMs))
      .toBeCloseTo(75, 6);
  });
});
