// Turning a `run_results` version *id* into the `v4` badge the matrix shows.
// A composable rather than a pure helper because the matrix ships ids only:
// resolving every id in a whole matrix up front would be one request per
// distinct version for rows nobody opened, so ids are fetched as the rows
// that need them open.
//
// Shared by `MatrixTable`'s row headers and `CellDetail`: a row shows one copy
// of a prompt when its cells froze the same text and falls back to a per-cell
// copy when they didn't, so both places need the same lookup — and, more to
// the point, the same cache.
import { ref, watch } from 'vue'
import { promptsApi } from '../api/prompts'

// Module-level, and keyed on the *promise* rather than the resolved number:
// opening a row can ask for the same id from its header and from several
// cells at once, so caching after the fact would still let five near-identical
// requests go out together. A failure is cached too — re-opening a row must
// not retry an id the server already refused, and the label degrades to
// `v#<id>` rather than to a blank badge.
const versionNumbers = new Map<number, Promise<number | null>>()

function resolveVersion(id: number): Promise<number | null> {
  const cached = versionNumbers.get(id)
  if (cached !== undefined) return cached
  const pending = promptsApi
    .getVersion(id)
    .then((version) => version.version)
    .catch(() => null)
  versionNumbers.set(id, pending)
  return pending
}

/**
 * `versionLabel(text, versionId)` → `"v4"` / `"uncommitted"` / `null` (empty
 * slot) — the same three states run detail shows, told apart by the frozen
 * text since both of the latter two carry a null version id.
 *
 * `ids` is re-read whatever it depends on changes, so a caller passes the ids
 * of whatever it is currently displaying and nothing more.
 */
export function usePromptVersionLabels(ids: () => (number | null)[]) {
  // The module cache above is not reactive, so the numbers it hands back are
  // copied into this instance's own map to re-render the badges.
  const resolved = ref<Map<number, number>>(new Map())

  watch(
    ids,
    async (wanted) => {
      const missing = wanted.filter(
        (id): id is number => id !== null && !resolved.value.has(id),
      )
      if (missing.length === 0) return
      const entries = await Promise.all(
        missing.map(async (id) => [id, await resolveVersion(id)] as const),
      )
      const next = new Map(resolved.value)
      for (const [id, version] of entries) if (version !== null) next.set(id, version)
      resolved.value = next
    },
    { immediate: true },
  )

  function versionLabel(text: string | null, versionId: number | null): string | null {
    if (text === null || text.trim().length === 0) return null
    if (versionId === null) return 'uncommitted'
    const version = resolved.value.get(versionId)
    return version === undefined ? `v#${versionId}` : `v${version}`
  }

  return { versionLabel }
}
