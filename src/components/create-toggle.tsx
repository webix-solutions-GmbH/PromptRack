'use client';

import { useState } from 'react';

/**
 * Page header with a "New X" button top right that reveals a creation form
 * below the header. The form is passed as (server-rendered) children, so pages
 * keep their plain server-action forms; only the open/closed state lives on
 * the client. `header` is the page's title block (h1 + description).
 */
export function CreateToggle({
  label,
  title,
  header,
  className,
  children,
}: {
  label: string;
  title: string;
  header: React.ReactNode;
  className?: string;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(false);

  return (
    <>
      <div className="flex flex-wrap items-start justify-between gap-4">
        {header}
        {!open && (
          <button
            type="button"
            onClick={() => setOpen(true)}
            className="rounded-md bg-zinc-900 px-4 py-2 text-sm font-medium text-zinc-50 transition-colors hover:bg-zinc-700 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-300"
          >
            {label}
          </button>
        )}
      </div>

      {open && (
        <section
          className={`flex flex-col gap-4 rounded-lg border border-zinc-200 p-6 dark:border-zinc-800 ${className ?? ''}`}
        >
          <div className="flex items-center justify-between gap-4">
            <h2 className="text-lg font-semibold text-zinc-900 dark:text-zinc-50">{title}</h2>
            <button
              type="button"
              onClick={() => setOpen(false)}
              className="text-xs font-medium text-zinc-500 underline-offset-2 hover:underline dark:text-zinc-400"
            >
              Cancel
            </button>
          </div>
          {children}
        </section>
      )}
    </>
  );
}
