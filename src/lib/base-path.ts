/**
 * The app is served under a sub-path (`/agent-val`) rather than at the root, so it can
 * sit behind a reverse proxy alongside other services on the same hostname without an
 * extra DNS record. It is a build-time constant: change it here and rebuild.
 *
 * next/link and the router prefix this automatically; raw fetch() calls to our
 * own API routes do NOT, so they must go through apiPath().
 */
export const BASE_PATH = '/agent-val';

export function apiPath(path: string): string {
  return `${BASE_PATH}${path}`;
}

/**
 * Where the auth API is mounted, as an *absolute* path.
 *
 * better-auth knows nothing about Next's basePath: it routes by comparing the
 * request pathname against its own mount point, and builds every absolute URL
 * (the OIDC `redirect_uri` above all) from the same value. Both halves — the
 * server instance in `@/lib/auth` and the browser client in `@/lib/auth-client`
 * — therefore have to be told the full path, which is why it lives here rather
 * than in the server-only auth module.
 */
export const AUTH_BASE_PATH = `${BASE_PATH}/api/auth`;
