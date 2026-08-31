// Client-side twin of the merge rules in `backend/app/services/params.py`,
// used by the new-run page to preview what a run will send: the endpoint's
// `default_params`, the selected parameter groups folded into one layer, and
// the run's own overrides on top. Has to stay in step with the backend — a
// preview disagreeing with what run creation then freezes is worse than no
// preview at all.

/** Key-sorted JSON, so two values compare the way the backend compares them
 * (`json.dumps(..., sort_keys=True)`): `1` and `1.0` differ, a reordered
 * nested object does not. */
function stableJson(value: unknown): string {
  if (Array.isArray(value)) {
    return `[${value.map(stableJson).join(',')}]`
  }
  if (typeof value === 'object' && value !== null) {
    const entries = Object.entries(value as Record<string, unknown>).sort(([a], [b]) =>
      a < b ? -1 : a > b ? 1 : 0,
    )
    return `{${entries.map(([key, item]) => `${JSON.stringify(key)}:${stableJson(item)}`).join(',')}}`
  }
  return JSON.stringify(value) ?? 'null'
}

/** Shallow per-key merge, overrides winning, `null`-valued keys dropped
 * *after* merging — which is what makes a `null` an unset rather than a null
 * on the wire. Twin of `merge_params`. */
export function mergeParams(
  defaults: Record<string, unknown> | null | undefined,
  overrides: Record<string, unknown> | null | undefined,
): Record<string, unknown> | null {
  const merged: Record<string, unknown> = { ...(defaults ?? {}), ...(overrides ?? {}) }
  const kept = Object.fromEntries(
    Object.entries(merged).filter(([, value]) => value !== null && value !== undefined),
  )
  return Object.keys(kept).length > 0 ? kept : null
}

export interface GroupParamsSource {
  name: string
  params: Record<string, unknown>
}

export interface GroupParamsCollision {
  key: string
  /** The two group names fighting over `key`. */
  groups: [string, string]
}

export interface CombinedGroupParams {
  /** The folded layer, `null` values preserved (they unset an endpoint
   * default; `mergeParams` drops them after merging). `null` when the
   * selection contributes nothing. */
  combined: Record<string, unknown> | null
  /** Same key, different values across two selected groups — the backend
   * refuses the run, so the page warns before submitting. */
  collisions: GroupParamsCollision[]
}

/** Twin of `combine_group_params`, except it reports collisions instead of
 * throwing, so the page can render the warning inline. */
export function combineGroupParams(groups: GroupParamsSource[]): CombinedGroupParams {
  const combined: Record<string, unknown> = {}
  const origin: Record<string, string> = {}
  const collisions: GroupParamsCollision[] = []
  for (const group of groups) {
    for (const [key, value] of Object.entries(group.params)) {
      if (key in combined && stableJson(value) !== stableJson(combined[key])) {
        collisions.push({ key, groups: [origin[key], group.name] })
        continue
      }
      combined[key] = value
      origin[key] ??= group.name
    }
  }
  return {
    combined: Object.keys(combined).length > 0 ? combined : null,
    collisions,
  }
}
