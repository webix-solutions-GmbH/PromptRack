# Phase 4 — Auth (better-auth, OIDC, roles, API tokens)

Implementation plan. Source spec: `docs/superpowers/specs/2026-08-12-platform-evolution-design.md` (Phase 4).
Target repo: `/Users/phil/Projects/Webix.AI.Agent-Model-Eval`. Branch: `master`.

**Environment for every shell command:**

```bash
export PATH="$HOME/.nvm/versions/node/v22.23.1/bin:$PATH"
```

---

## 0. Read this first — risks, open questions, decisions

### Hard prerequisites

- **Phase 4 assumes Phase 1–3 have landed**: committed `drizzle/` with drizzle's own `migrate()`, Postgres (`pgTable`, `DATABASE_URL`, `db` exported from `src/db/index.ts` over `node-postgres`/`postgres-js`), and the scoped data-access layer. Every schema snippet below is **Postgres** (`pgTable`, `text` ids, `timestamp`). If you find `sqliteTable` in `src/db/schema.ts`, **stop and report** — this plan cannot be executed against SQLite as written (better-auth's drizzle adapter needs `provider: 'pg'` and the timestamp columns below).
- If Phase 3 landed, server actions call a data-access module rather than `db` directly. That does not change this plan: guards go at the **top of the exported action**, before any data call.

### Verified against real docs (do not re-derive)

- **better-auth `1.6.27`** is `latest` on npm (checked `npm view better-auth version`). Everything below was checked against Context7's better-auth docs: `drizzleAdapter(db, { provider: 'pg', schema })`, `toNextJsHandler(auth)`, `nextCookies()` (must be **last** plugin), `getSessionCookie(request)` from `better-auth/cookies`, `auth.api.getSession({ headers })`, `user.additionalFields`, `databaseHooks.user.create.before/after`, `genericOAuth({ config: [{ providerId, discoveryUrl, clientId, clientSecret }] })`.
- **Next 16 renamed `middleware.ts` → `proxy.ts`** (verified in `node_modules/next/dist/docs/01-app/03-api-reference/03-file-conventions/proxy.md`: *"The `middleware` file convention is deprecated and has been renamed to `proxy`"*, and *"Proxy defaults to using the Node.js runtime. The `runtime` config option is not available in Proxy files"*). The spec says `middleware.ts`; **write `src/proxy.ts`** instead. The file must sit at the same level as `app`, i.e. `src/proxy.ts` (this repo has `src/app`).

### Decisions made here (rationale, so you don't relitigate them mid-task)

1. **API tokens are hand-rolled, not better-auth's apiKey plugin.** In 1.6 the plugin moved to a separate package (`@better-auth/api-key`) with a changed array-of-configs shape and a large table. We need ~60 lines: random token, SHA-256 at rest, prefix for display, revoke. It also has to slot into the existing `checkApiKey(headers, expected)` call sites and their tests in `src/lib/mcp/protocol.test.ts`.
2. **Roles live in one place: `user.role` (`admin|member|viewer`) via `user.additionalFields`, enforced by our own `requireRole` helper.** The better-auth `admin` plugin is included **only** for its user-management endpoints (`createUser`, `removeUser`, `revokeUserSessions`) — creating a local account without signing the *admin's* browser into it is otherwise awkward. Do **not** configure `ac`/`createAccessControl`: a second authorization system parallel to `requireRole` is the failure mode to avoid. Set `adminRoles: ['admin']` so the plugin's own endpoints answer only to our admins.
   - **Open question / risk:** it is unverified whether `auth.api.createUser` validates `role` against a configured role list. Task 15 therefore calls `createUser` **without** a role and sets `user.role` with a drizzle `UPDATE` immediately after — sidestepping the question. If `createUser` turns out to reject the call for another reason, fall back to the documented `auth.api.signUpEmail({ body, asResponse: true })` **called from a plain route handler that discards the returned `Set-Cookie`** (never from a server action, because `nextCookies()` would adopt the cookie and re-log the admin as the new user).
3. **First-ever account bootstraps as admin, and public sign-up then closes forever.** `emailAndPassword.enabled: true` keeps `/api/auth/sign-up/email` live; our catch-all route wrapper refuses it with 403 once `SELECT count(*) FROM "user" > 0`. `databaseHooks.user.create.before` stamps `role = 'admin'` for the first row, `OIDC_DEFAULT_ROLE ?? 'member'` afterwards. No bootstrap env var, no seeding script — matches the spec sentence literally.
4. **The proxy is an optimistic cookie check only** (`getSessionCookie`), per better-auth's own warning that it "only verifies the presence of a session cookie and does not perform validation". Authoritative checks are in server actions, route handlers, and pages. Never let the proxy be the only gate.
5. **No `session.cookieCache`.** A role change must take effect on the next request; the cost is one DB read per `getSession`, which is nothing at this scale.
6. **Toolsets split**: toolset create/update/delete = **admin** (they hold `mcp_url` + headers = credentials); tool CRUD inside a toolset = **member** (that is content). `/api/machines/[id]/discover` = **member**, not admin, because `/runs/new` POSTs it on page load for every user; `/api/machines/[id]/test` = admin.
7. **Viewers can use MCP read tools.** `handleMcpMessage` gains a context argument and refuses any `readOnly !== true` tool for a viewer token — reusing the `readOnly` flag `McpToolSpec` already carries.

### Risks to keep in view

- **basePath.** `next/link` and the router prefix `/agent-val` automatically; **raw `fetch()` and any URL you construct in the proxy do not.** In the proxy, build redirects with `request.nextUrl.clone()` (a `NextURL` re-prepends basePath on serialization) — **never** `new URL('/login', request.url)`, which drops it. Client fetches go through `apiPath()` from `src/lib/base-path.ts`. Verification step in Task 7 checks the `Location:` header literally.
- **Entra ID claims.** Entra does not always emit an `email` claim; the ID token may only carry `preferred_username`/`upn`. Task 4 therefore configures `mapProfileToUser` with an `email ?? preferred_username ?? upn` fallback. Keycloak/Authentik emit `email` normally.
- **Server action errors in production.** Next replaces thrown server-action errors with a generic message in prod builds. Guard failures are therefore not a user-facing error channel: the UI must not *offer* actions a role cannot perform (Task 9 hides them). The throw is the backstop, not the UX.
- **`/api/runs/[id]/execute` streams NDJSON.** Its 401/403 must be a plain JSON `Response` returned *before* the stream is constructed.
- **`data/` and OIDC secrets.** `.env*` is already gitignored; add `.env.example` only.

---

## Phase 4 tasks

### Task 1 — Dependencies and environment plumbing

**Edit** `package.json`, **create** `.env.example`, **edit** `docker-compose.yml`, **edit** `Dockerfile` (only if it enumerates env vars).

1. Install: `npm install better-auth@^1.6.27`. (Nothing else — no `@better-auth/api-key`, no bcrypt; better-auth ships its own password hashing.)
2. Create `.env.example` at repo root:

```bash
# --- Database (Phase 2) -----------------------------------------------------
DATABASE_URL=postgres://agentval:agentval@localhost:5432/agentval

# --- Auth (Phase 4) ---------------------------------------------------------
# 32+ random bytes: `openssl rand -base64 32`
BETTER_AUTH_SECRET=replace-me
# Public origin *including* the Next.js basePath (/agent-val).
BETTER_AUTH_URL=http://localhost:3000/agent-val

# Optional generic OIDC provider (Entra ID / Keycloak / Authentik / ...).
# Leave OIDC_ISSUER empty to disable the "Sign in with SSO" button entirely.
# Redirect URI to register with the IdP:
#   ${BETTER_AUTH_URL}/api/auth/oauth2/callback/oidc
OIDC_ISSUER=
OIDC_CLIENT_ID=
OIDC_CLIENT_SECRET=
OIDC_SCOPES=openid,profile,email
OIDC_BUTTON_LABEL=Single sign-on
# Role given to auto-provisioned OIDC users: admin | member | viewer
OIDC_DEFAULT_ROLE=member

# Set to "true" to expose /api/mock-llm and /api/mock-mcp in a production build.
ENABLE_MOCKS=
```

3. `docker-compose.yml`: **delete** the `MCP_API_KEY` environment line (Task 14 removes the variable entirely) and add `BETTER_AUTH_SECRET`, `BETTER_AUTH_URL`, `OIDC_*`, `OIDC_DEFAULT_ROLE`, each as `- NAME=${NAME:-}`.

**Verify:** `npm ls better-auth` prints `better-auth@1.6.x`; `grep -c MCP_API_KEY docker-compose.yml` prints `0`; `git check-ignore .env.example` exits non-zero (the example file is *not* ignored — `.gitignore` has `.env*`, so add an explicit `!.env.example` line and re-check).

---

### Task 2 — Auth tables in the drizzle schema

**Edit** `src/db/schema.ts` — append a new section at the end; touch nothing that exists.

Table **names** must stay better-auth's defaults (`user`, `session`, `account`, `verification`); the exported drizzle symbols are plural to match this file's style, and Task 4 hands the adapter an explicit model→table map so the two never have to agree by convention.

```ts
// ---------------------------------------------------------------------------
// auth (better-auth core tables — table names are better-auth's defaults)
// ---------------------------------------------------------------------------
export const users = pgTable('user', {
  id: text('id').primaryKey(),
  name: text('name').notNull(),
  email: text('email').notNull().unique(),
  emailVerified: boolean('email_verified').notNull().default(false),
  image: text('image'),
  // App role. `viewer` is the safe default for anything we did not stamp.
  role: text('role', { enum: ['admin', 'member', 'viewer'] }).notNull().default('viewer'),
  // better-auth admin plugin fields:
  banned: boolean('banned').notNull().default(false),
  banReason: text('ban_reason'),
  banExpires: timestamp('ban_expires', { withTimezone: true }),
  createdAt: timestamp('created_at', { withTimezone: true }).notNull().defaultNow(),
  updatedAt: timestamp('updated_at', { withTimezone: true }).notNull().defaultNow(),
});

export const sessions = pgTable(
  'session',
  {
    id: text('id').primaryKey(),
    token: text('token').notNull().unique(),
    userId: text('user_id').notNull().references(() => users.id, { onDelete: 'cascade' }),
    expiresAt: timestamp('expires_at', { withTimezone: true }).notNull(),
    ipAddress: text('ip_address'),
    userAgent: text('user_agent'),
    impersonatedBy: text('impersonated_by'),
    createdAt: timestamp('created_at', { withTimezone: true }).notNull().defaultNow(),
    updatedAt: timestamp('updated_at', { withTimezone: true }).notNull().defaultNow(),
  },
  (table) => [index('session_user_id_idx').on(table.userId)],
);

export const accounts = pgTable(
  'account',
  {
    id: text('id').primaryKey(),
    accountId: text('account_id').notNull(),
    providerId: text('provider_id').notNull(),
    userId: text('user_id').notNull().references(() => users.id, { onDelete: 'cascade' }),
    accessToken: text('access_token'),
    refreshToken: text('refresh_token'),
    idToken: text('id_token'),
    accessTokenExpiresAt: timestamp('access_token_expires_at', { withTimezone: true }),
    refreshTokenExpiresAt: timestamp('refresh_token_expires_at', { withTimezone: true }),
    scope: text('scope'),
    password: text('password'),
    createdAt: timestamp('created_at', { withTimezone: true }).notNull().defaultNow(),
    updatedAt: timestamp('updated_at', { withTimezone: true }).notNull().defaultNow(),
  },
  (table) => [index('account_user_id_idx').on(table.userId)],
);

export const verifications = pgTable(
  'verification',
  {
    id: text('id').primaryKey(),
    identifier: text('identifier').notNull(),
    value: text('value').notNull(),
    expiresAt: timestamp('expires_at', { withTimezone: true }).notNull(),
    createdAt: timestamp('created_at', { withTimezone: true }).notNull().defaultNow(),
    updatedAt: timestamp('updated_at', { withTimezone: true }).notNull().defaultNow(),
  },
  (table) => [index('verification_identifier_idx').on(table.identifier)],
);

// ---------------------------------------------------------------------------
// api_tokens — per-user bearer tokens for /api/mcp (hashed at rest)
// ---------------------------------------------------------------------------
export const apiTokens = pgTable(
  'api_tokens',
  {
    id: text('id').primaryKey(),
    userId: text('user_id').notNull().references(() => users.id, { onDelete: 'cascade' }),
    name: text('name').notNull(),
    /** SHA-256 hex of the raw token. The raw value is shown exactly once. */
    tokenHash: text('token_hash').notNull().unique(),
    /** First 12 chars of the raw token, for recognising it in a list. */
    prefix: text('prefix').notNull(),
    createdAt: timestamp('created_at', { withTimezone: true }).notNull().defaultNow(),
    lastUsedAt: timestamp('last_used_at', { withTimezone: true }),
    expiresAt: timestamp('expires_at', { withTimezone: true }),
    revokedAt: timestamp('revoked_at', { withTimezone: true }),
  },
  (table) => [index('api_tokens_user_id_idx').on(table.userId)],
);

export type User = typeof users.$inferSelect;
export type ApiToken = typeof apiTokens.$inferSelect;
```

Add `boolean`, `timestamp`, `index` to the `drizzle-orm/pg-core` import if missing.

**Verify:**
```bash
npx tsc --noEmit                 # clean
npm run db:init                  # Phase-1 script: drizzle-kit generate + migrate
psql "$DATABASE_URL" -c '\d "user"' -c '\d api_tokens'
```
Expect a new `drizzle/00NN_*.sql` containing `CREATE TABLE "user"`, `"session"`, `"account"`, `"verification"`, `"api_tokens"` and **no** `DROP` of an existing table. Commit the generated SQL.

---

### Task 3 — Pure role policy module (+ tests)

**Create** `src/lib/auth/policy.ts` and `src/lib/auth/policy.test.ts`.

This is the only place role semantics are written down, and it is pure so it is unit-testable — matching the repo's "unit tests only where logic is pure and fiddly" convention.

```ts
export const ROLES = ['admin', 'member', 'viewer'] as const;
export type Role = (typeof ROLES)[number];

export const ROLE_LABELS: Record<Role, string> = {
  admin: 'Admin',
  member: 'Member',
  viewer: 'Viewer',
};

export const ROLE_DESCRIPTIONS: Record<Role, string> = {
  admin: 'Everything a member can do, plus user management, machines and toolset credentials.',
  member: 'Prompts, system prompts, tools, runs and ratings.',
  viewer: 'Read-only.',
};

/** Unknown/legacy values degrade to the least privileged role, never to admin. */
export function parseRole(value: unknown): Role {
  return typeof value === 'string' && (ROLES as readonly string[]).includes(value)
    ? (value as Role)
    : 'viewer';
}

/** May change content: prompts, system prompts, tools, runs, ratings. */
export function canWrite(role: Role): boolean {
  return role === 'admin' || role === 'member';
}

/** May change infrastructure and users: machines, toolsets, roles, other users' tokens. */
export function canAdminister(role: Role): boolean {
  return role === 'admin';
}
```

Tests: every role through `canWrite`/`canAdminister`; `parseRole('')`, `parseRole(null)`, `parseRole('owner')`, `parseRole('ADMIN')` all → `'viewer'`; `parseRole('admin')` → `'admin'`.

**Verify:** `npx vitest run src/lib/auth/policy.test.ts` — all pass.

---

### Task 4 — better-auth server instance

**Create** `src/lib/auth.ts`.

```ts
import { betterAuth } from 'better-auth';
import { drizzleAdapter } from 'better-auth/adapters/drizzle';
import { nextCookies } from 'better-auth/next-js';
import { admin as adminPlugin, genericOAuth } from 'better-auth/plugins';
import { count, eq } from 'drizzle-orm';
import { db } from '@/db';
import { accounts, sessions, users, verifications } from '@/db/schema';
import { parseRole } from '@/lib/auth/policy';

export const OIDC_PROVIDER_ID = 'oidc';

export function oidcConfigured(): boolean {
  return Boolean(process.env.OIDC_ISSUER && process.env.OIDC_CLIENT_ID);
}

export function oidcButtonLabel(): string {
  return process.env.OIDC_BUTTON_LABEL?.trim() || 'Single sign-on';
}

function discoveryUrl(issuer: string): string {
  return `${issuer.replace(/\/+$/, '')}/.well-known/openid-configuration`;
}

async function userCount(): Promise<number> {
  const [row] = await db.select({ value: count() }).from(users);
  return row?.value ?? 0;
}

/** Exported for the sign-up gate in the auth route handler. */
export async function isFirstAccount(): Promise<boolean> {
  return (await userCount()) === 0;
}

export const auth = betterAuth({
  // baseURL includes the Next basePath, so every auth URL better-auth builds
  // (including the OIDC redirect_uri) is /agent-val/api/auth/...
  baseURL: process.env.BETTER_AUTH_URL,
  basePath: '/api/auth',
  secret: process.env.BETTER_AUTH_SECRET,
  trustedOrigins: (process.env.BETTER_AUTH_URL ? [new URL(process.env.BETTER_AUTH_URL).origin] : []),

  database: drizzleAdapter(db, {
    provider: 'pg',
    // Explicit model → table map: our exports are plural, the tables are not.
    schema: { user: users, session: sessions, account: accounts, verification: verifications },
  }),

  emailAndPassword: {
    enabled: true,
    minPasswordLength: 12,
    // Sign-up stays *enabled* here; the route handler allows it only while the
    // database has zero users (see Task 5) — that is the admin bootstrap.
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
        before: async (user) => {
          const first = await isFirstAccount();
          const fallback = parseRole(process.env.OIDC_DEFAULT_ROLE ?? 'member');
          return { data: { ...user, role: first ? 'admin' : fallback } };
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
                  .map((s) => s.trim())
                  .filter(Boolean),
                // Entra ID does not reliably emit `email`; fall back to the
                // UPN claims. Keycloak/Authentik hit the first branch.
                mapProfileToUser: (profile: Record<string, unknown>) => {
                  const email =
                    (profile.email as string) ??
                    (profile.preferred_username as string) ??
                    (profile.upn as string);
                  return {
                    email,
                    name:
                      (profile.name as string) ??
                      (profile.given_name as string) ??
                      email,
                    emailVerified: profile.email_verified === true,
                  };
                },
              },
            ],
          }),
        ]
      : []),
    adminPlugin({ defaultRole: 'member', adminRoles: ['admin'] }),
    nextCookies(), // MUST stay last
  ],
});

export type AuthSession = typeof auth.$Infer.Session;
```

Note for later tasks: `eq` is imported for Task 15's role update if you keep user admin helpers here; drop the import if unused (eslint will tell you).

**Verify:** `npx tsc --noEmit` clean. Then a smoke check that config loads (it touches the DB only lazily):
```bash
node --input-type=module -e "process.env.BETTER_AUTH_URL='http://localhost:3000/agent-val'; await import('./src/lib/auth.ts')" 2>&1 | head -5
```
(If a bare `node` import of TS is inconvenient, the real verification is Task 5's curl.)

---

### Task 5 — Auth route handler with the sign-up gate

**Create** `src/app/api/auth/[...all]/route.ts`.

```ts
import { toNextJsHandler } from 'better-auth/next-js';
import { auth, isFirstAccount } from '@/lib/auth';

export const dynamic = 'force-dynamic';

const handlers = toNextJsHandler(auth);

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
  return handlers.POST(request);
}

export const GET = handlers.GET;
```

**Verify** (dev server running, `BETTER_AUTH_SECRET`/`BETTER_AUTH_URL` set, empty auth tables):

```bash
npm run dev &
curl -s http://localhost:3000/agent-val/api/auth/ok            # -> {"ok":true} (better-auth health route)
curl -s -X POST http://localhost:3000/agent-val/api/auth/sign-up/email \
  -H 'content-type: application/json' \
  -d '{"email":"admin@example.com","password":"correct-horse-battery","name":"Admin"}' -i | head -20
psql "$DATABASE_URL" -c 'select email, role from "user";'      # -> admin@example.com | admin
# second sign-up must be refused:
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://localhost:3000/agent-val/api/auth/sign-up/email \
  -H 'content-type: application/json' -d '{"email":"b@example.com","password":"correct-horse-battery","name":"B"}'
# -> 403
```
The first call must return 200 with a `set-cookie` for the session cookie.

---

### Task 6 — Guard helpers (`requireActor` / `requireRole`) and token hashing

**Create** `src/lib/auth/tokens.ts`, `src/lib/auth/tokens.test.ts`, `src/lib/auth/guards.ts`.

`tokens.ts` — pure crypto helpers plus one DB lookup:

```ts
import { createHash, randomBytes, randomUUID } from 'node:crypto';
import { and, eq, isNull, gt, or } from 'drizzle-orm';
import { db } from '@/db';
import { apiTokens, users } from '@/db/schema';

export const TOKEN_PREFIX = 'amv_';
const PREFIX_DISPLAY_LEN = 12;

/** 32 random bytes, base64url — the raw token is shown to the user once. */
export function generateToken(): string {
  return TOKEN_PREFIX + randomBytes(32).toString('base64url');
}

/**
 * SHA-256, not bcrypt: this is a 256-bit random secret, not a password, so
 * there is nothing to brute-force and every MCP request pays the cost.
 */
export function hashToken(raw: string): string {
  return createHash('sha256').update(raw.trim()).digest('hex');
}

export function tokenDisplayPrefix(raw: string): string {
  return raw.slice(0, PREFIX_DISPLAY_LEN);
}

export function newTokenId(): string {
  return randomUUID();
}

export interface TokenOwner {
  tokenId: string;
  userId: string;
  email: string;
  name: string;
  role: string;
}

/** Resolves a raw token to its (unrevoked, unexpired) owner, or null. */
export async function resolveToken(raw: string): Promise<TokenOwner | null> {
  const [row] = await db
    .select({
      tokenId: apiTokens.id,
      userId: users.id,
      email: users.email,
      name: users.name,
      role: users.role,
      banned: users.banned,
    })
    .from(apiTokens)
    .innerJoin(users, eq(users.id, apiTokens.userId))
    .where(and(eq(apiTokens.tokenHash, hashToken(raw)), isNull(apiTokens.revokedAt)));

  if (!row || row.banned) return null;
  await db.update(apiTokens).set({ lastUsedAt: new Date() }).where(eq(apiTokens.id, row.tokenId));
  return row;
}
```

Also add an expiry check inside `resolveToken` (`expiresAt === null || expiresAt > now`) — express it in the `where` with `or(isNull(apiTokens.expiresAt), gt(apiTokens.expiresAt, new Date()))`.

`tokens.test.ts` covers the pure half only: `generateToken()` starts with `amv_`, is ≥ 40 chars, and two calls differ; `hashToken` is 64 hex chars, stable, whitespace-trimmed, and differs for differing input; `tokenDisplayPrefix` length.

`guards.ts` — the API every action and route handler uses:

```ts
import { headers } from 'next/headers';
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
  constructor(readonly status: 401 | 403, message: string) {
    super(message);
    this.name = 'AuthError';
  }
}

/** RSC / server-action context: reads the session cookie. Never throws. */
export async function currentActor(): Promise<Actor | null> {
  const session = await auth.api.getSession({ headers: await headers() });
  if (!session?.user) return null;
  return {
    userId: session.user.id,
    email: session.user.email,
    name: session.user.name ?? session.user.email,
    role: parseRole((session.user as { role?: unknown }).role),
    via: 'session',
  };
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
 * Route-handler context. Accepts a session cookie *or* an `x-api-key` /
 * `Authorization: Bearer` API token — x-api-key first, so a reverse proxy's
 * basic auth can travel in `Authorization` at the same time.
 */
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
  return {
    userId: session.user.id,
    email: session.user.email,
    name: session.user.name ?? session.user.email,
    role: parseRole((session.user as { role?: unknown }).role),
    via: 'session',
  };
}

export function presentedToken(headers: Headers): string | null {
  const direct = headers.get('x-api-key');
  if (direct?.trim()) return direct.trim();
  const authorization = headers.get('authorization');
  const match = authorization ? /^bearer\s+(.+)$/i.exec(authorization.trim()) : null;
  return match ? match[1].trim() : null;
}

/** Route-handler convenience: returns a JSON error Response, or null to proceed. */
export async function guardRequest(
  request: Request,
  level: 'read' | 'write' | 'admin',
): Promise<{ actor: Actor } | { response: Response }> {
  const actor = await actorFromRequest(request);
  if (!actor) return { response: Response.json({ error: 'Sign in to continue.' }, { status: 401 }) };
  if (level === 'write' && !canWrite(actor.role))
    return { response: Response.json({ error: 'Your account is read-only.' }, { status: 403 }) };
  if (level === 'admin' && !canAdminister(actor.role))
    return { response: Response.json({ error: 'Administrator access is required.' }, { status: 403 }) };
  return { actor };
}
```

**Verify:** `npx vitest run src/lib/auth/tokens.test.ts` passes; `npx tsc --noEmit` clean.

---

### Task 7 — Route gating in `src/proxy.ts`

**Create** `src/proxy.ts` (Next 16 file convention; **not** `middleware.ts`).

```ts
import { NextResponse, type NextRequest } from 'next/server';
import { getSessionCookie } from 'better-auth/cookies';

const PUBLIC_PATHS = ['/login'];

/**
 * Optimistic gate only — `getSessionCookie` checks presence, not validity
 * (better-auth says so explicitly). The authoritative checks live in the
 * server actions, the route handlers and the pages; this exists so a signed-out
 * visitor lands on /login instead of on a rendered, empty app shell.
 *
 * Note on basePath: `nextUrl.pathname` is *without* /agent-val, and cloning
 * nextUrl keeps the basePath on the way out. Never build the redirect from
 * `request.url`, which would drop it.
 */
export function proxy(request: NextRequest) {
  const { pathname } = request.nextUrl;
  const signedIn = Boolean(getSessionCookie(request));
  const isPublic = PUBLIC_PATHS.some((p) => pathname === p || pathname.startsWith(`${p}/`));

  if (!signedIn && !isPublic) {
    const url = request.nextUrl.clone();
    url.pathname = '/login';
    url.search = pathname === '/' ? '' : `?next=${encodeURIComponent(pathname + request.nextUrl.search)}`;
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
```

**Verify** (dev server up, no session cookie):
```bash
curl -s -o /dev/null -D - http://localhost:3000/agent-val/runs | grep -i '^location'
# -> location: http://localhost:3000/agent-val/login?next=%2Fruns     (note /agent-val)
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:3000/agent-val/login          # -> 200
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://localhost:3000/agent-val/api/mcp # -> 401, not 307
```
The `/agent-val` in the `Location` header is the whole point of this check — if it is missing, the redirect is built from the wrong URL object.

---

### Task 8 — `/login` page (password + OIDC + first-account bootstrap)

**Create** `src/lib/auth-client.ts`, `src/app/login/page.tsx`, `src/components/auth/login-form.tsx`.

`src/lib/auth-client.ts`:

```ts
'use client';
import { createAuthClient } from 'better-auth/react';
import { genericOAuthClient } from 'better-auth/client/plugins';
import { BASE_PATH } from '@/lib/base-path';

export const authClient = createAuthClient({
  // basePath is not applied to client fetches automatically (see base-path.ts),
  // so the origin + /agent-val is spelled out here.
  baseURL: typeof window === 'undefined' ? undefined : `${window.location.origin}${BASE_PATH}`,
  basePath: '/api/auth',
  plugins: [genericOAuthClient()],
});
```

`src/app/login/page.tsx` (server component): reads `isFirstAccount()` and `oidcConfigured()`/`oidcButtonLabel()` from `@/lib/auth`, renders `<LoginForm bootstrap={…} oidc={…} oidcLabel={…} next={searchParams.next} />`. Do not import `db` here beyond that helper. `searchParams` is a Promise in Next 16 — `const { next } = await searchParams`.

`login-form.tsx` (`'use client'`):
- **bootstrap mode** (no users yet): name + email + password + confirm; submits `authClient.signUp.email({ name, email, password, callbackURL })`; headline copy states plainly that this first account becomes the administrator.
- **normal mode**: email + password → `authClient.signIn.email({ email, password, callbackURL: next ?? '/' })`; error text from the response's `error.message`.
- **OIDC button** (only when `oidc`): `authClient.signIn.oauth2({ providerId: 'oidc', callbackURL: next ?? '/' })`.
- `callbackURL` values are **app-relative** (`/runs`); better-auth resolves them against `baseURL`, which already carries `/agent-val`.
- Style with the same Tailwind zinc palette the rest of the app uses (see `src/components/runs/new-run-form.tsx` for the existing input/button classes).

**Verify:** with zero users, `http://localhost:3000/agent-val/login` shows the bootstrap form and creating the account lands on `/agent-val/`; sign out, sign back in with the password; the browser network tab shows the POST going to `/agent-val/api/auth/sign-in/email` (a 404 here means `baseURL` lost the basePath). With `OIDC_ISSUER` set to a local Keycloak, the SSO button round-trips and `psql -c 'select email, role from "user"'` shows the new user as `member`.

---

### Task 9 — Session in the shell: user chip, sign-out, role-aware nav

**Edit** `src/app/layout.tsx`, `src/components/sidebar-nav.tsx`; **create** `src/components/auth/user-menu.tsx`.

1. `layout.tsx` becomes `async`; calls `currentActor()`. When null (i.e. `/login`, which the proxy lets through) render `{children}` without the sidebar chrome. Otherwise pass `role={actor.role}` to `<SidebarNav>` and render `<UserMenu name email role />` at the bottom of the aside.
2. `SidebarNav` takes `role: Role`. Add `{ href: '/admin/users', label: 'Users', adminOnly: true }` and `{ href: '/account/tokens', label: 'API tokens' }` to `NAV_ITEMS`; filter `adminOnly` items unless `role === 'admin'`. Keep `usePathname()` as-is — it already returns basePath-free paths.
3. `UserMenu` (`'use client'`): shows name, email, `ROLE_LABELS[role]`; a "Sign out" button calling `await authClient.signOut()` then `router.push('/login')` and `router.refresh()`.
4. **Hide what a viewer cannot do** — this is the actual UX contract, since server-action errors are opaque in prod. Pass `role` down and drop the create/edit/delete controls for viewers in: `src/components/create-toggle.tsx` call sites, `src/components/runs/result-rating.tsx`, `run-comment.tsx`, `archive-run-button.tsx`, `delete-run-button.tsx`, `src/components/prompts/prompts-panel.tsx`, `group-sidebar.tsx`, `src/components/system-prompts/system-prompt-row.tsx`, `src/components/toolsets/*`, `src/components/machines/*` (machines: admin only), and the "New run" link on `/runs`. The simplest mechanism: each page (server component) already fetches data — have it call `currentActor()` and pass `canWrite`/`canAdminister` booleans into the client components it renders.

**Verify:** sign in as the admin — every nav item is present. Create a `viewer` (Task 15) and sign in as them: `/machines` renders rows but no Add/Edit/Delete, `/admin/users` is absent from the nav and returns 403 when typed in, rating buttons are gone from a run page.

---

### Task 10 — Guards in every server action

**Edit** `src/actions/machines.ts`, `src/actions/prompts.ts`, `src/actions/system-prompts.ts`, `src/actions/runs.ts`, `src/actions/toolsets.ts`.

Add `await requireWriter();` or `await requireAdmin();` as the **first statement** of each exported action, before any validation or DB access. There are **26** exported actions; the full table (this is the checklist — every row must be done):

| File | Action | Guard |
|---|---|---|
| machines.ts | `createMachine` | `requireAdmin` |
| machines.ts | `updateMachine` | `requireAdmin` |
| machines.ts | `deleteMachine` | `requireAdmin` |
| machines.ts | `addManualModel` | `requireAdmin` |
| system-prompts.ts | `createSystemPrompt` | `requireWriter` |
| system-prompts.ts | `updateSystemPrompt` | `requireWriter` |
| system-prompts.ts | `deleteSystemPrompt` | `requireWriter` |
| prompts.ts | `createGroup` | `requireWriter` |
| prompts.ts | `updateGroup` | `requireWriter` |
| prompts.ts | `deleteGroup` | `requireWriter` |
| prompts.ts | `createPrompt` | `requireWriter` |
| prompts.ts | `updatePrompt` | `requireWriter` |
| prompts.ts | `deletePrompt` | `requireWriter` |
| runs.ts | `createRun` | `requireWriter` |
| runs.ts | `updateRunComment` | `requireWriter` |
| runs.ts | `rateResult` | `requireWriter` |
| runs.ts | `updateResultNote` | `requireWriter` |
| runs.ts | `setRunArchived` | `requireWriter` |
| runs.ts | `deleteRun` | `requireWriter` |
| toolsets.ts | `createToolset` | `requireAdmin` |
| toolsets.ts | `updateToolset` | `requireAdmin` |
| toolsets.ts | `deleteToolset` | `requireAdmin` |
| toolsets.ts | `createTool` | `requireWriter` |
| toolsets.ts | `updateTool` | `requireWriter` |
| toolsets.ts | `deleteTool` | `requireWriter` |
| toolsets.ts | `setToolEnabled` | `requireWriter` |

`createRun` ends in `redirect(...)`; the guard goes above everything, so nothing changes there. Note that `redirect()` throws — never wrap an action body in `try/catch` while adding guards.

**Verify:**
```bash
grep -c 'requireWriter\|requireAdmin' src/actions/*.ts
# machines.ts 4 (+1 import), prompts.ts 6, system-prompts.ts 3, runs.ts 6, toolsets.ts 7
grep -n 'export async function' -A2 src/actions/runs.ts | head -40   # guard is the first line of each body
```
Manual: as a viewer, use devtools to POST a rating server action (or temporarily un-hide a button) → the action fails and the rating does not change in `psql`.

---

### Task 11 — Guards in route handlers

**Edit** `src/app/api/machines/[id]/discover/route.ts`, `src/app/api/machines/[id]/test/route.ts`, `src/app/api/toolsets/[id]/discover/route.ts`, `src/app/api/runs/[id]/execute/route.ts`.

Pattern, as the first lines of each `POST`/`GET`:

```ts
const guard = await guardRequest(request, 'write'); // or 'admin'
if ('response' in guard) return guard.response;
```

- `machines/[id]/discover` → `'write'` (**not** admin: `/runs/new` posts it for every user on page load, and the response contains model ids only, never the api key).
- `machines/[id]/test` → `'admin'` (probes an endpoint with its stored credentials).
- `toolsets/[id]/discover` → `'admin'` (uses `mcp_url` + headers).
- `runs/[id]/execute` → `'write'`, and the guard must return **before** the `ReadableStream` is constructed, so a refusal is plain JSON rather than a truncated NDJSON stream.

**Verify:**
```bash
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://localhost:3000/agent-val/api/runs/1/execute      # -> 401
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://localhost:3000/agent-val/api/machines/1/test     # -> 401
# with a viewer's session cookie: 403 on all four; with a member's: 403 only on test + toolsets/discover.
```

---

### Task 12 — Dev-gate the mock endpoints

**Edit** `src/app/api/mock-llm/chat/completions/route.ts`, `src/app/api/mock-llm/models/route.ts`, `src/app/api/mock-mcp/route.ts`.

**Create** `src/lib/dev-only.ts`:

```ts
/**
 * The mocks (a fake OpenAI endpoint and a fake MCP server) exist to exercise
 * the executor without a real model. They ship in the production image, so they
 * are switched off there unless ENABLE_MOCKS says otherwise — a 404, not a 403,
 * because in production these routes should not appear to exist.
 */
export function mocksEnabled(): boolean {
  return process.env.NODE_ENV !== 'production' || process.env.ENABLE_MOCKS === 'true';
}

export function mockDisabledResponse(): Response {
  return new Response('Not found', { status: 404 });
}
```

Add `if (!mocksEnabled()) return mockDisabledResponse();` as the first line of every exported handler in those three files (`GET` and `POST` both, where present).

**Verify:** `NODE_ENV=production npm run build && NODE_ENV=production npm run start` then
```bash
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:3000/agent-val/api/mock-llm/models   # -> 404
```
and in `npm run dev` the same URL returns 200 with the model list.

---

### Task 13 — API tokens: actions + management UI

**Create** `src/actions/api-tokens.ts`, `src/app/account/tokens/page.tsx`, `src/components/auth/token-list.tsx`, `src/components/auth/create-token-form.tsx`.

`src/actions/api-tokens.ts` (`'use server'`):

```ts
export async function createApiToken(formData: FormData): Promise<{ token: string }>
export async function revokeApiToken(tokenId: string): Promise<void>
```

- `createApiToken`: `const actor = await requireActor()` (any role may hold a token — a viewer's token can only call read tools). Read `name` (required, trimmed) and optional `expiresInDays` (integer 1…3650). `const raw = generateToken()`, insert `{ id: newTokenId(), userId: actor.userId, name, tokenHash: hashToken(raw), prefix: tokenDisplayPrefix(raw), expiresAt }`, `revalidatePath('/account/tokens')`, return `{ token: raw }`. **The raw token is returned exactly once and never stored.**
- `revokeApiToken`: `requireActor()`; `UPDATE api_tokens SET revoked_at = now() WHERE id = $1 AND user_id = $2` — the `user_id` predicate is the ownership check, so one user can never revoke another's token. An admin revoking someone else's token is out of scope for this phase (deleting the user cascades).

`/account/tokens` page (server component): `requireActor()`, list this user's tokens (`prefix`, name, created, last used, expiry, revoked) newest first, plus the create form. The create form is a client component that keeps the returned raw token in state and shows it in a copyable box with "This is the only time the token is shown", including the exact curl line:

```
curl -X POST https://<host>/agent-val/api/mcp -H 'x-api-key: amv_…' -H 'content-type: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

**Verify:** create a token in the UI; `psql "$DATABASE_URL" -c 'select name, prefix, token_hash from api_tokens;'` shows a 64-hex hash and **no** raw token; the displayed raw value's SHA-256 (`printf %s "$TOKEN" | shasum -a 256`) equals the stored hash. Revoke it and confirm `revoked_at` is set.

---

### Task 14 — MCP endpoint on tokens, with the actor threaded to tools

**Rewrite** `src/lib/mcp/auth.ts`; **edit** `src/lib/mcp/protocol.ts`, `src/app/api/mcp/route.ts`, `src/lib/mcp/protocol.test.ts`; **delete** the `MCP_API_KEY` env everywhere.

1. `src/lib/mcp/auth.ts` — drop `configuredApiKey`, `API_KEY_ENV` and the constant-time compare of a shared secret. Keep the shape the route already uses:

```ts
export const API_KEY_HEADER = 'x-api-key';

export type McpAuthResult =
  | { ok: true; actor: Actor }
  | { ok: false; status: number; message: string; challenge?: boolean };

/** x-api-key first, Bearer second — a reverse proxy's basic auth can then ride
 *  along in Authorization without either credential overwriting the other. */
export async function authenticateMcp(request: Request): Promise<McpAuthResult>;
```

Behaviour: no credential → 401 `challenge: true`, message naming the header and pointing at `/agent-val/account/tokens`; unknown/revoked/expired token → 401 `Invalid or revoked API token.`; a valid session cookie is also accepted (so the endpoint can be poked from a signed-in browser). There is no "not configured" 503 any more — the endpoint is always on, and *tokens* are the gate.

2. `src/lib/mcp/protocol.ts`:
   - `export interface McpCallContext { actor: { userId: string; email: string; role: Role } }`
   - `McpToolSpec.handler` becomes `(args: ToolArgs, ctx: McpCallContext) => Promise<unknown>` (existing handlers ignore the second argument — no edits needed in `tools-authoring.ts` / `tools-runs.ts` beyond the type).
   - `handleMcpMessage(payload, registry, ctx: McpCallContext)`.
   - In `tools/call`, **before** invoking the handler:
     ```ts
     if (!spec.readOnly && !canWrite(ctx.actor.role)) {
       return result(id, textContent(
         { error: `The token's account is read-only; "${spec.name}" writes.` }, true));
     }
     ```
     It is `isError` content rather than a JSON-RPC error for the same reason a tool failure is: the calling model reads the message and stops trying.
   - `initialize`'s `instructions` gain one sentence naming the authenticated account, so an agent's transcript records who it acted as.

3. `src/app/api/mcp/route.ts`: replace `checkApiKey(request.headers, configuredApiKey())` with `await authenticateMcp(request)` in both `POST` and `GET`; pass `{ actor }` into `handleMcpMessage`; update `unauthorized()`'s `hint` to *"Create a token under /agent-val/account/tokens and send it as the x-api-key header."*

4. `src/lib/mcp/protocol.test.ts`: delete the four `checkApiKey` cases (the shared-secret contract is gone); add dispatch cases with a stub context — a `viewer` actor calling a write tool gets `isError` content and the handler is **not** invoked; a `viewer` calling a `readOnly` tool succeeds; a `member` calling a write tool succeeds. Keep every existing dispatch/error-mapping case, adding the context argument.

5. Grep the repo for `MCP_API_KEY` and remove every hit (`docker-compose.yml`, `README.md`, `CLAUDE.md`, `.env.example` if it slipped in, `Dockerfile`).

**Verify:**
```bash
npx vitest run src/lib/mcp                       # all green
grep -rn "MCP_API_KEY" --exclude-dir=node_modules --exclude-dir=.git . | wc -l   # -> 0
TOKEN=amv_…   # from Task 13
curl -s -X POST http://localhost:3000/agent-val/api/mcp -H "x-api-key: $TOKEN" \
  -H 'content-type: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | head -c 200
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://localhost:3000/agent-val/api/mcp \
  -H 'x-api-key: amv_bogus' -H 'content-type: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"ping"}'   # -> 401
```
Then, with a **viewer's** token, `tools/call` `create_prompt_group` returns `isError: true` and `psql -c 'select count(*) from prompt_groups'` is unchanged; `get_run` with the same token succeeds.

---

### Task 15 — Admin user management

**Create** `src/actions/users.ts`, `src/app/admin/users/page.tsx`, `src/components/auth/user-table.tsx`, `src/components/auth/create-user-form.tsx`.

`src/actions/users.ts` (`'use server'`), every action opening with `const admin = await requireAdmin();`:

```ts
export async function createUser(formData: FormData): Promise<void>
export async function setUserRole(userId: string, role: Role): Promise<void>
export async function deleteUser(userId: string): Promise<void>
```

- `createUser`: read `name`, `email`, `password` (min 12), `role`. Call `auth.api.createUser({ body: { name, email, password }, headers: await headers() })`, then `db.update(users).set({ role: parseRole(role) }).where(eq(users.id, created.user.id))` — see decision 2 at the top for why the role is set in a second step. `revalidatePath('/admin/users')`.
- `setUserRole`: refuse `userId === admin.userId` ("You cannot change your own role") **and** refuse demoting the last admin (`select count(*) from "user" where role='admin'` must stay ≥ 1). Both are `throw new AuthError(403, …)`.
- `deleteUser`: refuse self-deletion and last-admin deletion with the same checks; then `auth.api.removeUser({ body: { userId }, headers })` (falls back to `db.delete(users).where(eq(users.id, userId))`, which cascades sessions/accounts/api_tokens through the FKs in Task 2).

Page: admin-only (`await requireAdmin()` at the top — the proxy does not know roles), table of users (name, email, role select, provider = "password" or "oidc" derived from `accounts.providerId`, created, last session), plus the create form and a paragraph stating the role matrix from `ROLE_DESCRIPTIONS`.

**Verify:** as admin, create a `viewer` and a `member`; both appear in `psql -c 'select email, role from "user"'` with the right role. Change a role and confirm the change is visible to that user on their **next request** (no cookie cache). Try to demote yourself → error, role unchanged. Sign in as the member → `/admin/users` returns a 403 error page, not the table.

---

### Task 16 — Documentation

**Edit** `README.md`, `CLAUDE.md`; **create nothing new**.

- README: setup order now includes `BETTER_AUTH_SECRET`/`BETTER_AUTH_URL`; "the first account you create at `/agent-val/login` becomes the administrator"; an OIDC section listing the redirect URI `${BETTER_AUTH_URL}/api/auth/oauth2/callback/oidc` and one worked example each for Entra ID, Keycloak and Authentik issuer URLs; MCP section rewritten around per-user tokens (`/account/tokens`), `MCP_API_KEY` gone; a note that **Caddy basic auth for `/agent-val*` is now optional** and, if kept, that MCP clients must send basic auth in `Authorization` and the token in `x-api-key`.
- `CLAUDE.md`: add an "Auth" section under Architecture describing (a) roles and the `requireWriter`/`requireAdmin` guard API as the single enforcement point, (b) why the proxy is optimistic only, (c) first-account-is-admin and closed sign-up, (d) tokens hashed with SHA-256 and shown once, (e) that `handleMcpMessage` now takes an actor and refuses write tools for viewer tokens. Also update the "This app as an MCP server" section's auth paragraph, which currently documents `MCP_API_KEY`.

**Verify:** `grep -rn "MCP_API_KEY" README.md CLAUDE.md` → no hits; `grep -n "middleware.ts" docs README.md CLAUDE.md` → no stale references (this phase ships `proxy.ts`).

---

## Phase verification

Run all of these; every one must pass before Phase 4 is called done.

```bash
export PATH="$HOME/.nvm/versions/node/v22.23.1/bin:$PATH"
npm test            # full vitest suite — the pre-existing pure-logic tests stay green,
                    # plus policy.test.ts, tokens.test.ts and the reworked protocol.test.ts
npx tsc --noEmit    # clean
npm run lint        # clean
npm run build       # production build; catches RSC/route errors and the async layout
```

Phase-specific end-to-end checks (dev server on `http://localhost:3000/agent-val`, fresh database):

1. **Bootstrap** — with zero users, `/login` offers account creation; the created account has `role = 'admin'` in `psql`; a second sign-up POST returns 403.
2. **Gate** — signed out, `GET /agent-val/runs` 307s to `/agent-val/login?next=%2Fruns` (basePath present in `Location`).
3. **Viewer** — a viewer sees data everywhere, sees no mutating control, and a hand-fired server action fails without changing a row.
4. **Member** — can create prompts, create and execute a run, rate results; cannot open `/machines`' editors, `/admin/users`, or `POST /api/toolsets/1/discover` (403).
5. **Admin** — can do all of the above plus register a machine and a toolset, create/delete users, change roles; cannot demote or delete the last admin.
6. **OIDC** — against a local Keycloak (or Authentik) with `OIDC_ISSUER` set: the SSO button completes the round trip, auto-provisions the user as `member`, and `accounts.provider_id = 'oidc'`.
7. **Tokens** — a token created at `/account/tokens` authenticates `/api/mcp`; its SHA-256 matches `token_hash`; revoking it turns the same call into a 401; a viewer's token gets `isError` on any write tool.
8. **Mocks** — 404 in a production build without `ENABLE_MOCKS=true`, 200 in dev.
9. **Regression** — the snapshot invariant is untouched: an existing run's detail page renders identically before and after this phase (compare `psql -c 'select id, prompt_text, tools_snapshot from run_results limit 5'` and the rendered page).
