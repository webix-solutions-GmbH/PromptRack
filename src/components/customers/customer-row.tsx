'use client';

import { useState, useTransition } from 'react';
import { useRouter } from 'next/navigation';
import type { Customer } from '@/db/schema';
import type { CustomerContentCounts } from '@/db/repo/customers';
import { deleteCustomer, setCustomerArchived, updateCustomer } from '@/actions/customers';

const inputClass =
  'w-full rounded-md border border-zinc-300 bg-white px-3 py-2 text-sm text-zinc-900 placeholder:text-zinc-400 focus:border-zinc-500 focus:outline-none dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-50 dark:placeholder:text-zinc-500';
const labelClass = 'text-xs font-medium text-zinc-600 dark:text-zinc-400';
const secondaryButton =
  'rounded-md border border-zinc-300 px-3 py-1.5 text-sm font-medium text-zinc-700 transition-colors hover:bg-zinc-100 disabled:opacity-50 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800';

function describeContents(counts: CustomerContentCounts): string {
  const parts = [
    `${counts.machines} machine${counts.machines === 1 ? '' : 's'}`,
    `${counts.systemPrompts} system prompt${counts.systemPrompts === 1 ? '' : 's'}`,
    `${counts.toolsets} toolset${counts.toolsets === 1 ? '' : 's'}`,
    `${counts.promptGroups} prompt group${counts.promptGroups === 1 ? '' : 's'}`,
    `${counts.runs} run${counts.runs === 1 ? '' : 's'}`,
  ];
  return parts.join(' · ');
}

export function CustomerRow({
  customer,
  counts,
  isDefault,
  isActive,
  canWrite,
  canAdminister,
}: {
  customer: Customer;
  counts: CustomerContentCounts;
  /** The oldest workspace — the one the migration put existing data in. */
  isDefault: boolean;
  isActive: boolean;
  canWrite: boolean;
  canAdminister: boolean;
}) {
  const router = useRouter();
  const [editing, setEditing] = useState(false);
  const [pending, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);

  function run(action: () => Promise<void>, fallback: string) {
    setError(null);
    startTransition(async () => {
      try {
        await action();
        router.refresh();
      } catch (err) {
        // A server action's message survives in development and is replaced by a
        // generic one in production, so both are handled.
        setError(err instanceof Error && err.message ? err.message : fallback);
      }
    });
  }

  if (editing) {
    return (
      <li className="rounded-lg border border-zinc-200 p-4 dark:border-zinc-800">
        <form
          onSubmit={(event) => {
            event.preventDefault();
            const formData = new FormData(event.currentTarget);
            run(async () => {
              await updateCustomer(customer.id, formData);
              setEditing(false);
            }, 'Failed to save workspace.');
          }}
          className="flex flex-col gap-3"
        >
          <div className="flex flex-col gap-1">
            <label className={labelClass} htmlFor={`name-${customer.id}`}>
              Name *
            </label>
            <input
              id={`name-${customer.id}`}
              name="name"
              required
              defaultValue={customer.name}
              className={inputClass}
            />
          </div>
          <div className="flex flex-col gap-1">
            <label className={labelClass} htmlFor={`description-${customer.id}`}>
              Description
            </label>
            <input
              id={`description-${customer.id}`}
              name="description"
              defaultValue={customer.description ?? ''}
              className={inputClass}
            />
          </div>
          {error && <p className="text-sm text-red-600 dark:text-red-400">{error}</p>}
          <div className="flex items-center gap-2">
            <button
              type="submit"
              disabled={pending}
              className="rounded-md bg-zinc-900 px-3 py-2 text-sm font-medium text-zinc-50 transition-colors hover:bg-zinc-700 disabled:opacity-50 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-300"
            >
              {pending ? 'Saving…' : 'Save'}
            </button>
            <button
              type="button"
              disabled={pending}
              onClick={() => {
                setEditing(false);
                setError(null);
              }}
              className={secondaryButton}
            >
              Cancel
            </button>
          </div>
        </form>
      </li>
    );
  }

  return (
    <li className="flex flex-col gap-2 rounded-lg border border-zinc-200 p-4 dark:border-zinc-800">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex flex-col gap-1">
          <span className="flex flex-wrap items-center gap-2">
            <span className="font-medium text-zinc-900 dark:text-zinc-50">{customer.name}</span>
            {isActive && (
              <span className="rounded-full bg-zinc-900 px-2 py-0.5 text-[11px] font-medium text-zinc-50 dark:bg-zinc-100 dark:text-zinc-900">
                active
              </span>
            )}
            {isDefault && (
              <span className="rounded-full border border-zinc-300 px-2 py-0.5 text-[11px] font-medium text-zinc-600 dark:border-zinc-700 dark:text-zinc-400">
                default
              </span>
            )}
            {customer.archivedAt !== null && (
              <span className="rounded-full bg-amber-100 px-2 py-0.5 text-[11px] font-medium text-amber-800 dark:bg-amber-950 dark:text-amber-300">
                archived
              </span>
            )}
          </span>
          {customer.description && (
            <span className="text-sm text-zinc-600 dark:text-zinc-400">{customer.description}</span>
          )}
          <span className="text-xs text-zinc-500 dark:text-zinc-400">
            {describeContents(counts)}
          </span>
        </div>
        <div className="flex items-center gap-2">
          {canWrite && (
            <>
              <button type="button" onClick={() => setEditing(true)} className={secondaryButton}>
                Rename
              </button>
              <button
                type="button"
                disabled={pending}
                onClick={() =>
                  run(
                    () => setCustomerArchived(customer.id, customer.archivedAt === null),
                    'Failed to change the workspace.',
                  )
                }
                className={secondaryButton}
              >
                {customer.archivedAt === null ? 'Archive' : 'Unarchive'}
              </button>
            </>
          )}
          {canAdminister && (
            <button
              type="button"
              disabled={pending}
              onClick={() => {
                if (!window.confirm(`Delete workspace "${customer.name}"? This cannot be undone.`)) {
                  return;
                }
                run(() => deleteCustomer(customer.id), 'Failed to delete the workspace.');
              }}
              className="rounded-md border border-red-300 px-3 py-1.5 text-sm font-medium text-red-600 transition-colors hover:bg-red-50 disabled:opacity-50 dark:border-red-900 dark:text-red-400 dark:hover:bg-red-950"
            >
              Delete
            </button>
          )}
        </div>
      </div>
      {error && <p className="text-sm text-red-600 dark:text-red-400">{error}</p>}
    </li>
  );
}
