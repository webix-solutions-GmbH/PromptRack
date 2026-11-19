// Pure client-side helpers for tool/function calling in the test case
// editor, trimmed to what the editor's preview needs (the request-building
// half stays server-side, in the run executor). Kept free
// of any API import so it stays trivially unit-testable and can preview
// exactly what the backend's `assert_tool_config`
// (`backend/app/services/tool_config.py`) would refuse, the moment
// a toolset is picked — before either a save or a run round-trips to the
// server.

export const DEFAULT_MAX_TURNS = 6
/** Guard rail for the agentic loop, independent of what a test case asks for. */
export const MAX_TURNS_LIMIT = 20

/** The subset of a `tools` row the collision/offer preview needs. */
export interface ToolLike {
  name: string
  description?: string | null
  enabled?: boolean
}

/**
 * Two toolsets may both define a tool called `search`. The model would only
 * ever see one of them, so the test case editor and (server-side)
 * `assert_tool_config` both refuse the combination instead of silently
 * dropping a tool.
 */
export function collectToolNameCollisions(rows: ToolLike[]): string[] {
  const seen = new Set<string>()
  const collisions = new Set<string>()

  for (const row of rows) {
    if (row.enabled === false) continue
    if (seen.has(row.name)) collisions.add(row.name)
    seen.add(row.name)
  }
  return [...collisions].sort()
}

/**
 * A `MockResponsesEditor` list as a request carries it, or a readable refusal.
 * A sole blank field is the single-response layout left empty and means
 * "none" (`[]`); a blank entry inside a longer list is a call the author meant
 * to fill in, which the server would refuse anyway — said here naming the call
 * instead. One rule for both places that edit canned responses: the toolset's
 * tool dialog and a test case's per-tool override.
 */
export function mockResponsesForWire(
  entries: string[],
): { ok: true; value: string[] } | { ok: false; error: string } {
  if (entries.length <= 1) {
    const only = entries[0] ?? ''
    return { ok: true, value: only.trim().length > 0 ? [only] : [] }
  }
  const blank = entries.findIndex((entry) => entry.trim().length === 0)
  if (blank !== -1) {
    return {
      ok: false,
      error: `The response for call ${blank + 1} is empty — fill it in or remove it.`,
    }
  }
  return { ok: true, value: entries }
}
