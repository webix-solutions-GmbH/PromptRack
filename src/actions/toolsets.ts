'use server';

import { revalidatePath } from 'next/cache';
import { eq } from 'drizzle-orm';
import { db } from '@/db';
import { tools, toolsets } from '@/db/schema';
import { validateParameterSchema, validateToolName } from '@/lib/tools';

function optionalString(formData: FormData, key: string): string | null {
  const value = formData.get(key);
  if (typeof value !== 'string') return null;
  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : null;
}

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
  const fields = toolsetFields(formData);
  const now = new Date();

  await db.insert(toolsets).values({ ...fields, createdAt: now, updatedAt: now });
  revalidatePath('/toolsets');
  revalidatePath('/prompts');
}

export async function updateToolset(id: number, formData: FormData) {
  const fields = toolsetFields(formData);

  await db
    .update(toolsets)
    .set({ ...fields, updatedAt: new Date() })
    .where(eq(toolsets.id, id));

  revalidatePath('/toolsets');
  revalidatePath('/prompts');
}

/**
 * Deleting a toolset cascades to its tools and to any prompt links, but never
 * touches `run_results` — a past run renders from its own frozen snapshot.
 */
export async function deleteToolset(id: number) {
  await db.delete(toolsets).where(eq(toolsets.id, id));
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

/** Turns a unique-constraint violation into something a user can act on. */
function describeToolWriteError(err: unknown, name: string): Error {
  const message = err instanceof Error ? err.message : '';
  if (/UNIQUE constraint failed/i.test(message)) {
    return new Error(`This toolset already has a tool called "${name}".`);
  }
  return err instanceof Error ? err : new Error('Failed to save tool.');
}

export async function createTool(toolsetId: number, formData: FormData) {
  const fields = toolFields(formData);
  const now = new Date();

  try {
    await db.insert(tools).values({
      ...fields,
      toolsetId,
      source: 'manual',
      enabled: true,
      firstSeenAt: now,
      lastSeenAt: now,
    });
  } catch (err) {
    throw describeToolWriteError(err, fields.name);
  }

  revalidatePath('/toolsets');
}

export async function updateTool(id: number, formData: FormData) {
  const fields = toolFields(formData);

  try {
    await db
      .update(tools)
      .set({ ...fields, lastSeenAt: new Date() })
      .where(eq(tools.id, id));
  } catch (err) {
    throw describeToolWriteError(err, fields.name);
  }

  revalidatePath('/toolsets');
}

export async function deleteTool(id: number) {
  await db.delete(tools).where(eq(tools.id, id));
  revalidatePath('/toolsets');
}

/**
 * A disabled tool is kept but never offered to a model. This is also how a
 * discovered tool that has since vanished from its MCP server is retired —
 * `machine_models` treats models the same way.
 */
export async function setToolEnabled(id: number, enabled: boolean) {
  await db.update(tools).set({ enabled }).where(eq(tools.id, id));
  revalidatePath('/toolsets');
}
