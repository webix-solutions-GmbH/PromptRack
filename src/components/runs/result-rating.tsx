'use client';

import { useState, useTransition } from 'react';
import { rateResult, updateResultNote } from '@/actions/runs';

const thumbBase =
  'inline-flex h-7 w-7 items-center justify-center rounded-md border text-sm transition-colors disabled:opacity-50';

const thumbUpClass = {
  active:
    'border-emerald-600 bg-emerald-600 text-white dark:border-emerald-500 dark:bg-emerald-500',
  inactive:
    'border-zinc-300 text-zinc-500 hover:border-emerald-400 hover:text-emerald-600 dark:border-zinc-700 dark:text-zinc-400 dark:hover:border-emerald-500 dark:hover:text-emerald-400',
};

const thumbDownClass = {
  active: 'border-red-600 bg-red-600 text-white dark:border-red-500 dark:bg-red-500',
  inactive:
    'border-zinc-300 text-zinc-500 hover:border-red-400 hover:text-red-600 dark:border-zinc-700 dark:text-zinc-400 dark:hover:border-red-500 dark:hover:text-red-400',
};

export function ResultRating({
  resultId,
  rating,
  ratingNote,
  onChange,
}: {
  resultId: number;
  rating: 'good' | 'bad' | null;
  ratingNote: string | null;
  /** Lets the parent keep its own result list (and live counts) in sync. */
  onChange: (patch: { rating?: 'good' | 'bad' | null; ratingNote?: string | null }) => void;
}) {
  const [noteValue, setNoteValue] = useState(ratingNote ?? '');
  const [showNote, setShowNote] = useState((ratingNote ?? '').length > 0);
  const [pending, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);

  function setRating(next: 'good' | 'bad') {
    const value = rating === next ? null : next;
    setError(null);
    onChange({ rating: value });
    startTransition(async () => {
      try {
        await rateResult(resultId, value);
      } catch {
        setError('Failed to save rating.');
      }
    });
  }

  function saveNote() {
    const trimmed = noteValue.trim();
    setError(null);
    onChange({ ratingNote: trimmed.length > 0 ? trimmed : null });
    startTransition(async () => {
      try {
        await updateResultNote(resultId, trimmed);
      } catch {
        setError('Failed to save note.');
      }
    });
  }

  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-center gap-2">
        <button
          type="button"
          aria-label="Good result"
          aria-pressed={rating === 'good'}
          disabled={pending}
          onClick={() => setRating('good')}
          className={`${thumbBase} ${rating === 'good' ? thumbUpClass.active : thumbUpClass.inactive}`}
        >
          👍
        </button>
        <button
          type="button"
          aria-label="Bad result"
          aria-pressed={rating === 'bad'}
          disabled={pending}
          onClick={() => setRating('bad')}
          className={`${thumbBase} ${rating === 'bad' ? thumbDownClass.active : thumbDownClass.inactive}`}
        >
          👎
        </button>
        <button
          type="button"
          onClick={() => setShowNote((current) => !current)}
          className="text-xs font-medium text-zinc-500 underline-offset-2 hover:underline dark:text-zinc-400"
        >
          {showNote ? 'Hide note' : ratingNote ? 'Edit note' : 'Add note'}
        </button>
        {error && <span className="text-xs text-red-600 dark:text-red-400">{error}</span>}
      </div>
      {showNote && (
        <input
          type="text"
          value={noteValue}
          onChange={(event) => setNoteValue(event.target.value)}
          onBlur={saveNote}
          placeholder="Optional note about this rating…"
          className="w-full max-w-sm rounded-md border border-zinc-300 bg-white px-2 py-1 text-xs text-zinc-900 focus:border-zinc-500 focus:outline-none dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-50"
        />
      )}
    </div>
  );
}
