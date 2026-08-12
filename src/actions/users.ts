'use server';

import { headers } from 'next/headers';
import { revalidatePath } from 'next/cache';
import { auth } from '@/lib/auth';
import { AuthError, requireAdmin } from '@/lib/auth/guards';
import { parseRole, ROLES, type Role } from '@/lib/auth/policy';
import {
  countAdmins,
  deleteUserRow,
  getUserRole,
  setUserRoleRow,
} from '@/lib/auth/users';
import { requiredString } from '@/lib/form-data';

const MIN_PASSWORD_LENGTH = 12;

/**
 * Creates a local password account.
 *
 * The role is set in a second step rather than passed to `createUser`: our own
 * `databaseHooks.user.create.before` stamps a role on every insert, so whatever
 * the plugin was told would be overwritten anyway.
 */
export async function createUser(formData: FormData): Promise<void> {
  await requireAdmin();

  const name = requiredString(formData, 'name');
  const email = requiredString(formData, 'email');
  const password = requiredString(formData, 'password');
  if (password.length < MIN_PASSWORD_LENGTH) {
    throw new Error(`The password must be at least ${MIN_PASSWORD_LENGTH} characters.`);
  }

  const role = parseRole(formData.get('role'));

  const created = await auth.api.createUser({
    body: { name, email, password },
    headers: await headers(),
  });

  await setUserRoleRow(created.user.id, role);
  revalidatePath('/admin/users');
}

export async function setUserRole(userId: string, role: Role): Promise<void> {
  const admin = await requireAdmin();
  await assertNotLastAdmin(admin.userId, userId, 'change your own role');

  if (!(ROLES as readonly string[]).includes(role)) {
    throw new Error(`Unknown role "${role}".`);
  }

  await setUserRoleRow(userId, role);
  revalidatePath('/admin/users');
}

export async function deleteUser(userId: string): Promise<void> {
  const admin = await requireAdmin();
  await assertNotLastAdmin(admin.userId, userId, 'delete your own account');

  await deleteUserRow(userId);
  revalidatePath('/admin/users');
}

/**
 * Refuses the two ways an admin can lock everybody out: acting on themselves,
 * and removing the last admin there is.
 */
async function assertNotLastAdmin(
  actingUserId: string,
  targetUserId: string,
  selfAction: string,
): Promise<void> {
  if (actingUserId === targetUserId) {
    throw new AuthError(403, `You cannot ${selfAction}.`);
  }
  if ((await getUserRole(targetUserId)) === 'admin' && (await countAdmins()) <= 1) {
    throw new AuthError(403, 'This is the last administrator account.');
  }
}
