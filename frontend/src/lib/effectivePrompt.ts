// Pure helper mirroring the backend's `resolve_effective_prompt`
// (`backend/app/services/effective_prompt.py`, Task 3.4 — itself a port of
// `git show master:src/lib/system-prompt.ts`'s `resolveEffectiveSystemPrompt`,
// renamed for the pivot's "prompt" terminology). Duplicated here rather than
// called over the network so the test case editor's preview updates on every
// keystroke with no round trip — the same reasoning the old React editor had
// for importing the function directly instead of calling an API
// (`git show master:src/components/prompts/prompt-editor.tsx`). Keep this
// logic identical to the backend's if either changes.
import type { PromptMode } from '../api/testCases'

export interface ResolveEffectivePromptInput {
  mode: PromptMode
  baseContent: string | null
  customText: string | null
}

function normalize(value: string | null | undefined): string | null {
  if (value == null) return null
  const trimmed = value.trim()
  return trimmed.length > 0 ? trimmed : null
}

/**
 * - mode 'override': effective = customText only (base is ignored).
 * - mode 'append': effective = base + "\n\n" + custom when both are present,
 *   otherwise whichever single part is present.
 * - Whitespace-only strings are treated as absent.
 * - If nothing remains, returns null (no system message at all).
 */
export function resolveEffectivePrompt(input: ResolveEffectivePromptInput): string | null {
  const base = normalize(input.baseContent)
  const custom = normalize(input.customText)

  if (input.mode === 'override') {
    return custom
  }

  if (base && custom) {
    return `${base}\n\n${custom}`
  }
  return base ?? custom ?? null
}
