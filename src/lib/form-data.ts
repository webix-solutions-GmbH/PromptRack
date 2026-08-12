/**
 * FormData readers shared by the server actions.
 *
 * Deliberately separate from the same-named helpers in `src/lib/mcp/args.ts`,
 * which read a JSON-RPC `ToolArgs` object: same names, different input type and
 * different error wording.
 */

/** Trimmed value, or null when absent/blank. */
export function optionalString(formData: FormData, key: string): string | null {
  const value = formData.get(key);
  if (typeof value !== 'string') return null;
  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : null;
}

/** Trimmed value; throws `${key} is required.` when absent/blank. */
export function requiredString(formData: FormData, key: string): string {
  const value = formData.get(key);
  const trimmed = typeof value === 'string' ? value.trim() : '';
  if (!trimmed) {
    throw new Error(`${key} is required.`);
  }
  return trimmed;
}

/** Integer id, or null when absent or not an integer. */
export function optionalId(formData: FormData, key: string): number | null {
  const raw = optionalString(formData, key);
  if (raw === null) return null;
  const id = Number(raw);
  return Number.isInteger(id) ? id : null;
}

/** Finite number, or null when absent; throws `${label} must be a number.` otherwise. */
export function optionalNumber(formData: FormData, key: string, label: string): number | null {
  const raw = optionalString(formData, key);
  if (raw === null) return null;
  const value = Number(raw);
  if (!Number.isFinite(value)) {
    throw new Error(`${label} must be a number.`);
  }
  return value;
}
