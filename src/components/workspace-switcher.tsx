'use client';

import { useTransition } from 'react';
import { useRouter } from 'next/navigation';
import type { CustomerOption } from '@/db/scope';
import { switchCustomer } from '@/actions/customers';

/**
 * Which customer workspace the app is showing, above the nav.
 *
 * A `<select>` rather than a popover: this is a handful of entries, and the
 * native control is the one every browser already makes keyboard- and
 * screen-reader-accessible. Switching goes through a server action because the
 * active workspace lives on the user row — a cookie could not be written from an
 * RSC render, and could be forged from the client.
 */
export function WorkspaceSwitcher({
  customers,
  activeId,
}: {
  customers: CustomerOption[];
  activeId: number;
}) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();

  // Archived workspaces stay hidden unless the user is standing in one, which
  // happens when someone archives the workspace they were working in.
  const visible = customers.filter((customer) => !customer.archived || customer.id === activeId);

  return (
    <div className="flex flex-col gap-1 px-4 pb-2">
      <label
        htmlFor="workspace-switcher"
        className="text-[11px] font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400"
      >
        Workspace
      </label>
      <select
        id="workspace-switcher"
        value={activeId}
        disabled={pending}
        onChange={(event) => {
          const next = Number(event.target.value);
          if (next === activeId) return;
          startTransition(async () => {
            await switchCustomer(next);
            router.refresh();
          });
        }}
        className="w-full rounded-md border border-zinc-300 bg-white px-2 py-1.5 text-sm text-zinc-900 focus:border-zinc-500 focus:outline-none disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-50"
      >
        {visible.map((customer) => (
          <option key={customer.id} value={customer.id}>
            {customer.name}
            {customer.archived ? ' (archived)' : ''}
          </option>
        ))}
      </select>
    </div>
  );
}
