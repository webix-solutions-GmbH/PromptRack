import { mockDisabledResponse, mocksEnabled } from '@/lib/dev-only';

export const dynamic = 'force-dynamic';

/**
 * A tiny MCP server over streamable HTTP, for end-to-end testing of the MCP
 * path without running a real Odoo or websearch server.
 *
 * It implements only what the client actually exercises: `initialize`,
 * `tools/list` and `tools/call`, answering with plain JSON rather than an SSE
 * stream (which the spec allows for a single response). Register a toolset with
 * URL `http://localhost:3000/api/mock-mcp`, hit Discover, and the
 * tools below show up.
 *
 * Query parameters steer it: `?hide=echo_upper` drops a tool from
 * `tools/list` so the discovery retire path can be verified, and `?fail=1`
 * makes every call answer with `isError`.
 */

const PROTOCOL_VERSION = '2025-06-18';

const TOOLS = [
  {
    name: 'echo_upper',
    description: 'Uppercases the given text.',
    inputSchema: {
      type: 'object',
      properties: { text: { type: 'string', description: 'Text to uppercase' } },
      required: ['text'],
    },
  },
  {
    name: 'add_numbers',
    description: 'Adds two numbers and returns the sum.',
    inputSchema: {
      type: 'object',
      properties: { a: { type: 'number' }, b: { type: 'number' } },
      required: ['a', 'b'],
    },
  },
] as const;

interface JsonRpcRequest {
  jsonrpc?: unknown;
  id?: unknown;
  method?: unknown;
  params?: unknown;
}

function ok(id: unknown, result: unknown) {
  return Response.json({ jsonrpc: '2.0', id, result });
}

function fail(id: unknown, code: number, message: string) {
  return Response.json({ jsonrpc: '2.0', id, error: { code, message } });
}

function textResult(text: string, isError = false) {
  return { content: [{ type: 'text', text }], isError };
}

function callTool(name: string, args: Record<string, unknown>, forceFail: boolean) {
  if (forceFail) {
    return textResult(`Mock MCP failure for "${name}" (?fail=1).`, true);
  }

  switch (name) {
    case 'echo_upper': {
      const text = typeof args.text === 'string' ? args.text : '';
      return textResult(text.toUpperCase());
    }
    case 'add_numbers': {
      const a = typeof args.a === 'number' ? args.a : Number.NaN;
      const b = typeof args.b === 'number' ? args.b : Number.NaN;
      if (!Number.isFinite(a) || !Number.isFinite(b)) {
        return textResult('add_numbers needs two numeric arguments, a and b.', true);
      }
      return textResult(JSON.stringify({ sum: a + b }));
    }
    default:
      return textResult(`Unknown tool "${name}".`, true);
  }
}

export async function POST(request: Request) {
  if (!mocksEnabled()) return mockDisabledResponse();
  const url = new URL(request.url);
  const hidden = new Set(url.searchParams.getAll('hide'));
  const forceFail = url.searchParams.get('fail') === '1';

  let payload: JsonRpcRequest;
  try {
    payload = (await request.json()) as JsonRpcRequest;
  } catch {
    return fail(null, -32700, 'Parse error.');
  }

  const { id, method } = payload;

  switch (method) {
    case 'initialize':
      return ok(id, {
        protocolVersion: PROTOCOL_VERSION,
        capabilities: { tools: {} },
        serverInfo: { name: 'mock-mcp', version: '0.1.0' },
      });

    // Notifications carry no id and expect no result, only an acknowledgement.
    case 'notifications/initialized':
      return new Response(null, { status: 202 });

    case 'ping':
      return ok(id, {});

    case 'tools/list':
      return ok(id, { tools: TOOLS.filter((tool) => !hidden.has(tool.name)) });

    case 'tools/call': {
      const params = (payload.params ?? {}) as { name?: unknown; arguments?: unknown };
      if (typeof params.name !== 'string') {
        return fail(id, -32602, 'tools/call requires a tool name.');
      }
      const args =
        params.arguments && typeof params.arguments === 'object' && !Array.isArray(params.arguments)
          ? (params.arguments as Record<string, unknown>)
          : {};
      return ok(id, callTool(params.name, args, forceFail));
    }

    default:
      return fail(id, -32601, `Method "${String(method)}" is not implemented by the mock.`);
  }
}
