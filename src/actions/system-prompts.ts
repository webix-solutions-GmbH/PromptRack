'use server';

import { revalidatePath } from 'next/cache';
import { eq } from 'drizzle-orm';
import { db } from '@/db';
import { systemPrompts } from '@/db/schema';

function requiredString(formData: FormData, key: string): string {
  const value = formData.get(key);
  const trimmed = typeof value === 'string' ? value.trim() : '';
  if (!trimmed) {
    throw new Error(`${key} is required.`);
  }
  return trimmed;
}

export async function createSystemPrompt(formData: FormData) {
  const name = requiredString(formData, 'name');
  const content = requiredString(formData, 'content');
  const now = new Date();

  await db.insert(systemPrompts).values({
    name,
    content,
    createdAt: now,
    updatedAt: now,
  });

  revalidatePath('/system-prompts');
}

export async function updateSystemPrompt(id: number, formData: FormData) {
  const name = requiredString(formData, 'name');
  const content = requiredString(formData, 'content');

  await db
    .update(systemPrompts)
    .set({ name, content, updatedAt: new Date() })
    .where(eq(systemPrompts.id, id));

  revalidatePath('/system-prompts');
  revalidatePath('/prompts');
}

export async function deleteSystemPrompt(id: number) {
  await db.delete(systemPrompts).where(eq(systemPrompts.id, id));
  revalidatePath('/system-prompts');
  revalidatePath('/prompts');
}
