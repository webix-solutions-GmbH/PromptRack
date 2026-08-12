'use server';

import { revalidatePath } from 'next/cache';
import { requireActor } from '@/lib/auth/guards';
import {
  generateToken,
  hashToken,
  insertApiToken,
  newTokenId,
  revokeApiTokenRow,
  tokenDisplayPrefix,
} from '@/lib/auth/tokens';
import { optionalNumber, requiredString } from '@/lib/form-data';

const MAX_EXPIRY_DAYS = 3650;

/**
 * Issues one API token for the signed-in user and returns the raw value.
 *
 * Any role may hold a token — a viewer's token simply cannot call a write tool
 * (see `handleMcpMessage`). The raw token is returned exactly once and never
 * stored: only its SHA-256 and a display prefix go to the database.
 */
export async function createApiToken(formData: FormData): Promise<{ token: string }> {
  const actor = await requireActor();
  const name = requiredString(formData, 'name');

  const days = optionalNumber(formData, 'expiresInDays', 'Expiry');
  if (days !== null && (!Number.isInteger(days) || days < 1 || days > MAX_EXPIRY_DAYS)) {
    throw new Error(`Expiry must be a whole number of days between 1 and ${MAX_EXPIRY_DAYS}.`);
  }

  const raw = generateToken();
  await insertApiToken({
    id: newTokenId(),
    userId: actor.userId,
    name,
    tokenHash: hashToken(raw),
    prefix: tokenDisplayPrefix(raw),
    expiresAt: days === null ? null : new Date(Date.now() + days * 24 * 60 * 60 * 1000),
  });

  revalidatePath('/account/tokens');
  return { token: raw };
}

/** Revokes one of the caller's own tokens; ownership is part of the UPDATE. */
export async function revokeApiToken(tokenId: string): Promise<void> {
  const actor = await requireActor();
  await revokeApiTokenRow(tokenId, actor.userId);
  revalidatePath('/account/tokens');
}
