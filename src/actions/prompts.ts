'use server';

import { revalidatePath } from 'next/cache';
import { redirect } from 'next/navigation';
import { eq } from 'drizzle-orm';
import { db } from '@/db';
import { promptGroups, promptToolsets, prompts } from '@/db/schema';
import { normalizeMaxTurns, type ToolChoice, type ToolMode } from '@/lib/tools';

function requiredString(formData: FormData, key: string): string {
  const value = formData.get(key);
  const trimmed = typeof value === 'string' ? value.trim() : '';
  if (!trimmed) {
    throw new Error(`${key} is required.`);
  }
  return trimmed;
}

function optionalString(formData: FormData, key: string): string | null {
  const value = formData.get(key);
  if (typeof value !== 'string') return null;
  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : null;
}

function optionalId(formData: FormData, key: string): number | null {
  const raw = optionalString(formData, key);
  if (raw === null) return null;
  const id = Number(raw);
  return Number.isInteger(id) ? id : null;
}

function requiredMode(formData: FormData): 'append' | 'override' {
  const value = formData.get('systemPromptMode');
  return value === 'override' ? 'override' : 'append';
}

function requiredToolMode(formData: FormData): ToolMode {
  const value = formData.get('toolMode');
  if (value === 'definitions' || value === 'execute') return value;
  return 'none';
}

/** Empty means "leave `tool_choice` out of the request entirely". */
function optionalToolChoice(formData: FormData): ToolChoice | null {
  const value = formData.get('toolChoice');
  if (value === 'auto' || value === 'required' || value === 'none') return value;
  return null;
}

function selectedToolsetIds(formData: FormData): number[] {
  const ids = formData
    .getAll('toolsetIds')
    .map((value) => Number(value))
    .filter((value) => Number.isInteger(value) && value > 0);

  return Array.from(new Set(ids));
}

/** Tool settings shared by create and update. */
function toolFields(formData: FormData) {
  return {
    toolMode: requiredToolMode(formData),
    toolChoice: optionalToolChoice(formData),
    maxTurns: normalizeMaxTurns(optionalId(formData, 'maxTurns')),
  };
}

/**
 * Replaces a prompt's toolset links. Rewriting the set is simpler than diffing
 * it and the table holds a handful of rows per prompt.
 */
async function replaceToolsetLinks(promptId: number, toolsetIds: number[]) {
  await db.delete(promptToolsets).where(eq(promptToolsets.promptId, promptId));

  if (toolsetIds.length === 0) return;

  await db.insert(promptToolsets).values(
    toolsetIds.map((toolsetId, index) => ({
      promptId,
      toolsetId,
      sortOrder: index,
    })),
  );
}

// ---------------------------------------------------------------------------
// Prompt groups
// ---------------------------------------------------------------------------

export async function createGroup(formData: FormData) {
  const name = requiredString(formData, 'name');
  const description = optionalString(formData, 'description');

  const [row] = await db
    .insert(promptGroups)
    .values({
      name,
      description,
      sortOrder: 0,
      createdAt: new Date(),
    })
    .returning({ id: promptGroups.id });

  revalidatePath('/prompts');
  redirect(`/prompts?group=${row.id}`);
}

export async function updateGroup(id: number, formData: FormData) {
  const name = requiredString(formData, 'name');
  const description = optionalString(formData, 'description');

  await db
    .update(promptGroups)
    .set({ name, description })
    .where(eq(promptGroups.id, id));

  revalidatePath('/prompts');
}

export async function deleteGroup(id: number) {
  await db.delete(promptGroups).where(eq(promptGroups.id, id));
  revalidatePath('/prompts');
}

// ---------------------------------------------------------------------------
// Prompts
// ---------------------------------------------------------------------------

function requiredGroupId(formData: FormData): number {
  const groupId = optionalId(formData, 'groupId');
  if (groupId === null) {
    throw new Error('groupId is required.');
  }
  return groupId;
}

export async function createPrompt(formData: FormData) {
  const groupId = requiredGroupId(formData);
  const title = requiredString(formData, 'title');
  const content = requiredString(formData, 'content');
  const expectedOutput = optionalString(formData, 'expectedOutput');
  const systemPromptId = optionalId(formData, 'systemPromptId');
  const systemPromptMode = requiredMode(formData);
  const customSystemText = optionalString(formData, 'customSystemText');
  const now = new Date();

  const [row] = await db
    .insert(prompts)
    .values({
      groupId,
      title,
      content,
      expectedOutput,
      systemPromptId,
      systemPromptMode,
      customSystemText,
      ...toolFields(formData),
      sortOrder: 0,
      createdAt: now,
      updatedAt: now,
    })
    .returning({ id: prompts.id });

  await replaceToolsetLinks(row.id, selectedToolsetIds(formData));

  revalidatePath('/prompts');
}

export async function updatePrompt(id: number, formData: FormData) {
  const title = requiredString(formData, 'title');
  const content = requiredString(formData, 'content');
  const expectedOutput = optionalString(formData, 'expectedOutput');
  const systemPromptId = optionalId(formData, 'systemPromptId');
  const systemPromptMode = requiredMode(formData);
  const customSystemText = optionalString(formData, 'customSystemText');

  await db
    .update(prompts)
    .set({
      title,
      content,
      expectedOutput,
      systemPromptId,
      systemPromptMode,
      customSystemText,
      ...toolFields(formData),
      updatedAt: new Date(),
    })
    .where(eq(prompts.id, id));

  await replaceToolsetLinks(id, selectedToolsetIds(formData));

  revalidatePath('/prompts');
}

export async function deletePrompt(id: number) {
  await db.delete(prompts).where(eq(prompts.id, id));
  revalidatePath('/prompts');
}
