/**
 * Authentication for the MCP endpoint.
 *
 * The credential is a per-user API token from /agent-val/account/tokens, read
 * from `X-Api-Key` *first* and only then from `Authorization: Bearer` — that
 * way a client behind a reverse proxy that demands basic auth can send both
 * credentials at once without either overwriting the other. A browser session
 * cookie is accepted too, so the endpoint can be poked from a signed-in tab.
 *
 * There is no "not configured" state any more: the endpoint is always on and
 * *tokens* are the gate, which is also what gives every call an actor whose
 * role decides which tools it may use.
 */

import { actorFromRequest, presentedToken, type Actor } from '@/lib/auth/guards';

export const API_KEY_HEADER = 'x-api-key';

export type McpAuthResult =
  | { ok: true; actor: Actor }
  | { ok: false; status: number; message: string; challenge?: boolean };

export async function authenticateMcp(request: Request): Promise<McpAuthResult> {
  const presented = presentedToken(request.headers);
  const actor = await actorFromRequest(request);

  if (actor) return { ok: true, actor };

  if (!presented) {
    return {
      ok: false,
      status: 401,
      message: `Missing API token. Create one at /agent-val/account/tokens and send it as the "${API_KEY_HEADER}" header (or as "Authorization: Bearer <token>").`,
      challenge: true,
    };
  }

  return {
    ok: false,
    status: 401,
    message: 'Invalid or revoked API token.',
    challenge: true,
  };
}
