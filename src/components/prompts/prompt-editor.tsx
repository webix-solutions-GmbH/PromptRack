'use client';

import { useState, useTransition } from 'react';
import { resolveEffectiveSystemPrompt, type SystemPromptMode } from '@/lib/system-prompt';

const inputClass =
  'w-full rounded-md border border-zinc-300 bg-white px-3 py-2 text-sm text-zinc-900 placeholder:text-zinc-400 focus:border-zinc-500 focus:outline-none dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-50 dark:placeholder:text-zinc-500';
const labelClass = 'text-xs font-medium text-zinc-600 dark:text-zinc-400';

export interface SystemPromptOption {
  id: number;
  name: string;
  content: string;
}

export interface PromptEditorInitialValues {
  title: string;
  content: string;
  expectedOutput: string;
  systemPromptId: string; // '' means none
  systemPromptMode: SystemPromptMode;
  customSystemText: string;
}

interface PromptEditorProps {
  heading: string;
  groupId?: number;
  initialValues: PromptEditorInitialValues;
  systemPromptOptions: SystemPromptOption[];
  submitLabel: string;
  onSubmit: (formData: FormData) => Promise<void>;
  onCancel: () => void;
}

export function PromptEditor({
  heading,
  groupId,
  initialValues,
  systemPromptOptions,
  submitLabel,
  onSubmit,
  onCancel,
}: PromptEditorProps) {
  const [systemPromptId, setSystemPromptId] = useState(initialValues.systemPromptId);
  const [mode, setMode] = useState<SystemPromptMode>(initialValues.systemPromptMode);
  const [customSystemText, setCustomSystemText] = useState(initialValues.customSystemText);
  const [pending, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);

  const selectedBase = systemPromptOptions.find((sp) => String(sp.id) === systemPromptId);
  const preview = resolveEffectiveSystemPrompt({
    mode,
    baseContent: selectedBase?.content ?? null,
    customText: customSystemText,
  });

  function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formData = new FormData(event.currentTarget);
    setError(null);
    startTransition(async () => {
      try {
        await onSubmit(formData);
      } catch {
        setError('Failed to save prompt.');
      }
    });
  }

  return (
    <div className="rounded-lg border border-zinc-200 p-6 dark:border-zinc-800">
      <h3 className="mb-4 text-lg font-semibold text-zinc-900 dark:text-zinc-50">{heading}</h3>
      <form onSubmit={handleSubmit} className="flex flex-col gap-4">
        {groupId !== undefined && <input type="hidden" name="groupId" value={groupId} />}

        <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
          <div className="flex flex-col gap-4">
            <div className="flex flex-col gap-1">
              <label className={labelClass} htmlFor="title">
                Title *
              </label>
              <input
                id="title"
                name="title"
                required
                defaultValue={initialValues.title}
                className={inputClass}
              />
            </div>
            <div className="flex flex-col gap-1">
              <label className={labelClass} htmlFor="content">
                Prompt (user message) *
              </label>
              <textarea
                id="content"
                name="content"
                required
                rows={6}
                defaultValue={initialValues.content}
                className={`${inputClass} font-mono`}
              />
            </div>
            <div className="flex flex-col gap-1">
              <label className={labelClass} htmlFor="expectedOutput">
                Expected output
              </label>
              <textarea
                id="expectedOutput"
                name="expectedOutput"
                rows={4}
                defaultValue={initialValues.expectedOutput}
                placeholder="optional"
                className={`${inputClass} font-mono`}
              />
            </div>
          </div>

          <div className="flex flex-col gap-4">
            <div className="flex flex-col gap-1">
              <label className={labelClass} htmlFor="systemPromptId">
                Base system prompt
              </label>
              <select
                id="systemPromptId"
                name="systemPromptId"
                value={systemPromptId}
                onChange={(event) => setSystemPromptId(event.target.value)}
                className={inputClass}
              >
                <option value="">(none)</option>
                {systemPromptOptions.map((sp) => (
                  <option key={sp.id} value={sp.id}>
                    {sp.name}
                  </option>
                ))}
              </select>
            </div>

            <div className="flex flex-col gap-2">
              <span className={labelClass}>Mode</span>
              <div className="flex gap-4 text-sm text-zinc-700 dark:text-zinc-300">
                <label className="flex items-center gap-2">
                  <input
                    type="radio"
                    name="systemPromptMode"
                    value="append"
                    checked={mode === 'append'}
                    onChange={() => setMode('append')}
                  />
                  Append
                </label>
                <label className="flex items-center gap-2">
                  <input
                    type="radio"
                    name="systemPromptMode"
                    value="override"
                    checked={mode === 'override'}
                    onChange={() => setMode('override')}
                  />
                  Override
                </label>
              </div>
            </div>

            <div className="flex flex-col gap-1">
              <label className={labelClass} htmlFor="customSystemText">
                {mode === 'override'
                  ? 'Custom system text (replaces base)'
                  : 'Custom system text (appended after base)'}
              </label>
              <textarea
                id="customSystemText"
                name="customSystemText"
                rows={4}
                value={customSystemText}
                onChange={(event) => setCustomSystemText(event.target.value)}
                className={`${inputClass} font-mono`}
              />
            </div>

            <div className="flex flex-col gap-1">
              <span className={labelClass}>Effective system prompt preview</span>
              <pre className="min-h-[4.5rem] whitespace-pre-wrap rounded-md border border-dashed border-zinc-300 bg-zinc-50 p-3 font-mono text-xs text-zinc-700 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-300">
                {preview ?? '(no system message)'}
              </pre>
            </div>
          </div>
        </div>

        {error && <p className="text-sm text-red-600 dark:text-red-400">{error}</p>}

        <div className="flex items-center gap-2">
          <button
            type="submit"
            disabled={pending}
            className="rounded-md bg-zinc-900 px-4 py-2 text-sm font-medium text-zinc-50 transition-colors hover:bg-zinc-700 disabled:opacity-50 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-300"
          >
            {pending ? 'Saving…' : submitLabel}
          </button>
          <button
            type="button"
            disabled={pending}
            onClick={onCancel}
            className="rounded-md border border-zinc-300 px-4 py-2 text-sm font-medium text-zinc-700 transition-colors hover:bg-zinc-100 disabled:opacity-50 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800"
          >
            Cancel
          </button>
        </div>
      </form>
    </div>
  );
}
