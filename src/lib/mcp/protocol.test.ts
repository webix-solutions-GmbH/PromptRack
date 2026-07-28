import { describe, expect, it } from 'vitest';
import { McpToolError } from './args';
import {
  handleMcpMessage,
  LATEST_PROTOCOL_VERSION,
  type McpToolSpec,
} from './protocol';
import { checkApiKey } from './auth';

const REGISTRY: McpToolSpec[] = [
  {
    name: 'echo',
    description: 'Echoes its argument.',
    readOnly: true,
    inputSchema: { type: 'object', properties: { value: { type: 'string' } } },
    handler: async (args) => ({ value: args.value ?? null }),
  },
  {
    name: 'refuse',
    description: 'Always refuses.',
    inputSchema: { type: 'object', properties: {} },
    handler: async () => {
      throw new McpToolError('"group" is required.');
    },
  },
  {
    name: 'crash',
    description: 'Always crashes.',
    inputSchema: { type: 'object', properties: {} },
    handler: async () => {
      throw new Error('SQLITE_BUSY');
    },
  },
];

function call(name: string, args?: unknown) {
  return handleMcpMessage(
    { jsonrpc: '2.0', id: 1, method: 'tools/call', params: { name, arguments: args } },
    REGISTRY,
  );
}

/** The `result` of a successful reply, typed loosely for assertions. */
function resultOf(reply: { body: unknown }) {
  return (reply.body as { result?: Record<string, unknown> }).result ?? {};
}

function errorOf(reply: { body: unknown }) {
  return (reply.body as { error?: { code: number; message: string } }).error;
}

describe('initialize', () => {
  it('echoes a protocol version it supports', async () => {
    const reply = await handleMcpMessage(
      { jsonrpc: '2.0', id: 1, method: 'initialize', params: { protocolVersion: '2025-03-26' } },
      REGISTRY,
    );
    expect(resultOf(reply).protocolVersion).toBe('2025-03-26');
  });

  it('falls back to its own latest version for anything unknown', async () => {
    const reply = await handleMcpMessage(
      { jsonrpc: '2.0', id: 1, method: 'initialize', params: { protocolVersion: '1999-01-01' } },
      REGISTRY,
    );
    expect(resultOf(reply).protocolVersion).toBe(LATEST_PROTOCOL_VERSION);
  });

  it('advertises only tools', async () => {
    const reply = await handleMcpMessage({ jsonrpc: '2.0', id: 1, method: 'initialize' }, REGISTRY);
    expect(resultOf(reply).capabilities).toEqual({ tools: { listChanged: false } });
  });
});

describe('notifications', () => {
  it('are acknowledged with 202 and no body', async () => {
    const reply = await handleMcpMessage(
      { jsonrpc: '2.0', method: 'notifications/initialized' },
      REGISTRY,
    );
    expect(reply).toEqual({ status: 202, body: null });
  });
});

describe('tools/list', () => {
  it('describes every tool without leaking the handler', async () => {
    const reply = await handleMcpMessage({ jsonrpc: '2.0', id: 1, method: 'tools/list' }, REGISTRY);
    const tools = resultOf(reply).tools as Record<string, unknown>[];

    expect(tools.map((tool) => tool.name)).toEqual(['echo', 'refuse', 'crash']);
    expect(tools[0]).not.toHaveProperty('handler');
    expect(tools[0].annotations).toEqual({ readOnlyHint: true, destructiveHint: false });
    expect(tools[1].annotations).toEqual({ readOnlyHint: false, destructiveHint: false });
  });
});

describe('tools/call', () => {
  it('returns the handler payload as JSON text content', async () => {
    const reply = await call('echo', { value: 'hi' });
    const result = resultOf(reply);
    expect(result.isError).toBe(false);
    expect(JSON.parse((result.content as { text: string }[])[0].text)).toEqual({ value: 'hi' });
  });

  it('tolerates a missing arguments object', async () => {
    const result = resultOf(await call('echo'));
    expect(JSON.parse((result.content as { text: string }[])[0].text)).toEqual({ value: null });
  });

  it('reports a validation refusal as tool content, so the model can fix it', async () => {
    const result = resultOf(await call('refuse'));
    expect(result.isError).toBe(true);
    expect(JSON.parse((result.content as { text: string }[])[0].text)).toEqual({
      error: '"group" is required.',
    });
  });

  it('reports an unexpected failure as a JSON-RPC error instead', async () => {
    const reply = await call('crash');
    expect(errorOf(reply)?.code).toBe(-32603);
    expect(errorOf(reply)?.message).toContain('SQLITE_BUSY');
  });

  it('rejects an unknown tool name', async () => {
    expect(errorOf(await call('nope'))?.code).toBe(-32602);
  });
});

describe('unsupported shapes', () => {
  it('declines batches, which the current protocol removed', async () => {
    const reply = await handleMcpMessage([{ jsonrpc: '2.0', id: 1, method: 'ping' }], REGISTRY);
    expect(reply.status).toBe(400);
    expect(errorOf(reply)?.message).toContain('batching is not supported');
  });

  it('answers an unknown method with method-not-found', async () => {
    const reply = await handleMcpMessage(
      { jsonrpc: '2.0', id: 1, method: 'resources/read' },
      REGISTRY,
    );
    expect(errorOf(reply)?.code).toBe(-32601);
  });

  it('answers probes for resources and prompts with empty lists', async () => {
    expect(
      resultOf(await handleMcpMessage({ jsonrpc: '2.0', id: 1, method: 'resources/list' }, REGISTRY)),
    ).toEqual({ resources: [] });
    expect(
      resultOf(await handleMcpMessage({ jsonrpc: '2.0', id: 1, method: 'prompts/list' }, REGISTRY)),
    ).toEqual({ prompts: [] });
  });
});

describe('checkApiKey', () => {
  it('accepts the key in x-api-key', () => {
    expect(checkApiKey(new Headers({ 'x-api-key': 'secret' }), 'secret')).toEqual({ ok: true });
  });

  it('accepts a bearer token as a fallback', () => {
    expect(checkApiKey(new Headers({ authorization: 'Bearer secret' }), 'secret')).toEqual({
      ok: true,
    });
  });

  it('prefers x-api-key, so basic auth for the proxy can travel alongside', () => {
    const headers = new Headers({
      authorization: 'Basic dXNlcjpwYXNz',
      'x-api-key': 'secret',
    });
    expect(checkApiKey(headers, 'secret')).toEqual({ ok: true });
  });

  it('rejects a wrong key and a missing one differently', () => {
    const wrong = checkApiKey(new Headers({ 'x-api-key': 'nope' }), 'secret');
    expect(wrong).toMatchObject({ ok: false, status: 401, message: 'Invalid API key.' });

    const missing = checkApiKey(new Headers(), 'secret');
    expect(missing).toMatchObject({ ok: false, status: 401 });
    expect(missing.ok === false && missing.message).toContain('Missing API key');
  });

  it('refuses everything when no key is configured', () => {
    const reply = checkApiKey(new Headers({ 'x-api-key': 'anything' }), null);
    expect(reply).toMatchObject({ ok: false, status: 503 });
    expect(reply.ok === false && reply.message).toContain('MCP_API_KEY');
  });
});
