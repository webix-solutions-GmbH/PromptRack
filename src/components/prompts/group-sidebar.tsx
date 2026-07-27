'use client';

import { useState, useTransition } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import type { PromptGroup } from '@/db/schema';
import { createGroup, deleteGroup, updateGroup } from '@/actions/prompts';

const inputClass =
  'w-full rounded-md border border-zinc-300 bg-white px-3 py-2 text-sm text-zinc-900 placeholder:text-zinc-400 focus:border-zinc-500 focus:outline-none dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-50 dark:placeholder:text-zinc-500';
const labelClass = 'text-xs font-medium text-zinc-600 dark:text-zinc-400';

export function GroupSidebar({
  groups,
  selectedGroupId,
  counts,
}: {
  groups: PromptGroup[];
  selectedGroupId: number | null;
  counts: Record<number, number>;
}) {
  const router = useRouter();
  const [editingId, setEditingId] = useState<number | null>(null);
  const [pending, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);

  function handleRenameSubmit(id: number, event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formData = new FormData(event.currentTarget);
    setError(null);
    startTransition(async () => {
      try {
        await updateGroup(id, formData);
        setEditingId(null);
        router.refresh();
      } catch {
        setError('Failed to save group.');
      }
    });
  }

  function handleDelete(group: PromptGroup) {
    if (
      !window.confirm(`Delete group "${group.name}" and all its prompts? This cannot be undone.`)
    ) {
      return;
    }
    setError(null);
    startTransition(async () => {
      try {
        await deleteGroup(group.id);
        if (selectedGroupId === group.id) {
          router.push('/prompts');
        } else {
          router.refresh();
        }
      } catch {
        setError('Failed to delete group.');
      }
    });
  }

  return (
    <div className="flex flex-col gap-4">
      <h2 className="text-lg font-semibold text-zinc-900 dark:text-zinc-50">Groups</h2>

      <ul className="flex flex-col gap-2">
        {groups.length === 0 && (
          <li className="text-sm text-zinc-500 dark:text-zinc-400">No groups yet.</li>
        )}
        {groups.map((group) => {
          if (editingId === group.id) {
            return (
              <li key={group.id} className="rounded-md border border-zinc-200 p-3 dark:border-zinc-800">
                <form
                  onSubmit={(event) => handleRenameSubmit(group.id, event)}
                  className="flex flex-col gap-2"
                >
                  <input name="name" required defaultValue={group.name} className={inputClass} />
                  <textarea
                    name="description"
                    rows={2}
                    defaultValue={group.description ?? ''}
                    placeholder="description (optional)"
                    className={inputClass}
                  />
                  <div className="flex gap-2">
                    <button
                      type="submit"
                      disabled={pending}
                      className="rounded-md bg-zinc-900 px-3 py-1.5 text-sm font-medium text-zinc-50 transition-colors hover:bg-zinc-700 disabled:opacity-50 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-300"
                    >
                      Save
                    </button>
                    <button
                      type="button"
                      disabled={pending}
                      onClick={() => setEditingId(null)}
                      className="rounded-md border border-zinc-300 px-3 py-1.5 text-sm font-medium text-zinc-700 transition-colors hover:bg-zinc-100 disabled:opacity-50 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800"
                    >
                      Cancel
                    </button>
                  </div>
                </form>
              </li>
            );
          }

          const isSelected = group.id === selectedGroupId;
          return (
            <li
              key={group.id}
              className={`flex items-center justify-between gap-2 rounded-md px-3 py-2 transition-colors ${
                isSelected
                  ? 'bg-zinc-900 text-zinc-50 dark:bg-zinc-100 dark:text-zinc-900'
                  : 'text-zinc-700 hover:bg-zinc-100 dark:text-zinc-300 dark:hover:bg-zinc-800'
              }`}
            >
              <Link href={`/prompts?group=${group.id}`} className="flex-1 truncate text-sm font-medium">
                {group.name}{' '}
                <span className="opacity-70">({counts[group.id] ?? 0})</span>
              </Link>
              <div className="flex items-center gap-2 text-xs">
                <button
                  type="button"
                  onClick={() => setEditingId(group.id)}
                  className="underline opacity-80 hover:opacity-100"
                >
                  edit
                </button>
                <button
                  type="button"
                  onClick={() => handleDelete(group)}
                  className={`underline opacity-80 hover:opacity-100 ${
                    isSelected ? '' : 'text-red-600 dark:text-red-400'
                  }`}
                >
                  delete
                </button>
              </div>
            </li>
          );
        })}
      </ul>

      {error && <p className="text-sm text-red-600 dark:text-red-400">{error}</p>}

      <form
        action={createGroup}
        className="flex flex-col gap-2 border-t border-zinc-200 pt-4 dark:border-zinc-800"
      >
        <label className={labelClass} htmlFor="new-group-name">
          New group
        </label>
        <input id="new-group-name" name="name" required placeholder="Group name" className={inputClass} />
        <textarea name="description" rows={2} placeholder="description (optional)" className={inputClass} />
        <button
          type="submit"
          className="rounded-md bg-zinc-900 px-3 py-2 text-sm font-medium text-zinc-50 transition-colors hover:bg-zinc-700 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-300"
        >
          Create group
        </button>
      </form>
    </div>
  );
}
