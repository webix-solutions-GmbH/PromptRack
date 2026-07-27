export const dynamic = 'force-dynamic';

const CHUNK_COUNT = 10;
const MIN_CHUNK_DELAY_MS = 100;
const MAX_CHUNK_DELAY_MS = 200;
const ERROR_DELAY_MS = 300;
const SLOW_PREFILL_MS = 2000;

/** Magic strings a prompt can contain to steer the mock. */
export const TRIGGER_ERROR = 'TRIGGER_ERROR';
export const TRIGGER_SLOW = 'TRIGGER_SLOW';

interface ChatMessageLike {
  role?: unknown;
  content?: unknown;
}

function lastUserMessage(payload: unknown): string {
  const messages = (payload as { messages?: unknown } | null)?.messages;
  if (!Array.isArray(messages)) return '';

  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const message = messages[i] as ChatMessageLike;
    if (message?.role === 'user' && typeof message.content === 'string') {
      return message.content;
    }
  }
  return '';
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/** Deterministic-ish echo: a short acknowledgement plus the prompt's own words. */
function buildChunks(userMessage: string): string[] {
  const words = userMessage.trim().split(/\s+/).filter(Boolean).slice(0, 24);
  const echo = words.length > 0 ? words.join(' ') : '(empty prompt)';
  const body = `Mock response. You said: "${echo}". `;
  const filler =
    'This text is generated locally by the mock endpoint so run metrics have something to measure.';
  const source = `${body}${filler}`;

  const size = Math.ceil(source.length / CHUNK_COUNT);
  const chunks: string[] = [];
  for (let i = 0; i < source.length; i += size) {
    chunks.push(source.slice(i, i + size));
  }
  return chunks;
}

function sse(payload: unknown): string {
  return `data: ${JSON.stringify(payload)}\n\n`;
}

/**
 * Minimal OpenAI-compatible streaming chat-completions endpoint used for
 * end-to-end testing of the run executor.
 */
export async function POST(request: Request) {
  let payload: unknown;
  try {
    payload = await request.json();
  } catch {
    return Response.json({ error: { message: 'Invalid JSON body.' } }, { status: 400 });
  }

  const userMessage = lastUserMessage(payload);
  const model =
    typeof (payload as { model?: unknown }).model === 'string'
      ? ((payload as { model: string }).model)
      : 'mock-fast-7b';

  if (userMessage.includes(TRIGGER_ERROR)) {
    await sleep(ERROR_DELAY_MS);
    return Response.json(
      { error: { message: 'Mock failure requested via TRIGGER_ERROR.', type: 'mock_error' } },
      { status: 500 },
    );
  }

  const slow = userMessage.includes(TRIGGER_SLOW);
  const chunks = buildChunks(userMessage);
  const id = `chatcmpl-mock-${Date.now()}`;
  const created = Math.floor(Date.now() / 1000);
  const encoder = new TextEncoder();

  const stream = new ReadableStream<Uint8Array>({
    async start(controller) {
      const send = (text: string) => controller.enqueue(encoder.encode(text));

      try {
        send(
          sse({
            id,
            object: 'chat.completion.chunk',
            created,
            model,
            choices: [{ index: 0, delta: { role: 'assistant' }, finish_reason: null }],
          }),
        );

        if (slow) {
          await sleep(SLOW_PREFILL_MS);
        }

        for (const chunk of chunks) {
          await sleep(
            MIN_CHUNK_DELAY_MS +
              Math.floor(Math.random() * (MAX_CHUNK_DELAY_MS - MIN_CHUNK_DELAY_MS + 1)),
          );
          if (request.signal.aborted) break;
          send(
            sse({
              id,
              object: 'chat.completion.chunk',
              created,
              model,
              choices: [{ index: 0, delta: { content: chunk }, finish_reason: null }],
            }),
          );
        }

        send(
          sse({
            id,
            object: 'chat.completion.chunk',
            created,
            model,
            choices: [{ index: 0, delta: {}, finish_reason: 'stop' }],
          }),
        );

        const completionTokens = chunks.reduce(
          (total, chunk) => total + Math.max(1, Math.round(chunk.length / 4)),
          0,
        );
        send(
          sse({
            id,
            object: 'chat.completion.chunk',
            created,
            model,
            choices: [],
            usage: {
              prompt_tokens: Math.max(1, Math.round(userMessage.length / 4)),
              completion_tokens: completionTokens,
              total_tokens: Math.max(1, Math.round(userMessage.length / 4)) + completionTokens,
            },
          }),
        );

        send('data: [DONE]\n\n');
      } catch {
        // Client disconnected mid-stream — nothing to clean up.
      } finally {
        try {
          controller.close();
        } catch {
          // Already closed.
        }
      }
    },
  });

  return new Response(stream, {
    headers: {
      'Content-Type': 'text/event-stream; charset=utf-8',
      'Cache-Control': 'no-store, no-transform',
      Connection: 'keep-alive',
      'X-Accel-Buffering': 'no',
    },
  });
}
