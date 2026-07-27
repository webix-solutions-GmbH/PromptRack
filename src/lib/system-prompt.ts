export type SystemPromptMode = 'append' | 'override';

export interface ResolveEffectiveSystemPromptInput {
  mode: SystemPromptMode;
  baseContent: string | null;
  customText: string | null;
}

function normalize(value: string | null | undefined): string | null {
  if (value == null) return null;
  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : null;
}

/**
 * Computes the effective system prompt for a test-case prompt.
 *
 * - mode 'override': effective = customText only (base is ignored).
 * - mode 'append': effective = base + "\n\n" + custom when both are present,
 *   otherwise whichever single part is present.
 * - Whitespace-only strings are treated as absent.
 * - If nothing remains, returns null (no system message at all).
 */
export function resolveEffectiveSystemPrompt(
  input: ResolveEffectiveSystemPromptInput,
): string | null {
  const base = normalize(input.baseContent);
  const custom = normalize(input.customText);

  if (input.mode === 'override') {
    return custom;
  }

  if (base && custom) {
    return `${base}\n\n${custom}`;
  }
  return base ?? custom ?? null;
}
