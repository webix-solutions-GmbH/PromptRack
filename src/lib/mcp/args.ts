/**
 * Argument coercion for MCP tool calls.
 *
 * A tool call arrives as untrusted JSON written by a model, so every field is
 * read through one of these helpers: they trim, they reject the wrong type with
 * a message the model can act on, and they distinguish "absent" from "empty"
 * (which is what makes patch semantics possible in `update_*` tools).
 */

/** A tool failure the model should see as the tool's answer, not a protocol error. */
export class McpToolError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'McpToolError';
  }
}

export type ToolArgs = Record<string, unknown>;

/** Reads the `arguments` object of a `tools/call`, tolerating its absence. */
export function toolArgs(raw: unknown): ToolArgs {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return {};
  return raw as ToolArgs;
}

/** True when the caller mentioned the key at all — `null` counts as mentioned. */
export function hasKey(args: ToolArgs, key: string): boolean {
  return Object.prototype.hasOwnProperty.call(args, key);
}

export function optionalString(args: ToolArgs, key: string): string | null {
  const value = args[key];
  if (value === undefined || value === null) return null;
  if (typeof value !== 'string') {
    throw new McpToolError(`"${key}" must be a string.`);
  }
  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : null;
}

export function requireString(args: ToolArgs, key: string): string {
  const value = optionalString(args, key);
  if (value === null) {
    throw new McpToolError(`"${key}" is required.`);
  }
  return value;
}

/**
 * Multi-line content, where leading/trailing blank lines are the author's
 * business but an all-whitespace value is still empty.
 */
export function requireText(args: ToolArgs, key: string): string {
  const value = args[key];
  if (typeof value !== 'string' || value.trim().length === 0) {
    throw new McpToolError(`"${key}" is required.`);
  }
  return value;
}

export function optionalText(args: ToolArgs, key: string): string | null {
  const value = args[key];
  if (value === undefined || value === null) return null;
  if (typeof value !== 'string') {
    throw new McpToolError(`"${key}" must be a string.`);
  }
  return value.trim().length > 0 ? value : null;
}

export function optionalInteger(args: ToolArgs, key: string): number | null {
  const value = args[key];
  if (value === undefined || value === null) return null;

  // Models often send numbers as strings; accept that rather than fail a whole
  // call over quoting.
  const parsed = typeof value === 'string' ? Number(value.trim()) : value;
  if (typeof parsed !== 'number' || !Number.isFinite(parsed) || !Number.isInteger(parsed)) {
    throw new McpToolError(`"${key}" must be a whole number.`);
  }
  return parsed;
}

export function requireInteger(args: ToolArgs, key: string): number {
  const value = optionalInteger(args, key);
  if (value === null) {
    throw new McpToolError(`"${key}" is required.`);
  }
  return value;
}

export function optionalNumber(args: ToolArgs, key: string): number | null {
  const value = args[key];
  if (value === undefined || value === null) return null;

  const parsed = typeof value === 'string' ? Number(value.trim()) : value;
  if (typeof parsed !== 'number' || !Number.isFinite(parsed)) {
    throw new McpToolError(`"${key}" must be a number.`);
  }
  return parsed;
}

export function optionalBoolean(args: ToolArgs, key: string, fallback: boolean): boolean {
  const value = args[key];
  if (value === undefined || value === null) return fallback;
  if (typeof value === 'boolean') return value;
  if (value === 'true') return true;
  if (value === 'false') return false;
  throw new McpToolError(`"${key}" must be true or false.`);
}

export function optionalEnum<T extends string>(
  args: ToolArgs,
  key: string,
  allowed: readonly T[],
): T | null {
  const value = optionalString(args, key);
  if (value === null) return null;
  if (!(allowed as readonly string[]).includes(value)) {
    throw new McpToolError(`"${key}" must be one of: ${allowed.join(', ')}.`);
  }
  return value as T;
}

/**
 * A reference to a row by numeric id *or* by name.
 *
 * Every relation an MCP client has to name (group, system prompt, toolset,
 * machine) accepts both, because a client that just created a group knows its
 * name and would otherwise have to look the id up again.
 */
export type RowRef = { kind: 'id'; id: number } | { kind: 'name'; name: string };

export function parseRowRef(value: unknown, label: string): RowRef {
  if (typeof value === 'number') {
    if (!Number.isInteger(value)) {
      throw new McpToolError(`${label} must be a name or a whole-number id.`);
    }
    return { kind: 'id', id: value };
  }

  if (typeof value === 'string') {
    const trimmed = value.trim();
    if (trimmed.length === 0) {
      throw new McpToolError(`${label} must not be empty.`);
    }
    // A numeric string is an id: "12" is never a sensible name and treating it
    // as one would silently create a second group called "12".
    if (/^\d+$/.test(trimmed)) {
      return { kind: 'id', id: Number(trimmed) };
    }
    return { kind: 'name', name: trimmed };
  }

  throw new McpToolError(`${label} must be a name (string) or an id (number).`);
}

export function requireRowRef(args: ToolArgs, key: string): RowRef {
  if (!hasKey(args, key) || args[key] === null) {
    throw new McpToolError(`"${key}" is required.`);
  }
  return parseRowRef(args[key], `"${key}"`);
}

export function optionalRowRef(args: ToolArgs, key: string): RowRef | null {
  if (!hasKey(args, key) || args[key] === null || args[key] === undefined) return null;
  return parseRowRef(args[key], `"${key}"`);
}

/** A list of refs; a single value is accepted as a one-element list. */
export function optionalRowRefList(args: ToolArgs, key: string): RowRef[] | null {
  if (!hasKey(args, key) || args[key] === null || args[key] === undefined) return null;

  const raw = args[key];
  const list = Array.isArray(raw) ? raw : [raw];
  return list.map((entry) => parseRowRef(entry, `each entry of "${key}"`));
}

/** Finds a ref in a set of rows, reporting what was available when it misses. */
export function resolveRowRef<T extends { id: number; name: string }>(
  ref: RowRef,
  rows: readonly T[],
  label: string,
): T {
  if (ref.kind === 'id') {
    const row = rows.find((candidate) => candidate.id === ref.id);
    if (!row) {
      throw new McpToolError(`No ${label} with id ${ref.id}.`);
    }
    return row;
  }

  const wanted = ref.name.toLowerCase();
  const matches = rows.filter((candidate) => candidate.name.trim().toLowerCase() === wanted);
  if (matches.length === 0) {
    const known = rows.map((row) => `${row.name} (${row.id})`).join(', ');
    throw new McpToolError(
      `No ${label} named "${ref.name}".${known ? ` Known: ${known}.` : ''}`,
    );
  }
  if (matches.length > 1) {
    throw new McpToolError(
      `Several ${label} entries are named "${ref.name}" (ids ${matches
        .map((row) => row.id)
        .join(', ')}). Use the id.`,
    );
  }
  return matches[0];
}

/** Shortens long text for list-style responses, marking that it was cut. */
export function truncate(
  value: string | null,
  max: number,
): { text: string | null; truncated: boolean } {
  if (value === null) return { text: null, truncated: false };
  if (max <= 0 || value.length <= max) return { text: value, truncated: false };
  return { text: `${value.slice(0, max)}…`, truncated: true };
}
