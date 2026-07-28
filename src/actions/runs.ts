'use server';

import { revalidatePath } from 'next/cache';
import { redirect } from 'next/navigation';
import { and, asc, eq, inArray } from 'drizzle-orm';
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
import { probeLlmInfo } from '@/lib/llm-info';
import type { Rating } from '@/lib/rating';
import { isRunExecuting } from '@/lib/run-executor';
import { resolveEffectiveSystemPrompt, type SystemPromptMode } from '@/lib/system-prompt';
import {
  buildToolDefinitions,
  collectToolNameCollisions,
  type SnapshotTool,
  type ToolChoice,
  type ToolMode,
} from '@/lib/tools';

function optionalString(formData: FormData, key: string): string | null {
  const value = formData.get(key);
  if (typeof value !== 'string') return null;
  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : null;
}

function optionalNumber(formData: FormData, key: string, label: string): number | null {
  const raw = optionalString(formData, key);
  if (raw === null) return null;
  const value = Number(raw);
  if (!Number.isFinite(value)) {
    throw new Error(`${label} must be a number.`);
  }
  return value;
}

/**
 * Extra body fields sent to the endpoint. Empty inputs are omitted entirely so
 * the server keeps its own defaults rather than receiving `null`.
 */
function buildParams(formData: FormData): Record<string, number> | null {
  const params: Record<string, number> = {};

  const temperature = optionalNumber(formData, 'temperature', 'Temperature');
  if (temperature !== null) {
    if (temperature < 0 || temperature > 2) {
      throw new Error('Temperature must be between 0 and 2.');
    }
    params.temperature = temperature;
  }

  const maxTokens = optionalNumber(formData, 'maxTokens', 'Max tokens');
  if (maxTokens !== null) {
    if (!Number.isInteger(maxTokens) || maxTokens < 1) {
      throw new Error('Max tokens must be a positive whole number.');
    }
    params.max_tokens = maxTokens;
  }

  return Object.keys(params).length > 0 ? params : null;
}

function selectedGroupIds(formData: FormData): number[] {
  const ids = formData
    .getAll('groupIds')
    .map((value) => Number(value))
    .filter((value) => Number.isInteger(value));

  if (ids.length === 0) {
    throw new Error('Select at least one prompt group.');
  }
  return Array.from(new Set(ids));
}

/**
 * Freezes each prompt's tool configuration.
 *
 * Definitions and canned responses are content and travel with the run; an MCP
 * tool only records which toolset it came from, because its endpoint and auth
 * are credentials that must be read live at execution time.
 */
async function resolveToolSnapshots(
  promptIds: number[],
): Promise<Map<number, SnapshotTool[]>> {
  const byPrompt = new Map<number, SnapshotTool[]>();
  if (promptIds.length === 0) return byPrompt;

  const rows = await db
    .select({
      promptId: promptToolsets.promptId,
      toolsetId: toolsets.id,
      toolsetName: toolsets.name,
      toolName: tools.name,
      description: tools.description,
      parametersJson: tools.parametersJson,
      mockResponse: tools.mockResponse,
      enabled: tools.enabled,
      source: tools.source,
    })
    .from(promptToolsets)
    .innerJoin(toolsets, eq(promptToolsets.toolsetId, toolsets.id))
    .innerJoin(tools, eq(tools.toolsetId, toolsets.id))
    .where(inArray(promptToolsets.promptId, promptIds))
    .orderBy(
      asc(promptToolsets.promptId),
      asc(promptToolsets.sortOrder),
      asc(toolsets.id),
      asc(tools.name),
    );

  for (const row of rows) {
    if (!row.enabled) continue;

    const [definition] = buildToolDefinitions([
      {
        name: row.toolName,
        description: row.description,
        parametersJson: row.parametersJson,
      },
    ]);

    const list = byPrompt.get(row.promptId) ?? [];
    list.push({
      definition,
      source: row.source,
      toolsetId: row.toolsetId,
      toolsetName: row.toolsetName,
      mockResponse: row.mockResponse,
    });
    byPrompt.set(row.promptId, list);
  }

  return byPrompt;
}

/**
 * Creates a run and materializes one `run_results` row per prompt.
 *
 * Everything the run needs later — machine specs, group names, prompt text, the
 * *resolved* system prompt and the tool definitions — is snapshotted here, so
 * editing or deleting prompts and toolsets afterwards can never rewrite
 * history.
 */
export async function createRun(formData: FormData) {
  const machineId = Number(optionalString(formData, 'machineId'));
  if (!Number.isInteger(machineId)) {
    throw new Error('Select a machine.');
  }

  const modelId = optionalString(formData, 'modelId');
  if (!modelId) {
    throw new Error('Select or enter a model.');
  }

  const groupIds = selectedGroupIds(formData);
  const params = buildParams(formData);
  const comment = optionalString(formData, 'comment');

  const [machine] = await db.select().from(machines).where(eq(machines.id, machineId));
  if (!machine) {
    throw new Error('Machine not found.');
  }

  const groups = await db
    .select()
    .from(promptGroups)
    .where(inArray(promptGroups.id, groupIds))
    .orderBy(asc(promptGroups.sortOrder), asc(promptGroups.id));

  if (groups.length === 0) {
    throw new Error('The selected prompt groups no longer exist.');
  }

  const promptRows = await db
    .select()
    .from(prompts)
    .where(inArray(prompts.groupId, groups.map((group) => group.id)))
    .orderBy(asc(prompts.sortOrder), asc(prompts.id));

  if (promptRows.length === 0) {
    throw new Error('The selected prompt groups contain no prompts.');
  }

  const systemPromptRows = await db.select().from(systemPrompts);
  const systemPromptById = new Map(systemPromptRows.map((row) => [row.id, row]));

  const toolSnapshots = await resolveToolSnapshots(
    promptRows.filter((prompt) => prompt.toolMode !== 'none').map((prompt) => prompt.id),
  );

  // A tool test with no usable tools would quietly become an ordinary prompt
  // and produce a result that looks meaningful but measured nothing. Refuse
  // before anything is written, and name the prompt that needs fixing.
  for (const prompt of promptRows) {
    if (prompt.toolMode === 'none') continue;

    const snapshot = toolSnapshots.get(prompt.id) ?? [];
    if (snapshot.length === 0) {
      throw new Error(
        `Prompt "${prompt.title}" has tool mode "${prompt.toolMode}" but no enabled tools. Pick a toolset or set the mode back to none.`,
      );
    }

    const collisions = collectToolNameCollisions(
      snapshot.map((entry) => ({ name: entry.definition.function.name })),
    );
    if (collisions.length > 0) {
      throw new Error(
        `Prompt "${prompt.title}" selects toolsets that both define: ${collisions.join(', ')}. Tool names must be unique within one prompt.`,
      );
    }
  }

  // Ask the endpoint about itself (server software, model metadata) and freeze
  // the answer with the run. Best-effort: an unreachable or tight-lipped server
  // just leaves the snapshot empty.
  const llmInfo = await probeLlmInfo({
    baseUrl: machine.baseUrl,
    apiKey: machine.apiKey,
    modelId,
  });

  const now = Date.now();

  const [run] = await db
    .insert(runs)
    .values({
      machineId: machine.id,
      machineSnapshot: JSON.stringify({
        name: machine.name,
        base_url: machine.baseUrl,
        cpu: machine.cpu,
        ram: machine.ram,
        gpu: machine.gpu,
      }),
      modelId,
      params: params ? JSON.stringify(params) : null,
      llmInfo: llmInfo ? JSON.stringify(llmInfo) : null,
      comment,
      groupNames: JSON.stringify(groups.map((group) => group.name)),
      status: 'pending',
      createdAt: now,
    })
    .returning({ id: runs.id });

  let sortOrder = 0;
  for (const group of groups) {
    const groupPrompts = promptRows.filter((prompt) => prompt.groupId === group.id);
    for (const prompt of groupPrompts) {
      const base = prompt.systemPromptId
        ? (systemPromptById.get(prompt.systemPromptId)?.content ?? null)
        : null;

      const snapshot = prompt.toolMode === 'none' ? [] : (toolSnapshots.get(prompt.id) ?? []);

      await db.insert(runResults).values({
        runId: run.id,
        promptId: prompt.id,
        sortOrder: sortOrder++,
        groupName: group.name,
        promptTitle: prompt.title,
        promptText: prompt.content,
        expectedOutput: prompt.expectedOutput,
        systemPromptText: resolveEffectiveSystemPrompt({
          mode: prompt.systemPromptMode as SystemPromptMode,
          baseContent: base,
          customText: prompt.customSystemText,
        }),
        toolsSnapshot: snapshot.length > 0 ? JSON.stringify(snapshot) : null,
        toolMode: prompt.toolMode as ToolMode,
        toolChoice: prompt.toolChoice as ToolChoice | null,
        maxTurns: prompt.maxTurns,
        status: 'pending',
      });
    }
  }

  // Remember the model against the machine so the next run can offer it even
  // when it was typed by hand and never showed up in /models.
  const [existingModel] = await db
    .select({ id: machineModels.id })
    .from(machineModels)
    .where(and(eq(machineModels.machineId, machine.id), eq(machineModels.modelId, modelId)));

  if (existingModel) {
    await db
      .update(machineModels)
      .set({ lastSeenAt: now })
      .where(eq(machineModels.id, existingModel.id));
  } else {
    await db.insert(machineModels).values({
      machineId: machine.id,
      modelId,
      source: 'run',
      currentlyLoaded: false,
      firstSeenAt: now,
      lastSeenAt: now,
    });
  }

  revalidatePath('/runs');
  revalidatePath(`/machines/${machine.id}`);
  redirect(`/runs/${run.id}`);
}

export async function updateRunComment(runId: number, comment: string) {
  const trimmed = comment.trim();

  await db
    .update(runs)
    .set({ comment: trimmed.length > 0 ? trimmed : null })
    .where(eq(runs.id, runId));

  revalidatePath('/runs');
  revalidatePath(`/runs/${runId}`);
}

/**
 * Sets or clears a single result's rating (`good` / `meh` / `bad`). Passing
 * `null` clears it. `note` is optional — when omitted, the existing note is left
 * untouched, so clicking a rating button never wipes a note the user saved.
 */
export async function rateResult(
  resultId: number,
  rating: Rating | null,
  note?: string | null,
) {
  const values: { rating: Rating | null; ratingNote?: string | null } = { rating };
  if (note !== undefined) {
    const trimmed = note?.trim() ?? '';
    values.ratingNote = trimmed.length > 0 ? trimmed : null;
  }

  const [result] = await db
    .update(runResults)
    .set(values)
    .where(eq(runResults.id, resultId))
    .returning({ runId: runResults.runId });

  if (result) {
    revalidatePath(`/runs/${result.runId}`);
    revalidatePath('/runs');
    revalidatePath('/');
  }
}

/** Saves a result's free-text rating note independently of its good/bad rating. */
export async function updateResultNote(resultId: number, note: string) {
  const trimmed = note.trim();

  const [result] = await db
    .update(runResults)
    .set({ ratingNote: trimmed.length > 0 ? trimmed : null })
    .where(eq(runResults.id, resultId))
    .returning({ runId: runResults.runId });

  if (result) {
    revalidatePath(`/runs/${result.runId}`);
  }
}

/**
 * Archives or unarchives a run.
 *
 * Archiving only hides a run from the default lists; it never touches results
 * or `status`, so an archived run that still has pending rows can be unarchived
 * and resumed. Refuses while the run is executing, for the same reason delete
 * does — the list the user is looking at would be lying about it.
 */
export async function setRunArchived(runId: number, archived: boolean) {
  if (archived && isRunExecuting(runId)) {
    throw new Error('This run is currently executing — stop it before archiving.');
  }

  await db
    .update(runs)
    .set({ archivedAt: archived ? Date.now() : null })
    .where(eq(runs.id, runId));

  revalidatePath('/runs');
  revalidatePath(`/runs/${runId}`);
  revalidatePath('/compare');
  revalidatePath('/');
}

/**
 * Deletes a run and (via FK cascade) all of its results. Navigation after a
 * successful delete is the caller's job — a `redirect` here would throw inside
 * the client's try/catch and read as a failure.
 */
export async function deleteRun(runId: number) {
  if (isRunExecuting(runId)) {
    throw new Error('This run is currently executing — stop it before deleting.');
  }

  await db.delete(runs).where(eq(runs.id, runId));
  revalidatePath('/runs');
  revalidatePath('/');
}
