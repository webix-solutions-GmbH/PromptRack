/**
 * Pure helpers for tool/function calling.
 *
 * Kept free of server-only imports so the prompt editor (client) can preview
 * the exact tool list a run would send, and so the fiddly bits — argument
 * parsing, name collisions — are unit-testable.
 */

export type ToolMode = 'none' | 'definitions' | 'execute';
export type ToolChoice = 'auto' | 'required' | 'none';

export const DEFAULT_MAX_TURNS = 6;
/** Guard rail for the agentic loop, independent of what a prompt asks for. */
export const MAX_TURNS_LIMIT = 20;

/** One entry of the OpenAI-compatible `tools` array. */
export interface ToolDefinition {
  type: 'function';
  function: {
    name: string;
    description?: string;
    parameters: Record<string, unknown>;
  };
}

/** The subset of a `tools` row the definition builder needs. */
export interface ToolLike {
  name: string;
  description?: string | null;
  parametersJson?: string | null;
  enabled?: boolean;
}

/**
 * An empty JSON Schema object is what a no-argument function looks like on the
 * wire. Several servers reject a bare `{}`, so spell the shape out.
 */
export function emptyParameterSchema(): Record<string, unknown> {
  return { type: 'object', properties: {} };
}

/**
 * Parses a stored JSON-Schema string. Anything that is not a JSON object —
 * empty, malformed, an array — degrades to the no-argument schema rather than
 * throwing, so one bad tool row cannot break a whole run.
 */
export function parseParameterSchema(raw: string | null | undefined): Record<string, unknown> {
  if (!raw || raw.trim().length === 0) return emptyParameterSchema();

  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return emptyParameterSchema();
  }

  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    return emptyParameterSchema();
  }
  return parsed as Record<string, unknown>;
}

/** Validation for the schema textarea in the tool editor. */
export function validateParameterSchema(raw: string): string | null {
  if (raw.trim().length === 0) return null;

  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return 'Parameters must be valid JSON.';
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    return 'Parameters must be a JSON object (a JSON Schema).';
  }
  return null;
}

/**
 * OpenAI requires function names to match `^[a-zA-Z0-9_-]{1,64}$`. Enforced at
 * authoring time so a bad name surfaces in the editor, not as an HTTP 400
 * halfway through a run.
 */
const NAME_PATTERN = /^[a-zA-Z0-9_-]{1,64}$/;

export function validateToolName(name: string): string | null {
  if (name.trim().length === 0) return 'Tool name is required.';
  if (!NAME_PATTERN.test(name)) {
    return 'Tool name may only contain letters, digits, underscores and hyphens (max 64).';
  }
  return null;
}

/** Builds the `tools` request array, skipping disabled rows. */
export function buildToolDefinitions(rows: ToolLike[]): ToolDefinition[] {
  return rows
    .filter((row) => row.enabled !== false)
    .map((row) => ({
      type: 'function' as const,
      function: {
        name: row.name,
        ...(row.description && row.description.trim().length > 0
          ? { description: row.description }
          : {}),
        parameters: parseParameterSchema(row.parametersJson),
      },
    }));
}

/**
 * Two toolsets may both define a tool called `search`. The model would only
 * ever see one of them, so the prompt editor and `createRun` refuse the
 * combination instead of silently dropping a tool.
 */
export function collectToolNameCollisions(rows: ToolLike[]): string[] {
  const seen = new Set<string>();
  const collisions = new Set<string>();

  for (const row of rows) {
    if (row.enabled === false) continue;
    if (seen.has(row.name)) collisions.add(row.name);
    seen.add(row.name);
  }
  return [...collisions].sort();
}

export type ParsedArguments =
  | { ok: true; value: Record<string, unknown> }
  | { ok: false; error: string };

/**
 * Parses the `arguments` string of a tool call.
 *
 * Models emit malformed JSON often enough that this must be a recorded finding
 * rather than a crash: the caller feeds the error back to the model as the
 * tool's output, which is exactly what a real agent would see.
 */
export function parseToolArguments(raw: string | null | undefined): ParsedArguments {
  const text = (raw ?? '').trim();
  // An empty argument string is how a no-argument call arrives.
  if (text.length === 0) return { ok: true, value: {} };

  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch (err) {
    return {
      ok: false,
      error: `Arguments are not valid JSON: ${err instanceof Error ? err.message : 'parse failed'}`,
    };
  }

  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    return { ok: false, error: 'Arguments must be a JSON object.' };
  }
  return { ok: true, value: parsed as Record<string, unknown> };
}

/** Pretty-prints an argument string for display, leaving junk untouched. */
export function formatToolArguments(raw: string | null | undefined): string {
  const parsed = parseToolArguments(raw);
  if (!parsed.ok) return (raw ?? '').trim();
  return JSON.stringify(parsed.value, null, 2);
}

/** Clamps a user-supplied turn budget into something the loop can honour. */
export function normalizeMaxTurns(value: number | null | undefined): number {
  if (typeof value !== 'number' || !Number.isFinite(value)) return DEFAULT_MAX_TURNS;
  const rounded = Math.floor(value);
  if (rounded < 1) return 1;
  if (rounded > MAX_TURNS_LIMIT) return MAX_TURNS_LIMIT;
  return rounded;
}

/**
 * One frozen tool in a `run_results.tools_snapshot`.
 *
 * The definition and a manual tool's canned response are *content* and are
 * frozen with the run, exactly like the prompt text and the resolved system
 * prompt. An MCP tool only records where it came from — its endpoint and auth
 * are credentials and are read live at execution time, the same tradeoff
 * already made for a machine's `base_url` and `api_key`.
 */
export interface SnapshotTool {
  definition: ToolDefinition;
  source: 'manual' | 'mcp';
  toolsetId: number;
  toolsetName: string;
  mockResponse: string | null;
}

/** Reads a `tools_snapshot` column back, skipping anything malformed. */
export function parseToolsSnapshot(raw: string | null | undefined): SnapshotTool[] {
  if (!raw) return [];

  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return [];
  }
  if (!Array.isArray(parsed)) return [];

  return parsed.filter((entry): entry is SnapshotTool => {
    if (!entry || typeof entry !== 'object') return false;
    const definition = (entry as { definition?: unknown }).definition;
    if (!definition || typeof definition !== 'object') return false;
    const fn = (definition as { function?: unknown }).function;
    return !!fn && typeof fn === 'object' && typeof (fn as { name?: unknown }).name === 'string';
  });
}

/** The wire `tools` array for a snapshot. */
export function snapshotDefinitions(snapshot: SnapshotTool[]): ToolDefinition[] {
  return snapshot.map((entry) => entry.definition);
}

/** Names in a snapshot, for compact display in lists and the compare matrix. */
export function snapshotToolNames(snapshot: SnapshotTool[]): string[] {
  return snapshot.map((entry) => entry.definition.function.name);
}
