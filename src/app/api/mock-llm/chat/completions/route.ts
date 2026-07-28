export const dynamic = 'force-dynamic';

const CHUNK_COUNT = 10;
const MIN_CHUNK_DELAY_MS = 100;
const MAX_CHUNK_DELAY_MS = 200;
const ERROR_DELAY_MS = 300;
const SLOW_PREFILL_MS = 2000;

/** Magic strings a prompt can contain to steer the mock. */
export const TRIGGER_ERROR = 'TRIGGER_ERROR';
export const TRIGGER_SLOW = 'TRIGGER_SLOW';
/** Never stop calling tools — used to verify the loop's turn budget. */
export const TRIGGER_TOOL_LOOP = 'TRIGGER_TOOL_LOOP';

/** How many pieces a tool call's arguments are split into. */
const TOOL_ARG_CHUNKS = 3;

interface ChatMessageLike {
  role?: unknown;
  content?: unknown;
}

function messagesOf(payload: unknown): ChatMessageLike[] {
  const messages = (payload as { messages?: unknown } | null)?.messages;
  return Array.isArray(messages) ? (messages as ChatMessageLike[]) : [];
}

function lastUserMessage(payload: unknown): string {
  const messages = messagesOf(payload);

  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const message = messages[i];
    if (message?.role === 'user' && typeof message.content === 'string') {
      return message.content;
    }
  }
  return '';
}

/** Text of the most recent tool result, or null when none has come back yet. */
function lastToolResult(payload: unknown): string | null {
  const messages = messagesOf(payload);

  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const message = messages[i];
    if (message?.role === 'tool' && typeof message.content === 'string') {
      return message.content;
    }
  }
  return null;
}

interface ToolLike {
  name: string;
  parameters: Record<string, unknown>;
}

function readTools(payload: unknown): ToolLike[] {
  const raw = (payload as { tools?: unknown } | null)?.tools;
  if (!Array.isArray(raw)) return [];

  const tools: ToolLike[] = [];
  for (const entry of raw) {
    const fn = (entry as { function?: unknown })?.function;
    if (!fn || typeof fn !== 'object') continue;

    const name = (fn as { name?: unknown }).name;
    if (typeof name !== 'string' || name.length === 0) continue;

    const parameters = (fn as { parameters?: unknown }).parameters;
    tools.push({
      name,
      parameters:
        parameters && typeof parameters === 'object' && !Array.isArray(parameters)
          ? (parameters as Record<string, unknown>)
          : {},
    });
  }
  return tools;
}

/**
 * Invents a plausible value for one JSON-Schema property.
 *
 * The point is not to be clever but to produce arguments that parse and that a
 * canned tool response can be checked against.
 */
function sampleValue(schema: unknown, key: string, userMessage: string): unknown {
  const type = (schema as { type?: unknown } | null)?.type;
  const enumValues = (schema as { enum?: unknown } | null)?.enum;
  if (Array.isArray(enumValues) && enumValues.length > 0) return enumValues[0];

  switch (type) {
    case 'number':
    case 'integer':
      return 42;
    case 'boolean':
      return true;
    case 'array':
      return [];
    case 'object':
      return {};
    default:
      // A query-ish string is far more useful carrying the prompt's own words.
      return /query|q|search|text|prompt|question/i.test(key)
        ? userMessage.trim().split(/\s+/).slice(0, 8).join(' ') || 'mock query'
        : `mock ${key}`;
  }
}

function synthesizeArguments(tool: ToolLike, userMessage: string): string {
  const properties = (tool.parameters as { properties?: unknown }).properties;
  if (!properties || typeof properties !== 'object') return '{}';

  const required = (tool.parameters as { required?: unknown }).required;
  const requiredKeys = Array.isArray(required)
    ? required.filter((key): key is string => typeof key === 'string')
    : [];

  const entries = Object.entries(properties as Record<string, unknown>);
  // Fill the required properties, or everything when nothing is marked required.
  const chosen = requiredKeys.length > 0
    ? entries.filter(([key]) => requiredKeys.includes(key))
    : entries;

  const args: Record<string, unknown> = {};
  for (const [key, schema] of chosen) {
    args[key] = sampleValue(schema, key, userMessage);
  }
  return JSON.stringify(args);
}

function splitEvenly(text: string, parts: number): string[] {
  if (text.length === 0) return [''];
  const size = Math.ceil(text.length / parts);
  const chunks: string[] = [];
  for (let i = 0; i < text.length; i += size) {
    chunks.push(text.slice(i, i + size));
  }
  return chunks;
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/** Deterministic-ish echo: a short acknowledgement plus the prompt's own words. */
function buildChunks(userMessage: string, toolResult: string | null): string[] {
  const words = userMessage.trim().split(/\s+/).filter(Boolean).slice(0, 24);
  const echo = words.length > 0 ? words.join(' ') : '(empty prompt)';

  // Quoting the tool output proves the result actually made it back into the
  // conversation, which is the whole point of the execute-mode loop.
  const source =
    toolResult === null
      ? `Mock response. You said: "${echo}". This text is generated locally by the mock endpoint so run metrics have something to measure.`
      : `Mock response. The tool returned: ${toolResult.trim().slice(0, 300)} — answering "${echo}" on that basis.`;

  return splitEvenly(source, CHUNK_COUNT);
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
  const toolResult = lastToolResult(payload);
  const offeredTools = readTools(payload);

  // Call a tool on the first turn, then answer using what came back. With
  // TRIGGER_TOOL_LOOP the mock never settles, so the loop's turn budget — and
  // the `max_turns` stop reason — can be exercised.
  const callTool =
    offeredTools.length > 0 &&
    (userMessage.includes(TRIGGER_TOOL_LOOP) || toolResult === null);

  const chunks = callTool ? [] : buildChunks(userMessage, toolResult);
  const toolCallName = callTool ? offeredTools[0].name : null;
  const toolCallArgs = callTool ? synthesizeArguments(offeredTools[0], userMessage) : '';

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

        const pause = () =>
          sleep(
            MIN_CHUNK_DELAY_MS +
              Math.floor(Math.random() * (MAX_CHUNK_DELAY_MS - MIN_CHUNK_DELAY_MS + 1)),
          );

        if (toolCallName !== null) {
          // The opening fragment carries the id and name; the arguments follow
          // in pieces, which is how vLLM streams them and what the client's
          // accumulator has to cope with.
          await pause();
          send(
            sse({
              id,
              object: 'chat.completion.chunk',
              created,
              model,
              choices: [
                {
                  index: 0,
                  delta: {
                    tool_calls: [
                      {
                        index: 0,
                        id: `call_mock_${created}`,
                        type: 'function',
                        function: { name: toolCallName, arguments: '' },
                      },
                    ],
                  },
                  finish_reason: null,
                },
              ],
            }),
          );

          for (const fragment of splitEvenly(toolCallArgs, TOOL_ARG_CHUNKS)) {
            await pause();
            if (request.signal.aborted) break;
            send(
              sse({
                id,
                object: 'chat.completion.chunk',
                created,
                model,
                choices: [
                  {
                    index: 0,
                    delta: { tool_calls: [{ index: 0, function: { arguments: fragment } }] },
                    finish_reason: null,
                  },
                ],
              }),
            );
          }
        }

        for (const chunk of chunks) {
          await pause();
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
            choices: [
              {
                index: 0,
                delta: {},
                finish_reason: toolCallName !== null ? 'tool_calls' : 'stop',
              },
            ],
          }),
        );

        const completionTokens =
          chunks.reduce((total, chunk) => total + Math.max(1, Math.round(chunk.length / 4)), 0) +
          (toolCallName !== null
            ? Math.max(1, Math.round((toolCallName.length + toolCallArgs.length) / 4))
            : 0);
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
