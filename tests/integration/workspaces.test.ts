/**
 * Cross-workspace isolation, against a real Postgres.
 *
 * The fixture builds two workspaces holding a **byte-identical** prompt (same
 * title, same text). That sameness is the whole point: `/results` falls back to
 * matching results by normalised prompt text once a prompt has been deleted, and
 * two workspaces' identical prompts are exactly what that fallback could
 * collapse into one row.
 */

import { describe, expect, it, vi } from 'vitest';
import { eq } from 'drizzle-orm';
import { db } from '@/db';
import { promptToolsets, prompts, runResults, runs, systemPrompts } from '@/db/schema';
import type { Scope } from '@/db/scope';
import { countCustomerContent, listCustomers } from '@/db/repo/customers';
import { createMachine } from '@/db/repo/machines';
import { createGroup, comparePromptRows, createPrompt, listToolsetLinks, replaceToolsetLinks } from '@/db/repo/prompts';
import { compareCellsForModels, modelColumnInputs } from '@/db/repo/results';
import { createRun, insertRunResults, scopeForRun } from '@/db/repo/runs';
import { createSystemPrompt } from '@/db/repo/system-prompts';
import { createTool, createToolset, listMcpServers } from '@/db/repo/toolsets';
import { createWorkspace } from './setup';

// Server actions and MCP tools call `revalidatePath`, which needs a request.
vi.mock('next/cache', () => ({ revalidatePath: () => {} }));

// The role gates are Phase 4's concern and need a session; these tests are about
// the workspace scope that sits *behind* them.
vi.mock('@/lib/auth/guards', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/auth/guards')>();
  const actor = {
    userId: 'u-admin',
    email: 'admin@example.com',
    name: 'Admin',
    role: 'admin' as const,
    via: 'session' as const,
  };
  return {
    ...actual,
    currentActor: async () => actor,
    requireActor: async () => actor,
    requireWriter: async () => actor,
    requireAdmin: async () => actor,
  };
});

vi.mock('@/lib/llm-info', () => ({ probeLlmInfo: async () => null }));

const { createRunRecord } = await import('@/lib/run-create');
const { deleteCustomer } = await import('@/actions/customers');
const { handleMcpMessage } = await import('@/lib/mcp/protocol');
const { MCP_TOOLS } = await import('@/lib/mcp/registry');

const NOW = new Date('2026-08-12T10:00:00.000Z');

/** Byte-identical in both workspaces — see the file comment. */
const PROMPT_TITLE = 'Order status';
const PROMPT_TEXT = 'Where is my order 4711?';

interface Workspace {
  id: number;
  scope: Scope;
  machineId: number;
  systemPromptId: number;
  toolsetId: number;
  groupId: number;
  promptId: number;
  runId: number;
  resultId: number;
}

async function buildWorkspace(name: string): Promise<Workspace> {
  const { id, scope } = await createWorkspace(name);

  const machine = await createMachine(scope, {
    name: `${name} box`,
    baseUrl: `http://127.0.0.1:9/${name}/v1`,
    apiKey: null,
    cpu: null,
    ram: null,
    gpu: null,
    notes: null,
    createdAt: NOW,
    updatedAt: NOW,
  });

  const systemPrompt = await createSystemPrompt(scope, {
    name: `${name} base`,
    content: `SYSTEM TEXT OF ${name}`,
    now: NOW,
  });

  const toolset = await createToolset(scope, {
    name: `${name} tools`,
    description: null,
    kind: 'mcp',
    mcpUrl: `http://127.0.0.1:9/${name}/mcp`,
    mcpHeaders: null,
    now: NOW,
  });

  await createTool(scope, toolset.id, {
    name: 'lookup_order',
    description: 'Look up an order.',
    parametersJson: '{}',
    mockResponse: null,
    now: NOW,
  });

  const group = await createGroup(scope, { name: 'General', description: null, now: NOW });

  const prompt = await createPrompt(scope, {
    groupId: group.id,
    title: PROMPT_TITLE,
    content: PROMPT_TEXT,
    expectedOutput: null,
    systemPromptId: systemPrompt.id,
    systemPromptMode: 'append',
    customSystemText: null,
    toolMode: 'none',
    toolChoice: null,
    maxTurns: 6,
    sortOrder: 0,
    createdAt: NOW,
    updatedAt: NOW,
  });

  const run = await createRun(scope, {
    machineId: machine.id,
    machineSnapshot: JSON.stringify({ name: `${name} box` }),
    modelId: 'qwen3-32b',
    groupNames: '["General"]',
    status: 'completed',
    createdAt: NOW,
    startedAt: NOW,
    finishedAt: NOW,
  });

  await insertRunResults(scope, run.id, [
    {
      runId: run.id,
      promptId: prompt.id,
      sortOrder: 0,
      groupName: 'General',
      promptTitle: PROMPT_TITLE,
      promptText: PROMPT_TEXT,
      status: 'ok',
      responseText: `ANSWER FROM ${name}`,
      startedAt: NOW,
      finishedAt: NOW,
    },
  ]);

  const [result] = await db.select().from(runResults).where(eq(runResults.runId, run.id));

  return {
    id,
    scope,
    machineId: machine.id,
    systemPromptId: systemPrompt.id,
    toolsetId: toolset.id,
    groupId: group.id,
    promptId: prompt.id,
    runId: run.id,
    resultId: result.id,
  };
}

async function bothWorkspaces() {
  // Sequential, not Promise.all: the ids are easier to reason about in a failure
  // and there is nothing to gain from two round trips overlapping.
  const a = await buildWorkspace('A');
  const b = await buildWorkspace('B');
  return { a, b };
}

/** One MCP tool call, run as if the connection had sent `X-Customer: <id>`. */
async function mcpCall(customerId: number, name: string, args: Record<string, unknown>) {
  const reply = await handleMcpMessage(
    { jsonrpc: '2.0', id: 1, method: 'tools/call', params: { name, arguments: args } },
    MCP_TOOLS,
    {
      actor: { userId: 'u-admin', email: 'admin@example.com', role: 'admin' },
      source: { header: { kind: 'id', id: customerId }, tokenDefault: null },
    },
  );
  const result = (reply.body as { result: { isError: boolean; content: { text: string }[] } })
    .result;
  return { isError: result.isError, payload: JSON.parse(result.content[0].text) as unknown };
}

describe('cross-workspace isolation', () => {
  it('refuses to run another workspace\'s prompt group, and writes nothing', async () => {
    const { a, b } = await bothWorkspaces();
    const before = await db.select().from(runs);

    await expect(
      createRunRecord(a.scope, {
        machineId: a.machineId,
        modelId: 'qwen3-32b',
        groupIds: [b.groupId],
      }),
    ).rejects.toThrow('no longer exist');

    expect(await db.select().from(runs)).toHaveLength(before.length);
  });

  it('never resolves a foreign system prompt into a run snapshot', async () => {
    const { a, b } = await bothWorkspaces();

    const own = await createRunRecord(a.scope, {
      machineId: a.machineId,
      modelId: 'qwen3-32b',
      groupIds: [a.groupId],
    });
    const [fromOwn] = await db
      .select()
      .from(runResults)
      .where(eq(runResults.runId, own.runId));
    expect(fromOwn.systemPromptText).toBe('SYSTEM TEXT OF A');

    // Force B's system prompt id onto A's prompt, behind the repository's back.
    // `createRunRecord` used to read the *whole* system_prompts table to build
    // its lookup map, which would have frozen B's text into A's run.
    await db
      .update(prompts)
      .set({ systemPromptId: b.systemPromptId })
      .where(eq(prompts.id, a.promptId));

    const forged = await createRunRecord(a.scope, {
      machineId: a.machineId,
      modelId: 'qwen3-32b',
      groupIds: [a.groupId],
    });
    const [fromForged] = await db
      .select()
      .from(runResults)
      .where(eq(runResults.runId, forged.runId));

    // Null, not "SYSTEM TEXT OF B": the scoped lookup simply cannot see it.
    expect(fromForged.systemPromptText).toBeNull();
  });

  it('keeps the results page inside one workspace, deleted prompts included', async () => {
    const { a, b } = await bothWorkspaces();

    // The exact shape that made text matching necessary: both prompts gone, so
    // `prompt_id` is null on both sides and only the text is left to match on.
    await db.delete(prompts).where(eq(prompts.id, a.promptId));
    await db.delete(prompts).where(eq(prompts.id, b.promptId));

    const inputs = await modelColumnInputs(a.scope);
    expect(inputs.runs.map((run) => run.id)).toEqual([a.runId]);
    expect(inputs.results.map((row) => row.runId)).toEqual([a.runId]);

    // A URL naming both machines cannot pull B's results across.
    const cells = await compareCellsForModels(
      a.scope,
      [
        { machineId: a.machineId, modelId: 'qwen3-32b' },
        { machineId: b.machineId, modelId: 'qwen3-32b' },
      ],
      null,
    );
    expect(cells.map((cell) => cell.result.id)).toEqual([a.resultId]);
    expect(cells.map((cell) => cell.result.responseText)).toEqual(['ANSWER FROM A']);
  });

  it('hides another workspace\'s live prompts from the results rows', async () => {
    const { a } = await bothWorkspaces();
    const rows = await comparePromptRows(a.scope);
    expect(rows.map((row) => row.id)).toEqual([a.promptId]);
  });

  it('answers "no such result" for a foreign result id over MCP, and changes nothing', async () => {
    const { a, b } = await bothWorkspaces();

    const read = await mcpCall(a.id, 'get_run_result', { result_id: b.resultId });
    expect(read.isError).toBe(true);
    expect(read.payload).toEqual({ error: `No run result with id ${b.resultId}.` });

    const rated = await mcpCall(a.id, 'set_rating', {
      result_id: b.resultId,
      rating: 'bad',
      note: 'should never land',
    });
    expect(rated.isError).toBe(true);
    expect(rated.payload).toEqual({ error: `No run result with id ${b.resultId}.` });

    const [row] = await db.select().from(runResults).where(eq(runResults.id, b.resultId));
    expect(row.rating).toBeNull();
    expect(row.ratingNote).toBeNull();
  });

  it('refuses a prompt update naming a toolset from another workspace', async () => {
    const { a, b } = await bothWorkspaces();

    const reply = await mcpCall(a.id, 'update_prompt', {
      prompt_id: a.promptId,
      tool_mode: 'execute',
      toolsets: [b.toolsetId],
    });

    expect(reply.isError).toBe(true);
    expect(JSON.stringify(reply.payload)).toContain(`No toolset with id ${b.toolsetId}`);

    expect(await listToolsetLinks(a.scope, [a.promptId])).toHaveLength(0);
    const [prompt] = await db.select().from(prompts).where(eq(prompts.id, a.promptId));
    expect(prompt.toolMode).toBe('none');
  });

  it('refuses to link a foreign toolset even below the MCP layer', async () => {
    const { a, b } = await bothWorkspaces();

    await expect(replaceToolsetLinks(a.scope, a.promptId, [b.toolsetId])).rejects.toThrow(
      'no longer exists in this workspace',
    );
    expect(await db.select().from(promptToolsets)).toHaveLength(0);
  });

  it('resolves no MCP server for a foreign toolset id in a run snapshot', async () => {
    const { a, b } = await bothWorkspaces();

    // What the executor does: derive the scope from the run row, then look the
    // snapshot's toolset ids up live. A's toolset must be invisible to B's run,
    // so `buildMcpExecutor` finds no server and answers the model with an error
    // string instead of calling A's endpoint with A's credentials.
    const found = await scopeForRun(b.runId);
    expect(found).not.toBeNull();

    expect(await listMcpServers(found!.scope, [a.toolsetId])).toEqual([]);
    // …while B's own toolset still resolves, so the test would catch an
    // over-broad predicate too.
    expect(await listMcpServers(found!.scope, [b.toolsetId])).toHaveLength(1);
  });

  it('refuses to delete a workspace that still holds content, then allows it', async () => {
    const { a } = await bothWorkspaces();

    await expect(deleteCustomer(a.id)).rejects.toThrow(/still holds/);
    await expect(deleteCustomer(a.id)).rejects.toThrow(/1 machine, 1 system prompt/);

    // Emptying it in FK order; `runs` before `machines` because a run keeps its
    // machine reference, and everything before `customers` because the workspace
    // FK is RESTRICT by design.
    await db.delete(runs).where(eq(runs.customerId, a.id));
    const { deleteGroup } = await import('@/db/repo/prompts');
    const { deleteToolset } = await import('@/db/repo/toolsets');
    const { deleteMachine } = await import('@/db/repo/machines');
    await deleteGroup(a.scope, a.groupId);
    await deleteToolset(a.scope, a.toolsetId);
    await deleteMachine(a.scope, a.machineId);
    await db.delete(systemPrompts).where(eq(systemPrompts.id, a.systemPromptId));

    expect(await countCustomerContent(a.id)).toEqual({
      machines: 0,
      systemPrompts: 0,
      toolsets: 0,
      promptGroups: 0,
      runs: 0,
    });

    await deleteCustomer(a.id);
    expect((await listCustomers()).map((row) => row.id)).not.toContain(a.id);
  });
});
