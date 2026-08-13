import { getSessionCookie } from 'better-auth/cookies';
import { NextResponse, type NextRequest } from 'next/server';

const PUBLIC_PATHS = ['/login'];

/**
 * Optimistic gate only — `getSessionCookie` checks that a session cookie is
 * present, not that it is valid (better-auth says so explicitly). The
 * authoritative checks live in the server actions, the route handlers and the
 * pages; this exists so a signed-out visitor lands on /login instead of on a
 * rendered, empty app shell.
 *
 * Note on basePath: `nextUrl.pathname` is always without whatever basePath is
 * configured, and cloning nextUrl keeps it on the way out. Never build the
 * redirect from `request.url`, which would drop it. Currently a no-op in
 * practice since BASE_PATH is empty (root deployment), but this stays correct
 * if a prefix is restored in base-path.ts.
 */
export function proxy(request: NextRequest) {
  const { pathname } = request.nextUrl;
  const signedIn = Boolean(getSessionCookie(request));
  const isPublic = PUBLIC_PATHS.some((path) => pathname === path || pathname.startsWith(`${path}/`));

  if (!signedIn && !isPublic) {
    const url = request.nextUrl.clone();
    url.pathname = '/login';
    url.search =
      pathname === '/' ? '' : `?next=${encodeURIComponent(pathname + request.nextUrl.search)}`;
    return NextResponse.redirect(url);
  }

  if (signedIn && isPublic) {
    const url = request.nextUrl.clone();
    url.pathname = '/';
    url.search = '';
    return NextResponse.redirect(url);
  }

  return NextResponse.next();
}

export const config = {
  // Everything except API routes (they authenticate themselves, and /api/mcp
  // uses tokens rather than cookies) and static assets.
  matcher: ['/((?!api|_next/static|_next/image|favicon.ico).*)'],
};
