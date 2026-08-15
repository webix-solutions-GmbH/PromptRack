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
