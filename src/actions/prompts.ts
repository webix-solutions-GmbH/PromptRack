'use server';

import { revalidatePath } from 'next/cache';
import { redirect } from 'next/navigation';
import { eq } from 'drizzle-orm';
import { db } from '@/db';
import { promptGroups, prompts } from '@/db/schema';

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
      createdAt: Date.now(),
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
  const now = Date.now();

  await db.insert(prompts).values({
    groupId,
    title,
    content,
    expectedOutput,
    systemPromptId,
    systemPromptMode,
    customSystemText,
    sortOrder: 0,
    createdAt: now,
    updatedAt: now,
  });

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
      updatedAt: Date.now(),
    })
    .where(eq(prompts.id, id));

  revalidatePath('/prompts');
}

export async function deletePrompt(id: number) {
  await db.delete(prompts).where(eq(prompts.id, id));
  revalidatePath('/prompts');
}
