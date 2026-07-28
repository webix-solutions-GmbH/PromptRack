'use client';

import { useState, useTransition } from 'react';

const inputClass =
  'w-full rounded-md border border-zinc-300 bg-white px-3 py-2 text-sm text-zinc-900 placeholder:text-zinc-400 focus:border-zinc-500 focus:outline-none dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-50 dark:placeholder:text-zinc-500';
const labelClass = 'text-xs font-medium text-zinc-600 dark:text-zinc-400';

export type ToolsetKind = 'manual' | 'mcp';

export interface ToolsetFormValues {
  name: string;
  description: string;
  kind: ToolsetKind;
  mcpUrl: string;
  mcpHeaders: string;
}

/**
 * Create/edit form for a toolset. The MCP endpoint fields only exist for `mcp`
 * toolsets, so the kind radio drives what is rendered.
 */
export function ToolsetForm({
  initialValues,
  submitLabel,
  onSubmit,
  onCancel,
}: {
  initialValues: ToolsetFormValues;
  submitLabel: string;
  onSubmit: (formData: FormData) => Promise<void>;
  onCancel?: () => void;
}) {
  const [kind, setKind] = useState<ToolsetKind>(initialValues.kind);
  const [pending, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);

  function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formData = new FormData(event.currentTarget);
    setError(null);
    startTransition(async () => {
      try {
        await onSubmit(formData);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to save toolset.');
      }
    });
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-4">
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <div className="flex flex-col gap-1">
          <label className={labelClass} htmlFor="name">
            Name *
          </label>
          <input
            id="name"
            name="name"
            required
            defaultValue={initialValues.name}
            placeholder="Odoo ERP"
            className={inputClass}
          />
        </div>
        <div className="flex flex-col gap-1">
          <label className={labelClass} htmlFor="description">
            Description
          </label>
          <input
            id="description"
            name="description"
            defaultValue={initialValues.description}
            placeholder="Read-only product and partner lookups"
            className={inputClass}
          />
        </div>
      </div>

      <div className="flex flex-col gap-2">
        <span className={labelClass}>Kind</span>
        <div className="flex flex-wrap gap-4 text-sm text-zinc-700 dark:text-zinc-300">
          <label className="flex items-center gap-2">
            <input
              type="radio"
              name="kind"
              value="manual"
              checked={kind === 'manual'}
              onChange={() => setKind('manual')}
            />
            Manual — tools defined here, answering with canned responses
          </label>
          <label className="flex items-center gap-2">
            <input
              type="radio"
              name="kind"
              value="mcp"
              checked={kind === 'mcp'}
              onChange={() => setKind('mcp')}
            />
            MCP server — tools discovered and executed over HTTP
          </label>
        </div>
      </div>

      {kind === 'mcp' && (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <div className="flex flex-col gap-1">
            <label className={labelClass} htmlFor="mcpUrl">
              MCP server URL *
            </label>
            <input
              id="mcpUrl"
              name="mcpUrl"
              defaultValue={initialValues.mcpUrl}
              placeholder="http://odoo-mcp:8000/mcp"
              className={`${inputClass} font-mono`}
            />
            <p className="text-xs text-zinc-500 dark:text-zinc-400">
              Streamable-HTTP endpoint. Read live at execution time, never frozen into a run.
            </p>
          </div>
          <div className="flex flex-col gap-1">
            <label className={labelClass} htmlFor="mcpHeaders">
              Headers (JSON)
            </label>
            <textarea
              id="mcpHeaders"
              name="mcpHeaders"
              rows={3}
              defaultValue={initialValues.mcpHeaders}
              placeholder='{"Authorization": "Bearer …"}'
              className={`${inputClass} font-mono`}
            />
          </div>
        </div>
      )}

      {error && <p className="text-sm text-red-600 dark:text-red-400">{error}</p>}

      <div className="flex items-center gap-2">
        <button
          type="submit"
          disabled={pending}
          className="rounded-md bg-zinc-900 px-4 py-2 text-sm font-medium text-zinc-50 transition-colors hover:bg-zinc-700 disabled:opacity-50 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-300"
        >
          {pending ? 'Saving…' : submitLabel}
        </button>
        {onCancel && (
          <button
            type="button"
            disabled={pending}
            onClick={onCancel}
            className="rounded-md border border-zinc-300 px-4 py-2 text-sm font-medium text-zinc-700 transition-colors hover:bg-zinc-100 disabled:opacity-50 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800"
          >
            Cancel
          </button>
        )}
      </div>
    </form>
  );
}
