import { onPage, requireAdmin } from '@/lib/auth/guards';
import { ROLES, ROLE_DESCRIPTIONS, ROLE_LABELS } from '@/lib/auth/policy';
import { listUsers } from '@/lib/auth/users';
import { CreateUserForm } from '@/components/auth/create-user-form';
import { UserTable, type UserRowView } from '@/components/auth/user-table';

export const dynamic = 'force-dynamic';

export default async function AdminUsersPage() {
  // The proxy is an optimistic cookie check and knows nothing about roles, so
  // this is where a non-admin is actually turned away.
  const admin = await onPage(requireAdmin);
  const rows = await listUsers();

  const users: UserRowView[] = rows.map((user) => ({
    id: user.id,
    name: user.name,
    email: user.email,
    role: user.role,
    providers: user.providers,
    createdAt: user.createdAt.getTime(),
    lastSessionAt: user.lastSessionAt?.getTime() ?? null,
  }));

  return (
    <div className="flex flex-1 flex-col gap-8 p-8">
      <div className="flex flex-col gap-2">
        <h1 className="text-2xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
          Users
        </h1>
        <ul className="max-w-prose text-sm text-zinc-600 dark:text-zinc-400">
          {ROLES.map((role) => (
            <li key={role}>
              <span className="font-medium text-zinc-800 dark:text-zinc-200">
                {ROLE_LABELS[role]}
              </span>{' '}
              — {ROLE_DESCRIPTIONS[role]}
            </li>
          ))}
        </ul>
      </div>

      <CreateUserForm />

      <UserTable users={users} currentUserId={admin.userId} />
    </div>
  );
}
