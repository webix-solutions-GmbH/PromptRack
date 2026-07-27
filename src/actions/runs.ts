'use server';

import { revalidatePath } from 'next/cache';
import { redirect } from 'next/navigation';
import { and, asc, eq, inArray } from 'drizzle-orm';
import { db } from '@/db';
import {
  machineModels,
  machines,
  promptGroups,
  prompts,
  runResults,
  runs,
  systemPrompts,
} from '@/db/schema';
import { resolveEffectiveSystemPrompt, type SystemPromptMode } from '@/lib/system-prompt';

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
 * Creates a run and materializes one `run_results` row per prompt.
 *
 * Everything the run needs later — machine specs, group names, prompt text and
 * the *resolved* system prompt — is snapshotted here, so editing or deleting
 * prompts afterwards can never rewrite history.
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
 * Sets or clears a single result's good/bad rating. Passing `null` clears it.
 * `note` is optional — when omitted, the existing note is left untouched, so
 * clicking a thumb button never wipes a note the user already saved.
 */
export async function rateResult(
  resultId: number,
  rating: 'good' | 'bad' | null,
  note?: string | null,
) {
  const values: { rating: string | null; ratingNote?: string | null } = { rating };
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

export async function deleteRun(runId: number) {
  await db.delete(runs).where(eq(runs.id, runId));
  revalidatePath('/runs');
  redirect('/runs');
}
