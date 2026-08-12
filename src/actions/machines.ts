'use server';

import { revalidatePath } from 'next/cache';
import { redirect } from 'next/navigation';
import { currentScope } from '@/db/scope';
import {
  createMachine as createMachineRow,
  deleteMachine as deleteMachineRow,
  touchMachineModel,
  updateMachine as updateMachineRow,
} from '@/db/repo/machines';
import { optionalString } from '@/lib/form-data';

function normalizeBaseUrl(raw: string): string {
  return raw.trim().replace(/\/+$/, '');
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
  const scope = await currentScope();
  const fields = requiredMachineFields(formData);
  const now = new Date();

  const row = await createMachineRow(scope, { ...fields, createdAt: now, updatedAt: now });

  revalidatePath('/machines');
  redirect(`/machines/${row.id}`);
}

export async function updateMachine(id: number, formData: FormData) {
  const scope = await currentScope();
  const fields = requiredMachineFields(formData);

  await updateMachineRow(scope, id, { ...fields, updatedAt: new Date() });

  revalidatePath('/machines');
  revalidatePath(`/machines/${id}`);
}

export async function deleteMachine(id: number) {
  const scope = await currentScope();
  await deleteMachineRow(scope, id);
  revalidatePath('/machines');
}

export async function addManualModel(machineId: number, formData: FormData) {
  const scope = await currentScope();
  const modelId = optionalString(formData, 'modelId');
  if (!modelId) {
    throw new Error('Model id is required.');
  }

  await touchMachineModel(scope, {
    machineId,
    modelId,
    source: 'manual',
    at: new Date(),
  });

  revalidatePath(`/machines/${machineId}`);
}
