/**
 * Reads and writes on the `user` table for the admin screens.
 *
 * Like `@/lib/auth` and `@/lib/auth/tokens`, this touches `db` directly: the
 * auth tables are global infrastructure rather than workspace data.
 */

import { count, desc, eq, max, sql } from 'drizzle-orm';
import { db } from '@/db';
import { accounts, sessions, users } from '@/db/schema';
import type { Role } from '@/lib/auth/policy';

export interface UserRow {
  id: string;
  name: string;
  email: string;
  role: Role;
  /** 'password', an OIDC provider id, or a comma-joined list of both. */
  providers: string | null;
  createdAt: Date;
  lastSessionAt: Date | null;
}

export async function listUsers(): Promise<UserRow[]> {
  const rows = await db
    .select({
      id: users.id,
      name: users.name,
      email: users.email,
      role: users.role,
      // `credential` is better-auth's provider id for an email/password
      // account; it is renamed here because the UI's word for it is "password".
      providers: sql<string | null>`string_agg(distinct case when ${accounts.providerId} = 'credential' then 'password' else ${accounts.providerId} end, ', ')`,
      createdAt: users.createdAt,
      lastSessionAt: max(sessions.createdAt),
    })
    .from(users)
    .leftJoin(accounts, eq(accounts.userId, users.id))
    .leftJoin(sessions, eq(sessions.userId, users.id))
    .groupBy(users.id)
    .orderBy(desc(users.createdAt));

  return rows.map((row) => ({ ...row, role: row.role as Role }));
}

export async function countAdmins(): Promise<number> {
  const [row] = await db.select({ value: count() }).from(users).where(eq(users.role, 'admin'));
  return row?.value ?? 0;
}

export async function getUserRole(userId: string): Promise<Role | null> {
  const [row] = await db.select({ role: users.role }).from(users).where(eq(users.id, userId));
  return (row?.role as Role) ?? null;
}

export async function setUserRoleRow(userId: string, role: Role): Promise<void> {
  await db.update(users).set({ role, updatedAt: new Date() }).where(eq(users.id, userId));
}

/**
 * The workspace a user is currently in, or null when they have never switched
 * (or the one they were in was archived away under them — the column is
 * `ON DELETE SET NULL`).
 */
export async function getActiveCustomerId(userId: string): Promise<number | null> {
  const [row] = await db
    .select({ activeCustomerId: users.activeCustomerId })
    .from(users)
    .where(eq(users.id, userId));
  return row?.activeCustomerId ?? null;
}

export async function setActiveCustomerId(userId: string, customerId: number): Promise<void> {
  await db
    .update(users)
    .set({ activeCustomerId: customerId, updatedAt: new Date() })
    .where(eq(users.id, userId));
}

/** Cascades to sessions, accounts and api_tokens through their foreign keys. */
export async function deleteUserRow(userId: string): Promise<void> {
  await db.delete(users).where(eq(users.id, userId));
}
