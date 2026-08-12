'use server';

import { revalidatePath } from 'next/cache';
import { currentScope } from '@/db/scope';
import {
  createTool as createToolRow,
  createToolset as createToolsetRow,
  deleteTool as deleteToolRow,
  deleteToolset as deleteToolsetRow,
  setToolEnabled as setToolEnabledRow,
  updateTool as updateToolRow,
  updateToolset as updateToolsetRow,
} from '@/db/repo/toolsets';
import { validateParameterSchema, validateToolName } from '@/lib/tools';
import { optionalString } from '@/lib/form-data';
import { requireAdmin, requireWriter } from '@/lib/auth/guards';

/**
 * A toolset's editable fields. `mcp` toolsets need a reachable endpoint; a
 * `manual` one must not keep a stale URL around after being switched over.
 */
function toolsetFields(formData: FormData) {
  const name = optionalString(formData, 'name');
  if (!name) {
    throw new Error('Name is required.');
  }

  const kind = formData.get('kind') === 'mcp' ? 'mcp' : 'manual';
  const description = optionalString(formData, 'description');

  if (kind === 'manual') {
    return { name, description, kind, mcpUrl: null, mcpHeaders: null } as const;
  }

  const mcpUrl = optionalString(formData, 'mcpUrl');
  if (!mcpUrl) {
    throw new Error('An MCP toolset needs a server URL.');
  }
  if (!/^https?:\/\//i.test(mcpUrl)) {
    throw new Error('MCP server URL must start with http:// or https://');
  }

  const rawHeaders = optionalString(formData, 'mcpHeaders');
  if (rawHeaders !== null) {
    let parsed: unknown;
    try {
      parsed = JSON.parse(rawHeaders);
    } catch {
      throw new Error('Headers must be valid JSON.');
    }
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      throw new Error('Headers must be a JSON object, e.g. {"Authorization": "Bearer …"}.');
    }
    for (const value of Object.values(parsed as Record<string, unknown>)) {
      if (typeof value !== 'string') {
        throw new Error('Every header value must be a string.');
      }
    }
  }

  return { name, description, kind, mcpUrl, mcpHeaders: rawHeaders } as const;
}

export async function createToolset(formData: FormData) {
  await requireAdmin();
  const fields = toolsetFields(formData);

  await createToolsetRow(await currentScope(), { ...fields, now: new Date() });
  revalidatePath('/toolsets');
  revalidatePath('/prompts');
}

export async function updateToolset(id: number, formData: FormData) {
  await requireAdmin();
  const fields = toolsetFields(formData);

  await updateToolsetRow(await currentScope(), id, { ...fields, now: new Date() });

  revalidatePath('/toolsets');
  revalidatePath('/prompts');
}

/**
 * Deleting a toolset cascades to its tools and to any prompt links, but never
 * touches `run_results` — a past run renders from its own frozen snapshot.
 */
export async function deleteToolset(id: number) {
  await requireAdmin();
  await deleteToolsetRow(await currentScope(), id);
  revalidatePath('/toolsets');
  revalidatePath('/prompts');
}

function toolFields(formData: FormData) {
  const name = optionalString(formData, 'name') ?? '';
  const nameError = validateToolName(name);
  if (nameError) {
    throw new Error(nameError);
  }

  const rawParameters = formData.get('parametersJson');
  const parameters = typeof rawParameters === 'string' ? rawParameters.trim() : '';
  const schemaError = validateParameterSchema(parameters);
  if (schemaError) {
    throw new Error(schemaError);
  }

  return {
    name,
    description: optionalString(formData, 'description'),
    parametersJson: parameters.length > 0 ? parameters : '{"type":"object","properties":{}}',
    mockResponse: optionalString(formData, 'mockResponse'),
  };
}

/**
 * Turns a unique-constraint violation into something a user can act on.
 *
 * Postgres reports it as `duplicate key value violates unique constraint`; the
 * SQLite wording is kept so a database restored from the pre-Postgres era, or a
 * future driver swap, still produces the readable message rather than a raw
 * driver dump.
 */
function describeToolWriteError(err: unknown, name: string): Error {
  const message = err instanceof Error ? err.message : '';
  if (/duplicate key value violates unique constraint|UNIQUE constraint failed/i.test(message)) {
    return new Error(`This toolset already has a tool called "${name}".`);
  }
  return err instanceof Error ? err : new Error('Failed to save tool.');
}

export async function createTool(toolsetId: number, formData: FormData) {
  await requireWriter();
  const scope = await currentScope();
  const fields = toolFields(formData);

  try {
    await createToolRow(scope, toolsetId, { ...fields, now: new Date() });
  } catch (err) {
    throw describeToolWriteError(err, fields.name);
  }

  revalidatePath('/toolsets');
}

export async function updateTool(id: number, formData: FormData) {
  await requireWriter();
  const scope = await currentScope();
  const fields = toolFields(formData);

  try {
    await updateToolRow(scope, id, { ...fields, now: new Date() });
  } catch (err) {
    throw describeToolWriteError(err, fields.name);
  }

  revalidatePath('/toolsets');
}

export async function deleteTool(id: number) {
  await requireWriter();
  await deleteToolRow(await currentScope(), id);
  revalidatePath('/toolsets');
}

/**
 * A disabled tool is kept but never offered to a model. This is also how a
 * discovered tool that has since vanished from its MCP server is retired —
 * `machine_models` treats models the same way.
 */
export async function setToolEnabled(id: number, enabled: boolean) {
  await requireWriter();
  await setToolEnabledRow(await currentScope(), id, enabled);
  revalidatePath('/toolsets');
}
