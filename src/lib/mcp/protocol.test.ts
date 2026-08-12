import { beforeEach, describe, expect, it } from 'vitest';
import type { Role } from '@/lib/auth/policy';
import { McpToolError } from './args';
import {
  handleMcpMessage,
  LATEST_PROTOCOL_VERSION,
  type McpCallContext,
  type McpToolSpec,
} from './protocol';

const invoked: string[] = [];

const REGISTRY: McpToolSpec[] = [
  {
    name: 'echo',
    description: 'Echoes its argument.',
    readOnly: true,
    inputSchema: { type: 'object', properties: { value: { type: 'string' } } },
    handler: async (args) => {
      invoked.push('echo');
      return { value: args.value ?? null };
    },
  },
  {
    name: 'write_something',
    description: 'Writes.',
    inputSchema: { type: 'object', properties: {} },
    handler: async () => {
      invoked.push('write_something');
      return { ok: true };
    },
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

function ctx(role: Role = 'member'): McpCallContext {
  return { actor: { userId: 'u1', email: 'user@example.com', role } };
}

function call(name: string, args?: unknown, role: Role = 'member') {
  return handleMcpMessage(
    { jsonrpc: '2.0', id: 1, method: 'tools/call', params: { name, arguments: args } },
    REGISTRY,
    ctx(role),
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
      ctx(),
    );
    expect(resultOf(reply).protocolVersion).toBe('2025-03-26');
  });

  it('falls back to its own latest version for anything unknown', async () => {
    const reply = await handleMcpMessage(
      { jsonrpc: '2.0', id: 1, method: 'initialize', params: { protocolVersion: '1999-01-01' } },
      REGISTRY,
      ctx(),
    );
    expect(resultOf(reply).protocolVersion).toBe(LATEST_PROTOCOL_VERSION);
  });

  it('advertises only tools', async () => {
    const reply = await handleMcpMessage({ jsonrpc: '2.0', id: 1, method: 'initialize' }, REGISTRY, ctx());
    expect(resultOf(reply).capabilities).toEqual({ tools: { listChanged: false } });
  });
});

describe('notifications', () => {
  it('are acknowledged with 202 and no body', async () => {
    const reply = await handleMcpMessage(
      { jsonrpc: '2.0', method: 'notifications/initialized' },
      REGISTRY,
      ctx(),
    );
    expect(reply).toEqual({ status: 202, body: null });
  });
});

describe('tools/list', () => {
  it('describes every tool without leaking the handler', async () => {
    const reply = await handleMcpMessage({ jsonrpc: '2.0', id: 1, method: 'tools/list' }, REGISTRY, ctx());
    const tools = resultOf(reply).tools as Record<string, unknown>[];

    expect(tools.map((tool) => tool.name)).toEqual([
      'echo',
      'write_something',
      'refuse',
      'crash',
    ]);
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
    const reply = await handleMcpMessage([{ jsonrpc: '2.0', id: 1, method: 'ping' }], REGISTRY, ctx());
    expect(reply.status).toBe(400);
    expect(errorOf(reply)?.message).toContain('batching is not supported');
  });

  it('answers an unknown method with method-not-found', async () => {
    const reply = await handleMcpMessage(
      { jsonrpc: '2.0', id: 1, method: 'resources/read' },
      REGISTRY,
      ctx(),
    );
    expect(errorOf(reply)?.code).toBe(-32601);
  });

  it('answers probes for resources and prompts with empty lists', async () => {
    expect(
      resultOf(await handleMcpMessage({ jsonrpc: '2.0', id: 1, method: 'resources/list' }, REGISTRY, ctx())),
    ).toEqual({ resources: [] });
    expect(
      resultOf(await handleMcpMessage({ jsonrpc: '2.0', id: 1, method: 'prompts/list' }, REGISTRY, ctx())),
    ).toEqual({ prompts: [] });
  });
});

describe('the actor\'s role gates tools/call', () => {
  beforeEach(() => {
    invoked.length = 0;
  });

  it('lets a viewer call a read-only tool', async () => {
    const result = resultOf(await call('echo', { value: 'hi' }, 'viewer'));
    expect(result.isError).toBe(false);
    expect(invoked).toEqual(['echo']);
  });

  it('refuses a viewer a writing tool as isError content, not a JSON-RPC error', async () => {
    const reply = await call('write_something', {}, 'viewer');
    const result = resultOf(reply);
    expect(errorOf(reply)).toBeUndefined();
    expect(result.isError).toBe(true);
    expect(JSON.parse((result.content as { text: string }[])[0].text)).toEqual({
      error: 'The token\'s account is read-only; "write_something" writes.',
    });
  });

  it('never reaches the handler of a tool it refuses', async () => {
    await call('write_something', {}, 'viewer');
    expect(invoked).toEqual([]);
  });

  it('lets a member and an admin write', async () => {
    expect(resultOf(await call('write_something', {}, 'member')).isError).toBe(false);
    expect(resultOf(await call('write_something', {}, 'admin')).isError).toBe(false);
    expect(invoked).toEqual(['write_something', 'write_something']);
  });
});

describe('initialize instructions', () => {
  it('name the authenticated account, so a transcript records who acted', async () => {
    const reply = await handleMcpMessage(
      { jsonrpc: '2.0', id: 1, method: 'initialize' },
      REGISTRY,
      ctx('viewer'),
    );
    const instructions = resultOf(reply).instructions as string;
    expect(instructions).toContain('user@example.com');
    expect(instructions).toContain('read-only');
  });
});
