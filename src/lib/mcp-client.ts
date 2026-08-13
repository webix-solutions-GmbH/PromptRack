/**
 * Minimal MCP client: list a server's tools, and call one.
 *
 * Transport is streamable HTTP only. A server is therefore configured exactly
 * like a machine — a URL plus optional auth headers — which means the deployed
 * container needs nothing baked in: an Odoo or websearch MCP server runs as its
 * own container on the same Docker network and is reached by URL.
 *
 * Connections are not pooled. A run is sequential and low-frequency, so opening
 * a connection per operation keeps lifecycle and failure handling trivial.
 */

import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StreamableHTTPClientTransport } from '@modelcontextprotocol/sdk/client/streamableHttp.js';
import { describeFetchError } from './fetch-error';

/** Live connection details for an MCP toolset. */
export interface McpServer {
  url: string;
  /** Extra request headers (auth), as stored on the toolset. */
  headers?: Record<string, string> | null;
}

/** One tool as the server describes it. */
export interface McpToolDescriptor {
  name: string;
  description: string | null;
  /** The tool's `inputSchema`, which is already a JSON Schema object. */
  parameters: Record<string, unknown>;
}

export class McpError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'McpError';
  }
}

const DEFAULT_TIMEOUT_MS = 60_000;
const CLIENT_INFO = { name: 'modelfit', version: '0.1.0' };

/** Parses a toolset's stored `mcp_headers` column. */
export function parseMcpHeaders(raw: string | null | undefined): Record<string, string> | null {
  if (!raw) return null;

  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return null;

  const headers: Record<string, string> = {};
  for (const [key, value] of Object.entries(parsed as Record<string, unknown>)) {
    if (typeof value === 'string') headers[key] = value;
  }
  return Object.keys(headers).length > 0 ? headers : null;
}

function describeMcpError(err: unknown, url: string): McpError {
  if (err instanceof McpError) return err;
  if (err instanceof Error) {
    // undici reports every transport failure as `TypeError: fetch failed`, so
    // reuse the same unwrapping the LLM client does.
    const detail =
      err instanceof TypeError ? describeFetchError(err) : err.message || 'Unknown MCP error.';
    return new McpError(`${detail} (${url})`);
  }
  return new McpError(`Unknown MCP error. (${url})`);
}

/** Opens a connection, runs `body`, and always closes again. */
async function withClient<T>(
  server: McpServer,
  body: (client: Client) => Promise<T>,
): Promise<T> {
  const url = server.url.trim();
  if (!/^https?:\/\//i.test(url)) {
    throw new McpError(`"${url}" is not an http(s) MCP endpoint.`);
  }

  const client = new Client(CLIENT_INFO);
  const transport = new StreamableHTTPClientTransport(new URL(url), {
    ...(server.headers ? { requestInit: { headers: server.headers } } : {}),
  });

  try {
    await client.connect(transport);
    return await body(client);
  } catch (err) {
    throw describeMcpError(err, url);
  } finally {
    // Closing is best effort — the operation's result matters more than a
    // tidy shutdown, and a server that already hung up would throw here.
    try {
      await client.close();
    } catch {
      // Already gone.
    }
  }
}

/** Everything the server currently advertises under `tools/list`. */
export async function listMcpTools(server: McpServer): Promise<McpToolDescriptor[]> {
  return withClient(server, async (client) => {
    const result = await client.listTools(undefined, { timeout: DEFAULT_TIMEOUT_MS });

    return result.tools.map((tool) => ({
      name: tool.name,
      description: tool.description ?? null,
      parameters: (tool.inputSchema ?? { type: 'object', properties: {} }) as Record<
        string,
        unknown
      >,
    }));
  });
}

export interface McpCallResult {
  /** Flattened text of the tool's content blocks, ready to feed to the model. */
  content: string;
  isError: boolean;
}

/**
 * Flattens MCP content blocks into one string.
 *
 * A model only ever sees a tool message as text, so non-text blocks are
 * described rather than dropped — silently losing an image would look like the
 * tool returned nothing.
 */
function flattenContent(content: unknown): string {
  if (!Array.isArray(content)) return '';

  const parts: string[] = [];
  for (const block of content) {
    if (!block || typeof block !== 'object') continue;

    const type = (block as { type?: unknown }).type;
    if (type === 'text') {
      const text = (block as { text?: unknown }).text;
      if (typeof text === 'string') parts.push(text);
    } else if (typeof type === 'string') {
      parts.push(`[${type} content omitted]`);
    }
  }
  return parts.join('\n');
}

export async function callMcpTool(
  server: McpServer,
  name: string,
  args: Record<string, unknown>,
  signal?: AbortSignal,
): Promise<McpCallResult> {
  return withClient(server, async (client) => {
    const result = await client.callTool(
      { name, arguments: args },
      undefined,
      { timeout: DEFAULT_TIMEOUT_MS, signal },
    );

    const content = flattenContent(result.content);
    const isError = result.isError === true;

    return {
      // A tool that reports an error with no text still has to say something,
      // or the model is left staring at an empty message.
      content:
        content.length > 0
          ? content
          : isError
            ? JSON.stringify({ error: `Tool "${name}" reported an error with no detail.` })
            : JSON.stringify({ result: 'ok', detail: 'The tool returned no content.' }),
      isError,
    };
  });
}
