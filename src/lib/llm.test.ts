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

/** One `delta.tool_calls` chunk, in whatever partial shape a server sends. */
function toolCallChunk(entries: Record<string, unknown>[]): string {
  return sse({
    id: 'chatcmpl-1',
    object: 'chat.completion.chunk',
    model: 'test-model',
    choices: [{ index: 0, delta: { tool_calls: entries }, finish_reason: null }],
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
    expect(result.toolCalls).toEqual([]);
    expect(result.finishReason).toBeNull();
  });

  it('reports no tool calls for a plain text response', async () => {
    const { chunks, now } = streamOf([contentChunk('just prose'), DONE]);

    const result = await consumeChatCompletionStream(chunks, { startedAt: 0, now });

    expect(result.toolCalls).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// Tool calls
// ---------------------------------------------------------------------------

describe('consumeChatCompletionStream — tool calls', () => {
  it('stitches the vLLM shape: one index-keyed slot, arguments in fragments', async () => {
    const fixture = [
      sse({ choices: [{ index: 0, delta: { role: 'assistant' }, finish_reason: null }] }),
      toolCallChunk([
        { index: 0, id: 'call_abc', type: 'function', function: { name: 'search', arguments: '' } },
      ]),
      toolCallChunk([{ index: 0, function: { arguments: '{"q":' } }]),
      toolCallChunk([{ index: 0, function: { arguments: '"laptops"}' } }]),
      sse({ choices: [{ index: 0, delta: {}, finish_reason: 'tool_calls' }] }),
      sse({ choices: [], usage: { prompt_tokens: 40, completion_tokens: 12 } }),
      DONE,
    ];
    const { chunks, now } = streamOf(fixture);

    const result = await consumeChatCompletionStream(chunks, { startedAt: 0, now });

    expect(result.toolCalls).toEqual([
      {
        id: 'call_abc',
        type: 'function',
        function: { name: 'search', arguments: '{"q":"laptops"}' },
      },
    ]);
    expect(result.text).toBe('');
    expect(result.finishReason).toBe('tool_calls');
    expect(result.completionTokens).toBe(12);
  });

  it('measures ttft from the first tool-call fragment when no content is streamed', async () => {
    const fixture = [
      sse({ choices: [{ index: 0, delta: { role: 'assistant' }, finish_reason: null }] }),
      toolCallChunk([{ index: 0, id: 'call_1', function: { name: 'ping', arguments: '{}' } }]),
      DONE,
    ];
    const { chunks, now } = streamOf(fixture);

    const result = await consumeChatCompletionStream(chunks, { startedAt: 0, now });

    // The role-only chunk is not output; the tool-call chunk at 200ms is.
    expect(result.ttftMs).toBe(200);
  });

  it('estimates tokens from the tool call when there is no text and no usage', async () => {
    const args = '{"query":"a"}';
    const { chunks, now } = streamOf([
      toolCallChunk([{ index: 0, id: 'c1', function: { name: 'search', arguments: args } }]),
      DONE,
    ]);

    const result = await consumeChatCompletionStream(chunks, { startedAt: 0, now });

    expect(result.tokensEstimated).toBe(true);
    expect(result.completionTokens).toBe(Math.ceil(('search'.length + args.length) / 4));
  });

  it('accepts a whole call delivered in a single chunk', async () => {
    const { chunks, now } = streamOf([
      toolCallChunk([
        {
          index: 0,
          id: 'call_one_shot',
          type: 'function',
          function: { name: 'get_time', arguments: '{}' },
        },
      ]),
      DONE,
    ]);

    const result = await consumeChatCompletionStream(chunks, { startedAt: 0, now });

    expect(result.toolCalls).toEqual([
      { id: 'call_one_shot', type: 'function', function: { name: 'get_time', arguments: '{}' } },
    ]);
  });

  it('keeps parallel calls apart and returns them in index order', async () => {
    const { chunks, now } = streamOf([
      toolCallChunk([
        { index: 0, id: 'a', function: { name: 'first', arguments: '{"x"' } },
        { index: 1, id: 'b', function: { name: 'second', arguments: '{"y"' } },
      ]),
      toolCallChunk([{ index: 1, function: { arguments: ':2}' } }]),
      toolCallChunk([{ index: 0, function: { arguments: ':1}' } }]),
      DONE,
    ]);

    const result = await consumeChatCompletionStream(chunks, { startedAt: 0, now });

    expect(result.toolCalls).toEqual([
      { id: 'a', type: 'function', function: { name: 'first', arguments: '{"x":1}' } },
      { id: 'b', type: 'function', function: { name: 'second', arguments: '{"y":2}' } },
    ]);
  });

  it('falls back to the call id when the endpoint omits index', async () => {
    const { chunks, now } = streamOf([
      toolCallChunk([{ id: 'x1', function: { name: 'alpha', arguments: '{"a"' } }]),
      toolCallChunk([{ id: 'x2', function: { name: 'beta', arguments: '{"b"' } }]),
      toolCallChunk([{ id: 'x1', function: { arguments: ':1}' } }]),
      toolCallChunk([{ id: 'x2', function: { arguments: ':2}' } }]),
      DONE,
    ]);

    const result = await consumeChatCompletionStream(chunks, { startedAt: 0, now });

    expect(result.toolCalls).toEqual([
      { id: 'x1', type: 'function', function: { name: 'alpha', arguments: '{"a":1}' } },
      { id: 'x2', type: 'function', function: { name: 'beta', arguments: '{"b":2}' } },
    ]);
  });

  it('appends to the call in flight when neither index nor id is sent', async () => {
    const { chunks, now } = streamOf([
      toolCallChunk([{ function: { name: 'lonely', arguments: '{"k"' } }]),
      toolCallChunk([{ function: { arguments: ':true}' } }]),
      DONE,
    ]);

    const result = await consumeChatCompletionStream(chunks, { startedAt: 0, now });

    expect(result.toolCalls).toEqual([
      { id: 'call_0', type: 'function', function: { name: 'lonely', arguments: '{"k":true}' } },
    ]);
  });

  it('synthesizes an id when the endpoint never sends one', async () => {
    const { chunks, now } = streamOf([
      toolCallChunk([{ index: 3, function: { name: 'nameless_id', arguments: '{}' } }]),
      DONE,
    ]);

    const result = await consumeChatCompletionStream(chunks, { startedAt: 0, now });

    expect(result.toolCalls[0].id).toBe('call_3');
  });

  it('stitches a tool-call payload split across network reads', async () => {
    const full = toolCallChunk([
      { index: 0, id: 'split', function: { name: 'search', arguments: '{"q":"mid-json"}' } },
    ]);
    const cut = Math.floor(full.length / 2);
    const { chunks, now } = streamOf([full.slice(0, cut), full.slice(cut), DONE]);

    const result = await consumeChatCompletionStream(chunks, { startedAt: 0, now });

    expect(result.toolCalls[0].function.arguments).toBe('{"q":"mid-json"}');
  });

  it('keeps malformed arguments verbatim — parsing them is the caller’s job', async () => {
    const { chunks, now } = streamOf([
      toolCallChunk([{ index: 0, id: 'bad', function: { name: 'oops', arguments: '{"q": ' } }]),
      DONE,
    ]);

    const result = await consumeChatCompletionStream(chunks, { startedAt: 0, now });

    expect(result.toolCalls[0].function.arguments).toBe('{"q": ');
  });

  it('drops a slot that never received a function name', async () => {
    const { chunks, now } = streamOf([
      toolCallChunk([{ index: 0, id: 'no_name', type: 'function' }]),
      DONE,
    ]);

    const result = await consumeChatCompletionStream(chunks, { startedAt: 0, now });

    expect(result.toolCalls).toEqual([]);
  });

  it('reassembles a name that itself arrived in fragments', async () => {
    const { chunks, now } = streamOf([
      toolCallChunk([{ index: 0, id: 'n', function: { name: 'sea' } }]),
      toolCallChunk([{ index: 0, function: { name: 'rch', arguments: '{}' } }]),
      DONE,
    ]);

    const result = await consumeChatCompletionStream(chunks, { startedAt: 0, now });

    expect(result.toolCalls[0].function.name).toBe('search');
  });

  it('captures content and tool calls together', async () => {
    const { chunks, now } = streamOf([
      contentChunk('Let me look that up. '),
      toolCallChunk([{ index: 0, id: 'both', function: { name: 'search', arguments: '{}' } }]),
      sse({ choices: [{ index: 0, delta: {}, finish_reason: 'tool_calls' }] }),
      DONE,
    ]);

    const result = await consumeChatCompletionStream(chunks, { startedAt: 0, now });

    expect(result.text).toBe('Let me look that up. ');
    expect(result.toolCalls).toHaveLength(1);
    expect(result.ttftMs).toBe(100);
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
