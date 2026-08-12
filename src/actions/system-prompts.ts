'use server';

import { revalidatePath } from 'next/cache';
import { currentScope } from '@/db/scope';
import {
  createSystemPrompt as createSystemPromptRow,
  deleteSystemPrompt as deleteSystemPromptRow,
  updateSystemPrompt as updateSystemPromptRow,
} from '@/db/repo/system-prompts';
import { requiredString } from '@/lib/form-data';
import { requireWriter } from '@/lib/auth/guards';

export async function createSystemPrompt(formData: FormData) {
  await requireWriter();
  const scope = await currentScope();
  const name = requiredString(formData, 'name');
  const content = requiredString(formData, 'content');

  await createSystemPromptRow(scope, { name, content, now: new Date() });

  revalidatePath('/system-prompts');
}

export async function updateSystemPrompt(id: number, formData: FormData) {
  await requireWriter();
  const scope = await currentScope();
  const name = requiredString(formData, 'name');
  const content = requiredString(formData, 'content');

  await updateSystemPromptRow(scope, id, { name, content, now: new Date() });

  revalidatePath('/system-prompts');
  revalidatePath('/prompts');
}

export async function deleteSystemPrompt(id: number) {
  await requireWriter();
  await deleteSystemPromptRow(await currentScope(), id);
  revalidatePath('/system-prompts');
  revalidatePath('/prompts');
}
