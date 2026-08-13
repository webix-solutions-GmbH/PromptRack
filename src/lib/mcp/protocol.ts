/**
 * The MCP wire protocol, server side.
 *
 * Hand-rolled JSON-RPC over streamable HTTP, exactly like `api/mock-mcp`
 * already does for the client side: the SDK's `StreamableHTTPServerTransport`
 * wants Node's `ServerResponse`, which a Next route handler does not have, and
 * the subset a tools-only server needs is small.
 *
 * Stateless on purpose — no session id is issued, so every POST is independent
 * and nothing has to survive a container restart or be shared between
 * instances. Answers are plain `application/json` single responses, which the
 * spec allows in place of an SSE stream.
 */

import { canWrite, type Role } from '@/lib/auth/policy';
import { McpToolError, toolArgs, type ToolArgs } from './args';
import type { McpScopeSource } from './customer';

export const LATEST_PROTOCOL_VERSION = '2025-11-25';

/** Versions we will negotiate down to, newest first. */
const SUPPORTED_PROTOCOL_VERSIONS = [
  LATEST_PROTOCOL_VERSION,
  '2025-06-18',
  '2025-03-26',
  '2024-11-05',
];

export const SERVER_INFO = {
  name: 'agent-model-evaluator',
  title: 'Agent Model Evaluator',
  version: '0.1.0',
} as const;

/**
 * Who a call is acting as, and where it is allowed to look.
 *
 * `actor` comes from the token (see `lib/mcp/auth.ts`); `source` is what the
 * connection said about the customer workspace, which every tool but
 * `list_customers` resolves through `resolveMcpScope`.
 */
export interface McpCallContext {
  actor: { userId: string; email: string; role: Role };
  source: McpScopeSource;
}

/** One tool: what the model is told, and what actually runs. */
export interface McpToolSpec {
  name: string;
  description: string;
  /** JSON Schema for `arguments`. */
  inputSchema: Record<string, unknown>;
  /** Returns any JSON-serializable payload; throw `McpToolError` to fail. */
  handler: (args: ToolArgs, ctx: McpCallContext) => Promise<unknown>;
  /**
   * Tools that only read are annotated as such, so a client can present the
   * writing ones (or `delete_prompt`) differently — and so a viewer's token can
   * be refused everything else.
   */
  readOnly?: boolean;
  destructive?: boolean;
}

export interface JsonRpcReply {
  /** HTTP status; 202 with a null body is the response to a notification. */
  status: number;
  body: unknown | null;
}

const PARSE_ERROR = -32700;
const INVALID_REQUEST = -32600;
const METHOD_NOT_FOUND = -32601;
const INVALID_PARAMS = -32602;
const INTERNAL_ERROR = -32603;

function result(id: unknown, value: unknown): JsonRpcReply {
  return { status: 200, body: { jsonrpc: '2.0', id, result: value } };
}

function error(id: unknown, code: number, message: string): JsonRpcReply {
  return { status: 200, body: { jsonrpc: '2.0', id, error: { code, message } } };
}

/**
 * A malformed request that never reached a method. Unlike a method's error
 * these carry an HTTP 400 as well, because there is no request id to answer.
 */
export function protocolError(code: number, message: string): JsonRpcReply {
  return { status: 400, body: { jsonrpc: '2.0', id: null, error: { code, message } } };
}

export function parseErrorReply(): JsonRpcReply {
  return protocolError(PARSE_ERROR, 'Request body is not valid JSON.');
}

/** Everything a tool returns is text; JSON keeps it readable and parseable. */
function textContent(value: unknown, isError: boolean) {
  const text =
    typeof value === 'string' ? value : JSON.stringify(value ?? { ok: true }, null, 2);
  return { content: [{ type: 'text', text }], isError };
}

function negotiateVersion(requested: unknown): string {
  if (typeof requested === 'string' && SUPPORTED_PROTOCOL_VERSIONS.includes(requested)) {
    return requested;
  }
  return LATEST_PROTOCOL_VERSION;
}

/** The `tools/list` view of a spec — handlers are not part of the wire format. */
function describeTool(spec: McpToolSpec) {
  return {
    name: spec.name,
    description: spec.description,
    inputSchema: spec.inputSchema,
    annotations: {
      readOnlyHint: spec.readOnly === true,
      destructiveHint: spec.destructive === true,
    },
  };
}

/**
 * Handles one JSON-RPC message.
 *
 * A tool that throws is reported as `isError` tool *content* rather than a
 * JSON-RPC error, which is what lets the calling model read the message and fix
 * its arguments — the same reasoning as `tool-loop.ts` feeding tool failures
 * back to the model instead of failing the row.
 */
export async function handleMcpMessage(
  payload: unknown,
  registry: readonly McpToolSpec[],
  ctx: McpCallContext,
): Promise<JsonRpcReply> {
  if (Array.isArray(payload)) {
    return protocolError(
      INVALID_REQUEST,
      'JSON-RPC batching is not supported; send one request per POST.',
    );
  }
  if (!payload || typeof payload !== 'object') {
    return protocolError(INVALID_REQUEST, 'Request body must be a JSON-RPC object.');
  }

  const message = payload as { id?: unknown; method?: unknown; params?: unknown };
  const { id } = message;
  const method = message.method;

  if (typeof method !== 'string') {
    return protocolError(INVALID_REQUEST, 'Request is missing a method.');
  }

  // Notifications carry no id and get an acknowledgement with no body.
  if (method.startsWith('notifications/')) {
    return { status: 202, body: null };
  }

  switch (method) {
    case 'initialize': {
      const params = (message.params ?? {}) as { protocolVersion?: unknown };
      return result(id, {
        protocolVersion: negotiateVersion(params.protocolVersion),
        capabilities: { tools: { listChanged: false } },
        serverInfo: SERVER_INFO,
        instructions:
          'Authoring and reading an LLM benchmark: prompt groups, system prompts and prompts (optionally tool tests), then runs against a registered machine and their measured results. ' +
          'Every call is scoped to one customer workspace: pass `customer` (name or id) on each call, or send an `X-Customer` header on the connection. `list_customers` lists them. ' +
          `You are authenticated as ${ctx.actor.email} (${ctx.actor.role})` +
          `${canWrite(ctx.actor.role) ? '.' : ', which is read-only: only the read tools will answer.'}`,
      });
    }

    case 'ping':
      return result(id, {});

    case 'tools/list':
      return result(id, { tools: registry.map(describeTool) });

    // Advertised capabilities do not include these, but clients probe anyway;
    // an empty list is friendlier than a protocol error in the client's log.
    case 'resources/list':
      return result(id, { resources: [] });
    case 'resources/templates/list':
      return result(id, { resourceTemplates: [] });
    case 'prompts/list':
      return result(id, { prompts: [] });

    case 'tools/call': {
      const params = (message.params ?? {}) as { name?: unknown; arguments?: unknown };
      if (typeof params.name !== 'string') {
        return error(id, INVALID_PARAMS, 'tools/call requires a tool name.');
      }

      const spec = registry.find((candidate) => candidate.name === params.name);
      if (!spec) {
        return error(id, INVALID_PARAMS, `Unknown tool "${params.name}".`);
      }

      // isError content rather than a JSON-RPC error, for the same reason a
      // tool failure is: the calling model reads the message and stops trying.
      if (!spec.readOnly && !canWrite(ctx.actor.role)) {
        return result(
          id,
          textContent(
            { error: `The token's account is read-only; "${spec.name}" writes.` },
            true,
          ),
        );
      }

      try {
        const value = await spec.handler(toolArgs(params.arguments), ctx);
        return result(id, textContent(value, false));
      } catch (err) {
        if (err instanceof McpToolError) {
          return result(id, textContent({ error: err.message }, true));
        }
        // An unexpected failure is a bug here, not bad input: report it as an
        // internal error so it shows up as such on the client.
        const detail = err instanceof Error ? err.message : 'Unknown error.';
        return error(id, INTERNAL_ERROR, `Tool "${spec.name}" failed: ${detail}`);
      }
    }

    default:
      return error(id, METHOD_NOT_FOUND, `Method "${method}" is not supported.`);
  }
}
