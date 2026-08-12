import { beforeEach, describe, expect, it, vi } from 'vitest';
import { eq } from 'drizzle-orm';
import { db } from '@/db';
import { currentScope } from '@/db/scope';
import {
  machines,
  promptGroups,
  promptToolsets,
  prompts,
  runResults,
  runs,
  systemPrompts,
  tools,
  toolsets,
} from '@/db/schema';

const state = vi.hoisted(() => ({ failResolve: false }));

// createRunRecord probes the endpoint before it writes anything; there is no
// endpoint here, and the probe is not what these tests are about.
vi.mock('@/lib/llm-info', () => ({ probeLlmInfo: async () => null }));

// The rollback test needs a failure *after* the `runs` insert. Resolving the
// effective system prompt happens while the result rows are being built, which
// is exactly inside the transaction.
vi.mock('@/lib/system-prompt', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/system-prompt')>();
  return {
    ...actual,
    resolveEffectiveSystemPrompt: (input: Parameters<typeof actual.resolveEffectiveSystemPrompt>[0]) => {
      if (state.failResolve) throw new Error('simulated failure mid-transaction');
      return actual.resolveEffectiveSystemPrompt(input);
    },
  };
});

const { createRunRecord } = await import('@/lib/run-create');

const NOW = new Date('2026-07-27T09:46:00.000Z');

async function seedFixture() {
  const [machine] = await db
    .insert(machines)
    .values({ name: 'ki01', baseUrl: 'http://127.0.0.1:9/v1', createdAt: NOW, updatedAt: NOW })
    .returning({ id: machines.id });

  const [systemPrompt] = await db
    .insert(systemPrompts)
    .values({ name: 'base', content: 'BASE SYSTEM TEXT', createdAt: NOW, updatedAt: NOW })
    .returning({ id: systemPrompts.id });

  const [toolset] = await db
    .insert(toolsets)
    .values({ name: 'Support Desk', kind: 'manual', createdAt: NOW, updatedAt: NOW })
    .returning({ id: toolsets.id });

  const [tool] = await db
    .insert(tools)
    .values({
      toolsetId: toolset.id,
      name: 'lookup_order',
      description: 'Look up an order.',
      parametersJson: JSON.stringify({ type: 'object', properties: {} }),
      mockResponse: 'ORIGINAL MOCK RESPONSE',
      enabled: true,
      source: 'manual',
      firstSeenAt: NOW,
      lastSeenAt: NOW,
    })
    .returning({ id: tools.id });

  const [group] = await db
    .insert(promptGroups)
    .values({ name: 'General', sortOrder: 0, createdAt: NOW })
    .returning({ id: promptGroups.id });

  const [prompt] = await db
    .insert(prompts)
    .values({
      groupId: group.id,
      title: 'Order status',
      content: 'ORIGINAL PROMPT TEXT',
      systemPromptId: systemPrompt.id,
      systemPromptMode: 'append',
      customSystemText: 'CUSTOM TAIL',
      toolMode: 'execute',
      maxTurns: 4,
      sortOrder: 10,
      createdAt: NOW,
      updatedAt: NOW,
    })
    .returning({ id: prompts.id });

  await db.insert(promptToolsets).values({ promptId: prompt.id, toolsetId: toolset.id });

  return { machine, systemPrompt, toolset, tool, group, prompt };
}

beforeEach(() => {
  state.failResolve = false;
});

describe('createRunRecord', () => {
  it('freezes prompt text, system prompt and tools against later edits', async () => {
    const fixture = await seedFixture();

    const created = await createRunRecord(await currentScope(), {
      machineId: fixture.machine.id,
      modelId: 'qwen3-32b',
      groupIds: [fixture.group.id],
    });
    expect(created.resultCount).toBe(1);

    const [before] = await db
      .select()
      .from(runResults)
      .where(eq(runResults.runId, created.runId));

    expect(before.promptText).toBe('ORIGINAL PROMPT TEXT');
    expect(before.systemPromptText).toBe('BASE SYSTEM TEXT\n\nCUSTOM TAIL');
    expect(before.toolsSnapshot).toContain('ORIGINAL MOCK RESPONSE');

    // Now change everything the snapshot came from.
    await db
      .update(prompts)
      .set({ content: 'EDITED PROMPT TEXT' })
      .where(eq(prompts.id, fixture.prompt.id));
    await db
      .update(systemPrompts)
      .set({ content: 'EDITED SYSTEM TEXT' })
      .where(eq(systemPrompts.id, fixture.systemPrompt.id));
    await db
      .update(tools)
      .set({ mockResponse: 'EDITED MOCK RESPONSE' })
      .where(eq(tools.id, fixture.tool.id));
    await db.delete(toolsets).where(eq(toolsets.id, fixture.toolset.id));

    const [after] = await db
      .select()
      .from(runResults)
      .where(eq(runResults.runId, created.runId));

    expect(after.promptText).toBe('ORIGINAL PROMPT TEXT');
    expect(after.systemPromptText).toBe('BASE SYSTEM TEXT\n\nCUSTOM TAIL');
    expect(after.toolsSnapshot).toBe(before.toolsSnapshot);
    expect(after.toolsSnapshot).toContain('ORIGINAL MOCK RESPONSE');
    // The FK survives for cross-run comparison; the snapshot is what renders.
    expect(after.promptId).toBe(fixture.prompt.id);
  });

  it('rolls the run back when a result row cannot be written', async () => {
    const fixture = await seedFixture();
    state.failResolve = true;

    await expect(
      createRunRecord(await currentScope(), {
        machineId: fixture.machine.id,
        modelId: 'qwen3-32b',
        groupIds: [fixture.group.id],
      }),
    ).rejects.toThrow('simulated failure mid-transaction');

    // Without the transaction the `runs` row would be left behind, and Resume
    // would report an empty run as finished.
    expect(await db.select().from(runs)).toHaveLength(0);
    expect(await db.select().from(runResults)).toHaveLength(0);
  });
});
