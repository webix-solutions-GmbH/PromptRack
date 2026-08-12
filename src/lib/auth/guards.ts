/**
 * The single enforcement point for roles.
 *
 * Server actions and pages call `requireWriter`/`requireAdmin` as their first
 * statement; route handlers call `guardRequest`, which additionally accepts an
 * API token. Nothing else decides what a role may do — `@/lib/auth/policy` says
 * that, and this module is what asks.
 */

import { headers } from 'next/headers';
import { forbidden, unauthorized } from 'next/navigation';
import { auth } from '@/lib/auth';
import { canAdminister, canWrite, parseRole, type Role } from '@/lib/auth/policy';
import { resolveToken } from '@/lib/auth/tokens';

export interface Actor {
  userId: string;
  email: string;
  name: string;
  role: Role;
  via: 'session' | 'token';
  tokenId?: string;
}

export class AuthError extends Error {
  constructor(
    readonly status: 401 | 403,
    message: string,
  ) {
    super(message);
    this.name = 'AuthError';
  }
}

/** The shape better-auth returns; `role` is ours, so it is read defensively. */
type SessionUser = { id: string; email: string; name?: string | null; role?: unknown };

function toActor(user: SessionUser, via: 'session'): Actor {
  return {
    userId: user.id,
    email: user.email,
    name: user.name ?? user.email,
    role: parseRole(user.role),
    via,
  };
}

/** RSC / server-action context: reads the session cookie. Never throws. */
export async function currentActor(): Promise<Actor | null> {
  const session = await auth.api.getSession({ headers: await headers() });
  if (!session?.user) return null;
  return toActor(session.user, 'session');
}

export async function requireActor(): Promise<Actor> {
  const actor = await currentActor();
  if (!actor) throw new AuthError(401, 'Sign in to continue.');
  return actor;
}

/** Any content mutation: prompts, system prompts, tools, runs, ratings. */
export async function requireWriter(): Promise<Actor> {
  const actor = await requireActor();
  if (!canWrite(actor.role)) throw new AuthError(403, 'Your account is read-only.');
  return actor;
}

/** Machines, toolsets, user management. */
export async function requireAdmin(): Promise<Actor> {
  const actor = await requireActor();
  if (!canAdminister(actor.role)) throw new AuthError(403, 'Administrator access is required.');
  return actor;
}

/**
 * Runs a guard from a page and answers a refusal with Next's own 401/403
 * interrupt page rather than letting the throw surface as a 500.
 *
 * Pages only: `forbidden()`/`unauthorized()` are render-time interrupts. In a
 * server action the thrown `AuthError` stays the backstop — the actual UX
 * contract there is that a role never gets offered the control in the first
 * place, because Next replaces action errors with a generic message in
 * production.
 */
export async function onPage<T>(guard: () => Promise<T>): Promise<T> {
  try {
    return await guard();
  } catch (err) {
    if (err instanceof AuthError) {
      if (err.status === 401) unauthorized();
      forbidden();
    }
    throw err;
  }
}

/**
 * `x-api-key` first, `Authorization: Bearer` second — a reverse proxy's basic
 * auth can then ride along in `Authorization` without either credential
 * overwriting the other.
 */
export function presentedToken(requestHeaders: Headers): string | null {
  const direct = requestHeaders.get('x-api-key');
  if (direct?.trim()) return direct.trim();

  const authorization = requestHeaders.get('authorization');
  const match = authorization ? /^bearer\s+(.+)$/i.exec(authorization.trim()) : null;
  return match ? match[1].trim() : null;
}

/** Route-handler context: a session cookie *or* an API token. */
export async function actorFromRequest(request: Request): Promise<Actor | null> {
  const raw = presentedToken(request.headers);
  if (raw) {
    const owner = await resolveToken(raw);
    if (!owner) return null;
    return {
      userId: owner.userId,
      email: owner.email,
      name: owner.name,
      role: parseRole(owner.role),
      via: 'token',
      tokenId: owner.tokenId,
    };
  }

  const session = await auth.api.getSession({ headers: request.headers });
  if (!session?.user) return null;
  return toActor(session.user, 'session');
}

/** Route-handler convenience: an actor to proceed with, or the error Response. */
export async function guardRequest(
  request: Request,
  level: 'read' | 'write' | 'admin',
): Promise<{ actor: Actor } | { response: Response }> {
  const actor = await actorFromRequest(request);
  if (!actor) {
    return { response: Response.json({ error: 'Sign in to continue.' }, { status: 401 }) };
  }
  if (level === 'write' && !canWrite(actor.role)) {
    return { response: Response.json({ error: 'Your account is read-only.' }, { status: 403 }) };
  }
  if (level === 'admin' && !canAdminister(actor.role)) {
    return {
      response: Response.json({ error: 'Administrator access is required.' }, { status: 403 }),
    };
  }
  return { actor };
}
