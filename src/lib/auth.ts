/**
 * The better-auth server instance — sessions, passwords, optional OIDC.
 *
 * This is one of the few modules that talks to `db` directly: the auth tables
 * are global infrastructure, the thing a Scope is derived *from*, so they
 * deliberately do not go through the scoped repositories in src/db/repo.
 */

import { betterAuth } from 'better-auth';
import { drizzleAdapter } from 'better-auth/adapters/drizzle';
import { nextCookies } from 'better-auth/next-js';
import { admin as adminPlugin, genericOAuth } from 'better-auth/plugins';
import { count } from 'drizzle-orm';
import { db } from '@/db';
import { accounts, sessions, users, verifications } from '@/db/schema';
import { AUTH_BASE_PATH } from '@/lib/base-path';
import { parseRole } from '@/lib/auth/policy';

export const OIDC_PROVIDER_ID = 'oidc';

/**
 * The full mount URL better-auth builds every auth URL from (including the OIDC
 * `redirect_uri`).
 *
 * `BETTER_AUTH_URL` is the public origin *plus* the Next basePath; the
 * `/api/auth` suffix has to be appended here rather than left to better-auth's
 * `basePath` option, which `getBaseURL` ignores as soon as the configured URL
 * already carries a path of its own. Unset in dev, the origin is derived from
 * the request instead and `basePath` above supplies the same path.
 */
export function authBaseUrl(): string | undefined {
  const configured = process.env.BETTER_AUTH_URL?.trim();
  if (!configured) return undefined;
  return `${configured.replace(/\/+$/, '')}/api/auth`;
}

export function oidcConfigured(): boolean {
  return Boolean(process.env.OIDC_ISSUER && process.env.OIDC_CLIENT_ID);
}

export function oidcButtonLabel(): string {
  return process.env.OIDC_BUTTON_LABEL?.trim() || 'Single sign-on';
}

export function discoveryUrl(issuer: string): string {
  return `${issuer.replace(/\/+$/, '')}/.well-known/openid-configuration`;
}

/**
 * The role an OIDC-provisioned (or admin-created) account starts with. Anything
 * unrecognised in the env degrades to `viewer`, never to admin.
 */
export function defaultProvisionedRole() {
  return parseRole(process.env.OIDC_DEFAULT_ROLE ?? 'member');
}

async function userCount(): Promise<number> {
  const [row] = await db.select({ value: count() }).from(users);
  return row?.value ?? 0;
}

/** Exported for the sign-up gate in the auth route handler. */
export async function isFirstAccount(): Promise<boolean> {
  return (await userCount()) === 0;
}

/**
 * Maps an OIDC profile onto our user row.
 *
 * Entra ID does not reliably emit `email`; the ID token may only carry
 * `preferred_username` or `upn`. Keycloak/Authentik hit the first branch.
 */
export function mapProfileToUser(profile: Record<string, unknown>) {
  const email =
    (profile.email as string) ??
    (profile.preferred_username as string) ??
    (profile.upn as string);
  return {
    email,
    name: (profile.name as string) ?? (profile.given_name as string) ?? email,
    emailVerified: profile.email_verified === true,
  };
}

export const auth = betterAuth({
  // Both carry the Next basePath, so every auth URL better-auth builds
  // (including the OIDC redirect_uri) is /agent-val/api/auth/...
  baseURL: authBaseUrl(),
  basePath: AUTH_BASE_PATH,
  secret: process.env.BETTER_AUTH_SECRET,
  trustedOrigins: process.env.BETTER_AUTH_URL
    ? [new URL(process.env.BETTER_AUTH_URL).origin]
    : [],

  database: drizzleAdapter(db, {
    provider: 'pg',
    // Explicit model → table map: our exports are plural, the tables are not.
    schema: { user: users, session: sessions, account: accounts, verification: verifications },
  }),

  emailAndPassword: {
    enabled: true,
    minPasswordLength: 12,
    // Sign-up stays *enabled* here; the route handler allows it only while the
    // database has zero users — that is the admin bootstrap.
  },

  user: {
    additionalFields: {
      role: {
        type: 'string',
        required: false,
        defaultValue: 'viewer',
        input: false, // a client can never name its own role
      },
    },
  },

  session: {
    expiresIn: 60 * 60 * 24 * 30,
    updateAge: 60 * 60 * 24,
    // No cookieCache on purpose: a role change must bite on the next request.
  },

  databaseHooks: {
    user: {
      create: {
        // Runs after the admin plugin's own hook (plugin hooks are registered
        // first), so this is the role that reaches the insert.
        before: async (user) => {
          const first = await isFirstAccount();
          return { data: { ...user, role: first ? 'admin' : defaultProvisionedRole() } };
        },
      },
    },
  },

  plugins: [
    ...(oidcConfigured()
      ? [
          genericOAuth({
            config: [
              {
                providerId: OIDC_PROVIDER_ID,
                discoveryUrl: discoveryUrl(process.env.OIDC_ISSUER!),
                clientId: process.env.OIDC_CLIENT_ID!,
                clientSecret: process.env.OIDC_CLIENT_SECRET ?? '',
                scopes: (process.env.OIDC_SCOPES ?? 'openid,profile,email')
                  .split(',')
                  .map((scope) => scope.trim())
                  .filter(Boolean),
                mapProfileToUser,
              },
            ],
          }),
        ]
      : []),
    // Included only for its user-management endpoints (createUser/removeUser).
    // No `ac`/`createAccessControl`: a second authorization system parallel to
    // `requireWriter`/`requireAdmin` is exactly the failure mode to avoid.
    adminPlugin({ defaultRole: 'member', adminRoles: ['admin'] }),
    nextCookies(), // MUST stay last
  ],
});

export type AuthSession = typeof auth.$Infer.Session;
