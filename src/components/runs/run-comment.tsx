'use client';

import { useState, useTransition } from 'react';
import { updateRunComment } from '@/actions/runs';

export function RunComment({
  runId,
  comment,
}: {
  runId: number;
  comment: string | null;
}) {
  const [value, setValue] = useState(comment ?? '');
  const [saved, setSaved] = useState(comment ?? '');
  const [editing, setEditing] = useState(false);
  const [pending, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);

  function save() {
    setError(null);
    startTransition(async () => {
      try {
        await updateRunComment(runId, value);
        setSaved(value.trim());
        setEditing(false);
      } catch {
        setError('Failed to save the comment.');
      }
    });
  }

  if (!editing) {
    return (
      <div className="flex flex-wrap items-start gap-2">
        <p className="max-w-prose whitespace-pre-wrap text-sm text-zinc-700 dark:text-zinc-300">
          {saved.length > 0 ? (
            saved
          ) : (
            <span className="text-zinc-400 dark:text-zinc-600">No comment.</span>
          )}
        </p>
        <button
          type="button"
          onClick={() => {
            setValue(saved);
            setEditing(true);
          }}
          className="text-xs font-medium text-zinc-500 underline-offset-2 hover:underline dark:text-zinc-400"
        >
          Edit
        </button>
      </div>
    );
  }

  return (
    <div className="flex max-w-xl flex-col gap-2">
      <textarea
        rows={3}
        value={value}
        onChange={(event) => setValue(event.target.value)}
        className="w-full rounded-md border border-zinc-300 bg-white px-3 py-2 text-sm text-zinc-900 focus:border-zinc-500 focus:outline-none dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-50"
      />
      {error && <p className="text-sm text-red-600 dark:text-red-400">{error}</p>}
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={save}
          disabled={pending}
          className="rounded-md bg-zinc-900 px-3 py-1.5 text-xs font-medium text-zinc-50 transition-colors hover:bg-zinc-700 disabled:opacity-50 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-300"
        >
          {pending ? 'Saving…' : 'Save'}
        </button>
        <button
          type="button"
          onClick={() => {
            setValue(saved);
            setEditing(false);
            setError(null);
          }}
          disabled={pending}
          className="rounded-md border border-zinc-300 px-3 py-1.5 text-xs font-medium text-zinc-700 transition-colors hover:bg-zinc-100 disabled:opacity-50 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800"
        >
          Cancel
        </button>
      </div>
    </div>
  );
}
