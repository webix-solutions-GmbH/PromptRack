'use server';

import { revalidatePath } from 'next/cache';
import { redirect } from 'next/navigation';
import { and, eq } from 'drizzle-orm';
import { db } from '@/db';
import { machineModels, machines } from '@/db/schema';

function normalizeBaseUrl(raw: string): string {
  return raw.trim().replace(/\/+$/, '');
}

function optionalString(formData: FormData, key: string): string | null {
  const value = formData.get(key);
  if (typeof value !== 'string') return null;
  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : null;
}

function requiredMachineFields(formData: FormData) {
  const name = optionalString(formData, 'name');
  const rawBaseUrl = optionalString(formData, 'baseUrl');

  if (!name) {
    throw new Error('Name is required.');
  }
  if (!rawBaseUrl) {
    throw new Error('Base URL is required.');
  }

  const baseUrl = normalizeBaseUrl(rawBaseUrl);
  if (!/^https?:\/\//i.test(baseUrl)) {
    throw new Error('Base URL must start with http:// or https://');
  }

  return {
    name,
    baseUrl,
    apiKey: optionalString(formData, 'apiKey'),
    cpu: optionalString(formData, 'cpu'),
    ram: optionalString(formData, 'ram'),
    gpu: optionalString(formData, 'gpu'),
    notes: optionalString(formData, 'notes'),
  };
}

export async function createMachine(formData: FormData) {
  const fields = requiredMachineFields(formData);
  const now = new Date();

  const [row] = await db
    .insert(machines)
    .values({
      ...fields,
      createdAt: now,
      updatedAt: now,
    })
    .returning({ id: machines.id });

  revalidatePath('/machines');
  redirect(`/machines/${row.id}`);
}

export async function updateMachine(id: number, formData: FormData) {
  const fields = requiredMachineFields(formData);

  await db
    .update(machines)
    .set({
      ...fields,
      updatedAt: new Date(),
    })
    .where(eq(machines.id, id));

  revalidatePath('/machines');
  revalidatePath(`/machines/${id}`);
}

export async function deleteMachine(id: number) {
  await db.delete(machines).where(eq(machines.id, id));
  revalidatePath('/machines');
}

export async function addManualModel(machineId: number, formData: FormData) {
  const modelId = optionalString(formData, 'modelId');
  if (!modelId) {
    throw new Error('Model id is required.');
  }

  const now = new Date();

  const [existing] = await db
    .select({ id: machineModels.id })
    .from(machineModels)
    .where(and(eq(machineModels.machineId, machineId), eq(machineModels.modelId, modelId)));

  if (existing) {
    // Row already exists (previously discovered or added manually) — just
    // bump last_seen_at, leave currently_loaded and source untouched.
    await db
      .update(machineModels)
      .set({ lastSeenAt: now })
      .where(eq(machineModels.id, existing.id));
  } else {
    await db.insert(machineModels).values({
      machineId,
      modelId,
      source: 'manual',
      currentlyLoaded: false,
      firstSeenAt: now,
      lastSeenAt: now,
    });
  }

  revalidatePath(`/machines/${machineId}`);
}
