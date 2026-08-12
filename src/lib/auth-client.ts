'use client';

import { createAuthClient } from 'better-auth/react';
import { genericOAuthClient } from 'better-auth/client/plugins';
import { AUTH_BASE_PATH } from '@/lib/base-path';

/**
 * The browser half of better-auth.
 *
 * `baseURL` is the bare origin and `basePath` carries the Next basePath, so the
 * client resolves to `<origin>/agent-val/api/auth` — client fetches are never
 * prefixed for us (see base-path.ts).
 */
export const authClient = createAuthClient({
  baseURL: typeof window === 'undefined' ? undefined : window.location.origin,
  basePath: AUTH_BASE_PATH,
  plugins: [genericOAuthClient()],
});
