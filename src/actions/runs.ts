'use server';

import { revalidatePath } from 'next/cache';
import { redirect } from 'next/navigation';
import { eq } from 'drizzle-orm';
import { db } from '@/db';
import { runResults, runs } from '@/db/schema';
import type { Rating } from '@/lib/rating';
import { createRunRecord } from '@/lib/run-create';
import { isRunExecuting } from '@/lib/run-executor';

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
 * Creates a run from the new-run form.
 *
 * The snapshotting itself lives in `createRunRecord` so this path and the MCP
 * server share one implementation; here we only parse the form and navigate.
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

  const run = await createRunRecord({
    machineId,
    modelId,
    groupIds: selectedGroupIds(formData),
    params: buildParams(formData),
    comment: optionalString(formData, 'comment'),
  });

  revalidatePath('/runs');
  revalidatePath(`/machines/${run.machineId}`);
  redirect(`/runs/${run.runId}`);
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
  if (archived && (await isRunExecuting(runId))) {
    throw new Error('This run is currently executing — stop it before archiving.');
  }

  await db
    .update(runs)
    .set({ archivedAt: archived ? new Date() : null })
    .where(eq(runs.id, runId));

  revalidatePath('/runs');
  revalidatePath(`/runs/${runId}`);
  revalidatePath('/results');
  revalidatePath('/');
}

/**
 * Deletes a run and (via FK cascade) all of its results. Navigation after a
 * successful delete is the caller's job — a `redirect` here would throw inside
 * the client's try/catch and read as a failure.
 */
export async function deleteRun(runId: number) {
  if (await isRunExecuting(runId)) {
    throw new Error('This run is currently executing — stop it before deleting.');
  }

  await db.delete(runs).where(eq(runs.id, runId));
  revalidatePath('/runs');
  revalidatePath('/');
}
