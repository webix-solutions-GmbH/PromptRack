import { toNextJsHandler } from 'better-auth/next-js';
import { auth, isFirstAccount } from '@/lib/auth';
import { BASE_PATH } from '@/lib/base-path';

export const dynamic = 'force-dynamic';

const handlers = toNextJsHandler(auth);

/**
 * Puts the Next basePath back on the request URL.
 *
 * Next strips whatever basePath is configured before a route handler sees
 * `request.url`, but better-auth routes by comparing that pathname against its
 * own configured mount point — and that mount point has to keep the basePath,
 * because the same value is what every absolute URL it builds (the OIDC
 * `redirect_uri` above all) is derived from. Handing it the public URL is what
 * makes both agree.
 *
 * With `BASE_PATH` empty (root deployment) this is a no-op: `${BASE_PATH}/`
 * is just `/`, which every pathname already starts with, so the early return
 * always fires. It stays written this way rather than special-cased so a
 * future non-empty BASE_PATH needs no change here.
 *
 * The body is buffered rather than piped: auth payloads are a few hundred
 * bytes, and a streamed body would need `duplex` handling for nothing.
 */
async function publicRequest(request: Request): Promise<Request> {
  const url = new URL(request.url);
  if (url.pathname.startsWith(`${BASE_PATH}/`)) return request;
  url.pathname = `${BASE_PATH}${url.pathname}`;

  const hasBody = request.method !== 'GET' && request.method !== 'HEAD';
  return new Request(url, {
    method: request.method,
    headers: request.headers,
    body: hasBody ? await request.arrayBuffer() : undefined,
  });
}

/**
 * Public registration is open for exactly one account — the first one, which
 * `databaseHooks.user.create.before` stamps as `admin`. After that every
 * further account is created by an admin (see /admin/users) or provisioned by
 * the OIDC provider, so the sign-up endpoint is refused outright.
 */
export async function POST(request: Request) {
  const { pathname } = new URL(request.url);
  if (pathname.endsWith('/sign-up/email') && !(await isFirstAccount())) {
    return Response.json(
      { message: 'Registration is closed. Ask an administrator for an account.' },
      { status: 403 },
    );
  }
  return handlers.POST(await publicRequest(request));
}

export async function GET(request: Request) {
  return handlers.GET(await publicRequest(request));
}
