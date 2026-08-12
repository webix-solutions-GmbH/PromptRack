/**
 * Per-user API tokens for /api/mcp.
 *
 * Like `@/lib/auth`, this reads the auth tables directly: they are global
 * infrastructure rather than workspace data, so they do not go through a scoped
 * repository.
 */

import { createHash, randomBytes, randomUUID } from 'node:crypto';
import { and, desc, eq, gt, isNull, or } from 'drizzle-orm';
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
  const now = new Date();
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
    .where(
      and(
        eq(apiTokens.tokenHash, hashToken(raw)),
        isNull(apiTokens.revokedAt),
        or(isNull(apiTokens.expiresAt), gt(apiTokens.expiresAt, now)),
      ),
    );

  if (!row || row.banned) return null;

  await db.update(apiTokens).set({ lastUsedAt: now }).where(eq(apiTokens.id, row.tokenId));

  return {
    tokenId: row.tokenId,
    userId: row.userId,
    email: row.email,
    name: row.name,
    role: row.role,
  };
}

export interface ApiTokenRow {
  id: string;
  name: string;
  prefix: string;
  createdAt: Date;
  lastUsedAt: Date | null;
  expiresAt: Date | null;
  revokedAt: Date | null;
}

/** One user's tokens, newest first — revoked ones included, as history. */
export async function listApiTokens(userId: string): Promise<ApiTokenRow[]> {
  return db
    .select({
      id: apiTokens.id,
      name: apiTokens.name,
      prefix: apiTokens.prefix,
      createdAt: apiTokens.createdAt,
      lastUsedAt: apiTokens.lastUsedAt,
      expiresAt: apiTokens.expiresAt,
      revokedAt: apiTokens.revokedAt,
    })
    .from(apiTokens)
    .where(eq(apiTokens.userId, userId))
    .orderBy(desc(apiTokens.createdAt));
}

export async function insertApiToken(values: {
  id: string;
  userId: string;
  name: string;
  tokenHash: string;
  prefix: string;
  expiresAt: Date | null;
}): Promise<void> {
  await db.insert(apiTokens).values(values);
}

/**
 * Revokes one token. The `userId` predicate is the ownership check, so one user
 * can never revoke another's token.
 */
export async function revokeApiTokenRow(tokenId: string, userId: string): Promise<void> {
  await db
    .update(apiTokens)
    .set({ revokedAt: new Date() })
    .where(and(eq(apiTokens.id, tokenId), eq(apiTokens.userId, userId)));
}
