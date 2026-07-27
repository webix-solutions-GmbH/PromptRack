/**
 * The app is served under a sub-path of ki01.webix.de (path-based routing in
 * Caddy instead of a subdomain, so no extra DNS record is needed).
 *
 * next/link and the router prefix this automatically; raw fetch() calls to our
 * own API routes do NOT, so they must go through apiPath().
 */
export const BASE_PATH = '/agent-val';

export function apiPath(path: string): string {
  return `${BASE_PATH}${path}`;
}
