'use server';

import { revalidatePath } from 'next/cache';
import { redirect } from 'next/navigation';
import { currentScope } from '@/db/scope';
import {
  createGroup as createGroupRow,
  createPrompt as createPromptRow,
  deleteGroup as deleteGroupRow,
  deletePrompt as deletePromptRow,
  replaceToolsetLinks,
  updateGroup as updateGroupRow,
  updatePrompt as updatePromptRow,
} from '@/db/repo/prompts';
import { normalizeMaxTurns, type ToolChoice, type ToolMode } from '@/lib/tools';
import { optionalId, optionalString, requiredString } from '@/lib/form-data';
import { requireWriter } from '@/lib/auth/guards';

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

// ---------------------------------------------------------------------------
// Prompt groups
// ---------------------------------------------------------------------------

export async function createGroup(formData: FormData) {
  await requireWriter();
  const name = requiredString(formData, 'name');
  const description = optionalString(formData, 'description');

  const row = await createGroupRow(await currentScope(), {
    name,
    description,
    now: new Date(),
  });

  revalidatePath('/prompts');
  redirect(`/prompts?group=${row.id}`);
}

export async function updateGroup(id: number, formData: FormData) {
  await requireWriter();
  const name = requiredString(formData, 'name');
  const description = optionalString(formData, 'description');

  await updateGroupRow(await currentScope(), id, { name, description });

  revalidatePath('/prompts');
}

export async function deleteGroup(id: number) {
  await requireWriter();
  await deleteGroupRow(await currentScope(), id);
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
  await requireWriter();
  const groupId = requiredGroupId(formData);
  const title = requiredString(formData, 'title');
  const content = requiredString(formData, 'content');
  const expectedOutput = optionalString(formData, 'expectedOutput');
  const systemPromptId = optionalId(formData, 'systemPromptId');
  const systemPromptMode = requiredMode(formData);
  const customSystemText = optionalString(formData, 'customSystemText');
  const scope = await currentScope();
  const now = new Date();

  const row = await createPromptRow(scope, {
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
  });

  await replaceToolsetLinks(scope, row.id, selectedToolsetIds(formData));

  revalidatePath('/prompts');
}

export async function updatePrompt(id: number, formData: FormData) {
  await requireWriter();
  const title = requiredString(formData, 'title');
  const content = requiredString(formData, 'content');
  const expectedOutput = optionalString(formData, 'expectedOutput');
  const systemPromptId = optionalId(formData, 'systemPromptId');
  const systemPromptMode = requiredMode(formData);
  const customSystemText = optionalString(formData, 'customSystemText');
  const scope = await currentScope();

  await updatePromptRow(scope, id, {
    title,
    content,
    expectedOutput,
    systemPromptId,
    systemPromptMode,
    customSystemText,
    ...toolFields(formData),
    updatedAt: new Date(),
  });

  await replaceToolsetLinks(scope, id, selectedToolsetIds(formData));

  revalidatePath('/prompts');
}

export async function deletePrompt(id: number) {
  await requireWriter();
  await deletePromptRow(await currentScope(), id);
  revalidatePath('/prompts');
}
