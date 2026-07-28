/**
 * API-key auth for the MCP endpoint.
 *
 * The web UI itself is protected by Caddy basic auth, which a `Authorization`
 * header can carry — so the key is read from `X-Api-Key` *first* and only falls
 * back to `Authorization: Bearer`. That way a client behind the reverse proxy
 * can send both credentials at once without either overwriting the other.
 */

import { timingSafeEqual } from 'node:crypto';

export const API_KEY_ENV = 'MCP_API_KEY';
export const API_KEY_HEADER = 'x-api-key';

export type AuthResult =
  | { ok: true }
  | { ok: false; status: number; message: string; challenge?: boolean };

/** The key the server expects, or null when the feature is not configured. */
export function configuredApiKey(): string | null {
  const value = process.env[API_KEY_ENV];
  const trimmed = typeof value === 'string' ? value.trim() : '';
  return trimmed.length > 0 ? trimmed : null;
}

function presentedKey(headers: Headers): string | null {
  const direct = headers.get(API_KEY_HEADER);
  if (direct && direct.trim().length > 0) return direct.trim();

  const authorization = headers.get('authorization');
  if (authorization) {
    const match = /^bearer\s+(.+)$/i.exec(authorization.trim());
    if (match) return match[1].trim();
  }
  return null;
}

function equals(a: string, b: string): boolean {
  const left = Buffer.from(a);
  const right = Buffer.from(b);
  // timingSafeEqual throws on a length mismatch, which is itself a difference.
  if (left.length !== right.length) return false;
  return timingSafeEqual(left, right);
}

/**
 * Checks a request's credentials against the expected key.
 *
 * With no key configured the endpoint refuses everything rather than opening up:
 * an unauthenticated write API reachable from the network is a worse default
 * than a broken one, and the error says exactly what to set.
 */
export function checkApiKey(headers: Headers, expected: string | null): AuthResult {
  if (!expected) {
    return {
      ok: false,
      status: 503,
      message: `The MCP endpoint is disabled: set ${API_KEY_ENV} on the server to enable it.`,
    };
  }

  const presented = presentedKey(headers);
  if (!presented) {
    return {
      ok: false,
      status: 401,
      message: `Missing API key. Send it as the "${API_KEY_HEADER}" header or as "Authorization: Bearer <key>".`,
      challenge: true,
    };
  }

  if (!equals(presented, expected)) {
    return { ok: false, status: 401, message: 'Invalid API key.', challenge: true };
  }

  return { ok: true };
}
