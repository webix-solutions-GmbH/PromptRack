'use client';

import { useState, useTransition } from 'react';
import { validateParameterSchema, validateToolName } from '@/lib/tools';

const inputClass =
  'w-full rounded-md border border-zinc-300 bg-white px-3 py-2 text-sm text-zinc-900 placeholder:text-zinc-400 focus:border-zinc-500 focus:outline-none dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-50 dark:placeholder:text-zinc-500';
const labelClass = 'text-xs font-medium text-zinc-600 dark:text-zinc-400';

const EXAMPLE_SCHEMA = `{
  "type": "object",
  "properties": {
    "query": { "type": "string", "description": "What to search for" }
  },
  "required": ["query"]
}`;

export interface ToolFormValues {
  name: string;
  description: string;
  parametersJson: string;
  mockResponse: string;
}

/**
 * Editor for one tool definition.
 *
 * The name and JSON-Schema rules are checked here as you type using the same
 * pure helpers the server action enforces, so a bad schema shows up next to the
 * field instead of as an HTTP 400 halfway through a run.
 */
export function ToolForm({
  heading,
  initialValues,
  submitLabel,
  canEditName = true,
  onSubmit,
  onCancel,
}: {
  heading: string;
  initialValues: ToolFormValues;
  submitLabel: string;
  /** Discovered tools own their name — it must keep matching the MCP server. */
  canEditName?: boolean;
  onSubmit: (formData: FormData) => Promise<void>;
  onCancel: () => void;
}) {
  const [name, setName] = useState(initialValues.name);
  const [parametersJson, setParametersJson] = useState(initialValues.parametersJson);
  const [pending, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);

  const nameError = canEditName ? validateToolName(name) : null;
  const schemaError = validateParameterSchema(parametersJson);
  const blocked = nameError !== null || schemaError !== null;

  function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (blocked) return;

    const formData = new FormData(event.currentTarget);
    setError(null);
    startTransition(async () => {
      try {
        await onSubmit(formData);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to save tool.');
      }
    });
  }

  return (
    <form
      onSubmit={handleSubmit}
      className="flex flex-col gap-4 rounded-lg border border-zinc-300 bg-zinc-50 p-4 dark:border-zinc-700 dark:bg-zinc-900"
    >
      <h4 className="text-sm font-semibold text-zinc-900 dark:text-zinc-50">{heading}</h4>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <div className="flex flex-col gap-1">
          <label className={labelClass} htmlFor="name">
            Function name *
          </label>
          <input
            id="name"
            name="name"
            required
            readOnly={!canEditName}
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="search_products"
            className={`${inputClass} font-mono ${canEditName ? '' : 'opacity-60'}`}
          />
          {nameError && <p className="text-xs text-red-600 dark:text-red-400">{nameError}</p>}
        </div>

        <div className="flex flex-col gap-1">
          <label className={labelClass} htmlFor="description">
            Description
          </label>
          <input
            id="description"
            name="description"
            defaultValue={initialValues.description}
            placeholder="Search the ERP product catalogue"
            className={inputClass}
          />
          <p className="text-xs text-zinc-500 dark:text-zinc-400">
            This is what the model reads to decide whether to call the tool.
          </p>
        </div>
      </div>

      <div className="flex flex-col gap-1">
        <label className={labelClass} htmlFor="parametersJson">
          Parameters (JSON Schema)
        </label>
        <textarea
          id="parametersJson"
          name="parametersJson"
          rows={8}
          value={parametersJson}
          onChange={(event) => setParametersJson(event.target.value)}
          placeholder={EXAMPLE_SCHEMA}
          className={`${inputClass} font-mono`}
        />
        {schemaError ? (
          <p className="text-xs text-red-600 dark:text-red-400">{schemaError}</p>
        ) : (
          <p className="text-xs text-zinc-500 dark:text-zinc-400">
            Leave empty for a tool that takes no arguments.
          </p>
        )}
      </div>

      <div className="flex flex-col gap-1">
        <label className={labelClass} htmlFor="mockResponse">
          Canned response
        </label>
        <textarea
          id="mockResponse"
          name="mockResponse"
          rows={5}
          defaultValue={initialValues.mockResponse}
          placeholder='{"hits": [{"name": "ThinkPad T14", "price": 899}]}'
          className={`${inputClass} font-mono`}
        />
        <p className="text-xs text-zinc-500 dark:text-zinc-400">
          Returned verbatim in execute mode, so the whole multi-turn loop stays deterministic.
        </p>
      </div>

      {error && <p className="text-sm text-red-600 dark:text-red-400">{error}</p>}

      <div className="flex items-center gap-2">
        <button
          type="submit"
          disabled={pending || blocked}
          className="rounded-md bg-zinc-900 px-3 py-2 text-sm font-medium text-zinc-50 transition-colors hover:bg-zinc-700 disabled:opacity-50 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-300"
        >
          {pending ? 'Saving…' : submitLabel}
        </button>
        <button
          type="button"
          disabled={pending}
          onClick={onCancel}
          className="rounded-md border border-zinc-300 px-3 py-2 text-sm font-medium text-zinc-700 transition-colors hover:bg-zinc-100 disabled:opacity-50 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800"
        >
          Cancel
        </button>
      </div>
    </form>
  );
}
