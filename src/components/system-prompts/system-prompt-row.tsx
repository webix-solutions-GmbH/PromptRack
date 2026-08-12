'use client';

import { useState, useTransition } from 'react';
import { useRouter } from 'next/navigation';
import type { SystemPrompt } from '@/db/schema';
import { deleteSystemPrompt, updateSystemPrompt } from '@/actions/system-prompts';
import { formatDateTime } from '@/lib/format';

const inputClass =
  'w-full rounded-md border border-zinc-300 bg-white px-3 py-2 text-sm text-zinc-900 placeholder:text-zinc-400 focus:border-zinc-500 focus:outline-none dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-50 dark:placeholder:text-zinc-500';
const labelClass = 'text-xs font-medium text-zinc-600 dark:text-zinc-400';

export function SystemPromptRow({
  prompt,
  canWrite,
}: {
  prompt: SystemPrompt;
  canWrite: boolean;
}) {
  const router = useRouter();
  const [editing, setEditing] = useState(false);
  const [pending, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);

  function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formData = new FormData(event.currentTarget);
    setError(null);
    startTransition(async () => {
      try {
        await updateSystemPrompt(prompt.id, formData);
        setEditing(false);
        router.refresh();
      } catch {
        setError('Failed to save system prompt.');
      }
    });
  }

  function handleDelete() {
    if (!window.confirm(`Delete system prompt "${prompt.name}"? This cannot be undone.`)) {
      return;
    }
    setError(null);
    startTransition(async () => {
      try {
        await deleteSystemPrompt(prompt.id);
        router.refresh();
      } catch {
        setError('Failed to delete system prompt.');
      }
    });
  }

  if (editing) {
    return (
      <li className="rounded-lg border border-zinc-200 p-4 dark:border-zinc-800">
        <form onSubmit={handleSubmit} className="flex flex-col gap-3">
          <div className="flex flex-col gap-1">
            <label className={labelClass} htmlFor={`name-${prompt.id}`}>
              Name *
            </label>
            <input
              id={`name-${prompt.id}`}
              name="name"
              required
              defaultValue={prompt.name}
              className={inputClass}
            />
          </div>
          <div className="flex flex-col gap-1">
            <label className={labelClass} htmlFor={`content-${prompt.id}`}>
              Content *
            </label>
            <textarea
              id={`content-${prompt.id}`}
              name="content"
              required
              rows={6}
              defaultValue={prompt.content}
              className={`${inputClass} font-mono`}
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
              className="rounded-md border border-zinc-300 px-3 py-2 text-sm font-medium text-zinc-700 transition-colors hover:bg-zinc-100 disabled:opacity-50 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800"
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
          <span className="font-medium text-zinc-900 dark:text-zinc-50">{prompt.name}</span>
          <span className="text-xs text-zinc-500 dark:text-zinc-400">
            Updated {formatDateTime(prompt.updatedAt)}
          </span>
        </div>
        {canWrite && (
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => setEditing(true)}
              className="rounded-md border border-zinc-300 px-3 py-1.5 text-sm font-medium text-zinc-700 transition-colors hover:bg-zinc-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800"
            >
              Edit
            </button>
            <button
              type="button"
              onClick={handleDelete}
              disabled={pending}
              className="rounded-md border border-red-300 px-3 py-1.5 text-sm font-medium text-red-600 transition-colors hover:bg-red-50 disabled:opacity-50 dark:border-red-900 dark:text-red-400 dark:hover:bg-red-950"
            >
              Delete
            </button>
          </div>
        )}
      </div>
      <pre className="max-h-24 overflow-hidden whitespace-pre-wrap rounded-md bg-zinc-50 p-3 font-mono text-xs text-zinc-600 dark:bg-zinc-900 dark:text-zinc-400">
        {prompt.content}
      </pre>
      {error && <p className="text-sm text-red-600 dark:text-red-400">{error}</p>}
    </li>
  );
}
