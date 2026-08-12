import { describe, expect, it } from 'vitest';
import { eq } from 'drizzle-orm';
import { db } from '@/db';
import {
  machineModels,
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

const NOW = new Date('2026-07-27T09:46:00.000Z');

async function seedEverything() {
  const [machine] = await db
    .insert(machines)
    .values({ name: 'ki01', baseUrl: 'http://x/v1', createdAt: NOW, updatedAt: NOW })
    .returning({ id: machines.id });

  const [model] = await db
    .insert(machineModels)
    .values({
      machineId: machine.id,
      modelId: 'qwen3-32b',
      currentlyLoaded: true,
      firstSeenAt: NOW,
      lastSeenAt: NOW,
      source: 'discovered',
    })
    .returning({ id: machineModels.id });

  const [systemPrompt] = await db
    .insert(systemPrompts)
    .values({ name: 'terse', content: 'Be terse.', createdAt: NOW, updatedAt: NOW })
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
      parametersJson: '{}',
      mockResponse: 'shipped',
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
      title: 'Hello',
      content: 'Say hi.',
      systemPromptId: systemPrompt.id,
      createdAt: NOW,
      updatedAt: NOW,
    })
    .returning({ id: prompts.id });

  await db.insert(promptToolsets).values({ promptId: prompt.id, toolsetId: toolset.id });

  const [run] = await db
    .insert(runs)
    .values({
      machineId: machine.id,
      machineSnapshot: '{"name":"ki01"}',
      modelId: 'qwen3-32b',
      groupNames: '["General"]',
      status: 'completed',
      createdAt: NOW,
      startedAt: NOW,
      finishedAt: NOW,
    })
    .returning({ id: runs.id });

  const [result] = await db
    .insert(runResults)
    .values({
      runId: run.id,
      promptId: prompt.id,
      groupName: 'General',
      promptTitle: 'Hello',
      promptText: 'Say hi.',
      status: 'ok',
      tokensPerSec: 41.318472916393,
      tokensEstimated: true,
      rating: 'good',
      startedAt: NOW,
      finishedAt: NOW,
    })
    .returning({ id: runResults.id });

  return { machine, model, systemPrompt, toolset, tool, group, prompt, run, result };
}

describe('schema', () => {
  it('round-trips Date, boolean and double precision values', async () => {
    const { result } = await seedEverything();

    const [row] = await db.select().from(runResults).where(eq(runResults.id, result.id));

    expect(row.startedAt).toBeInstanceOf(Date);
    expect(row.startedAt?.getTime()).toBe(NOW.getTime());
    expect(row.tokensEstimated).toBe(true);
    // float8, not float4: the historical value must not be rounded.
    expect(row.tokensPerSec).toBe(41.318472916393);

    const [machineRow] = await db.select().from(machines);
    expect(machineRow.createdAt).toBeInstanceOf(Date);
    expect(machineRow.createdAt.getTime()).toBe(NOW.getTime());

    const [modelRow] = await db.select().from(machineModels);
    expect(modelRow.currentlyLoaded).toBe(true);
  });

  it('cascades run_results when a run is deleted', async () => {
    const { run } = await seedEverything();

    await db.delete(runs).where(eq(runs.id, run.id));

    expect(await db.select().from(runResults)).toHaveLength(0);
  });

  it('cascades tools when a toolset is deleted', async () => {
    const { toolset } = await seedEverything();

    await db.delete(toolsets).where(eq(toolsets.id, toolset.id));

    expect(await db.select().from(tools)).toHaveLength(0);
    // The prompt link is a cascade too; the prompt itself survives.
    expect(await db.select().from(promptToolsets)).toHaveLength(0);
    expect(await db.select().from(prompts)).toHaveLength(1);
  });

  it('nulls runs.machine_id when the machine is deleted, keeping the run', async () => {
    const { machine, run } = await seedEverything();

    await db.delete(machines).where(eq(machines.id, machine.id));

    const [row] = await db.select().from(runs).where(eq(runs.id, run.id));
    expect(row).toBeDefined();
    expect(row.machineId).toBeNull();
    // machine_models is a cascade, unlike runs.
    expect(await db.select().from(machineModels)).toHaveLength(0);
  });

  it('nulls run_results.prompt_id when the prompt is deleted, keeping the snapshot', async () => {
    const { prompt, result } = await seedEverything();

    await db.delete(prompts).where(eq(prompts.id, prompt.id));

    const [row] = await db.select().from(runResults).where(eq(runResults.id, result.id));
    expect(row).toBeDefined();
    expect(row.promptId).toBeNull();
    expect(row.promptText).toBe('Say hi.');
  });
});
