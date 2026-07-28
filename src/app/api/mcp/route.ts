import { API_KEY_ENV, checkApiKey, configuredApiKey } from '@/lib/mcp/auth';
import {
  handleMcpMessage,
  parseErrorReply,
  LATEST_PROTOCOL_VERSION,
  SERVER_INFO,
} from '@/lib/mcp/protocol';
import { MCP_TOOLS } from '@/lib/mcp/registry';

export const dynamic = 'force-dynamic';

/**
 * This app *as* an MCP server: an agent (Claude Code, say) can push the system
 * prompts and prompts of another project in here, start a run against a
 * registered model, and read the measurements back — instead of retyping
 * someone else's prompts into the web UI by hand.
 *
 * Transport is streamable HTTP, stateless: every POST is a self-contained
 * JSON-RPC request answered with plain JSON, no session id, nothing to
 * reconnect to. The protocol handling lives in `lib/mcp/protocol.ts`.
 *
 * Auth is a single API key in `MCP_API_KEY` (see `lib/mcp/auth.ts`). Without it
 * the endpoint refuses everything, which matters because these tools write.
 */
export async function POST(request: Request) {
  const auth = checkApiKey(request.headers, configuredApiKey());
  if (!auth.ok) {
    return unauthorized(auth.status, auth.message, auth.challenge);
  }

  let payload: unknown;
  try {
    payload = await request.json();
  } catch {
    const reply = parseErrorReply();
    return Response.json(reply.body, { status: reply.status });
  }

  const reply = await handleMcpMessage(payload, MCP_TOOLS);
  if (reply.body === null) {
    return new Response(null, { status: reply.status });
  }
  return Response.json(reply.body, { status: reply.status });
}

/**
 * Clients open a GET to listen for server-initiated messages. This server never
 * sends any (stateless, tools only), so it declines instead of holding a stream
 * open — and answers an unauthenticated GET the same way as a POST, so a
 * misconfigured key is reported once rather than as a hanging connection.
 */
export async function GET(request: Request) {
  const auth = checkApiKey(request.headers, configuredApiKey());
  if (!auth.ok) {
    return unauthorized(auth.status, auth.message, auth.challenge);
  }

  return Response.json(
    {
      jsonrpc: '2.0',
      id: null,
      error: {
        code: -32000,
        message: 'This server does not offer a server-to-client stream; POST JSON-RPC requests instead.',
      },
    },
    { status: 405, headers: { Allow: 'POST, DELETE' } },
  );
}

/** No session state exists, so terminating one always succeeds. */
export async function DELETE() {
  return new Response(null, { status: 204 });
}

function unauthorized(status: number, message: string, challenge?: boolean) {
  return Response.json(
    {
      jsonrpc: '2.0',
      id: null,
      error: { code: -32001, message },
      server: SERVER_INFO.name,
      protocolVersion: LATEST_PROTOCOL_VERSION,
      hint: `Set ${API_KEY_ENV} on the server and send the same value in the x-api-key header.`,
    },
    {
      status,
      ...(challenge ? { headers: { 'WWW-Authenticate': 'Bearer realm="agent-val-mcp"' } } : {}),
    },
  );
}
