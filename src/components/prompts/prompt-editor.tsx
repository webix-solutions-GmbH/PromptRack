'use client';

import { useState, useTransition } from 'react';
import { resolveEffectiveSystemPrompt, type SystemPromptMode } from '@/lib/system-prompt';
import {
  collectToolNameCollisions,
  DEFAULT_MAX_TURNS,
  MAX_TURNS_LIMIT,
  type ToolChoice,
  type ToolMode,
} from '@/lib/tools';

const inputClass =
  'w-full rounded-md border border-zinc-300 bg-white px-3 py-2 text-sm text-zinc-900 placeholder:text-zinc-400 focus:border-zinc-500 focus:outline-none dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-50 dark:placeholder:text-zinc-500';
const labelClass = 'text-xs font-medium text-zinc-600 dark:text-zinc-400';

export interface SystemPromptOption {
  id: number;
  name: string;
  content: string;
}

/** A selectable toolset, with the tools it would contribute. */
export interface ToolsetOption {
  id: number;
  name: string;
  kind: 'manual' | 'mcp';
  tools: { name: string; description: string | null; enabled: boolean }[];
}

const TOOL_MODES: { value: ToolMode; label: string; hint: string }[] = [
  { value: 'none', label: 'No tools', hint: 'One user message, one answer.' },
  {
    value: 'definitions',
    label: 'Definitions only',
    hint: 'Offer the tools and record what the model wanted to call. Nothing is executed.',
  },
  {
    value: 'execute',
    label: 'Execute',
    hint: 'Run each call, feed the result back, and keep going until the model answers.',
  },
];

export interface PromptEditorInitialValues {
  title: string;
  content: string;
  expectedOutput: string;
  systemPromptId: string; // '' means none
  systemPromptMode: SystemPromptMode;
  customSystemText: string;
  toolMode: ToolMode;
  toolChoice: ToolChoice | '';
  maxTurns: number;
  toolsetIds: number[];
}

interface PromptEditorProps {
  heading: string;
  groupId?: number;
  initialValues: PromptEditorInitialValues;
  systemPromptOptions: SystemPromptOption[];
  toolsetOptions: ToolsetOption[];
  submitLabel: string;
  onSubmit: (formData: FormData) => Promise<void>;
  onCancel: () => void;
}

export function PromptEditor({
  heading,
  groupId,
  initialValues,
  systemPromptOptions,
  toolsetOptions,
  submitLabel,
  onSubmit,
  onCancel,
}: PromptEditorProps) {
  const [systemPromptId, setSystemPromptId] = useState(initialValues.systemPromptId);
  const [mode, setMode] = useState<SystemPromptMode>(initialValues.systemPromptMode);
  const [customSystemText, setCustomSystemText] = useState(initialValues.customSystemText);
  const [toolMode, setToolMode] = useState<ToolMode>(initialValues.toolMode);
  const [toolsetIds, setToolsetIds] = useState<number[]>(initialValues.toolsetIds);
  const [pending, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);

  const selectedBase = systemPromptOptions.find((sp) => String(sp.id) === systemPromptId);
  const preview = resolveEffectiveSystemPrompt({
    mode,
    baseContent: selectedBase?.content ?? null,
    customText: customSystemText,
  });

  const toolsActive = toolMode !== 'none';
  const selectedToolsets = toolsetOptions.filter((toolset) => toolsetIds.includes(toolset.id));
  const offeredTools = selectedToolsets.flatMap((toolset) =>
    toolset.tools.filter((tool) => tool.enabled),
  );
  const collisions = collectToolNameCollisions(offeredTools);
  // The same checks `createRun` enforces, surfaced while the prompt is being
  // written rather than when a run refuses to start.
  const toolError = !toolsActive
    ? null
    : offeredTools.length === 0
      ? 'Pick at least one toolset with an enabled tool, or set the mode back to “No tools”.'
      : collisions.length > 0
        ? `Two selected toolsets both define: ${collisions.join(', ')}. Tool names must be unique within one prompt.`
        : null;

  function toggleToolset(id: number) {
    setToolsetIds((current) =>
      current.includes(id) ? current.filter((value) => value !== id) : [...current, id],
    );
  }

  function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (toolError !== null) return;

    const formData = new FormData(event.currentTarget);
    setError(null);
    startTransition(async () => {
      try {
        await onSubmit(formData);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to save prompt.');
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

        <div className="flex flex-col gap-4 border-t border-zinc-200 pt-4 dark:border-zinc-800">
          <div className="flex flex-col gap-2">
            <span className={labelClass}>Tools</span>
            <div className="flex flex-col gap-1.5 text-sm text-zinc-700 dark:text-zinc-300">
              {TOOL_MODES.map((option) => (
                <label key={option.value} className="flex items-start gap-2">
                  <input
                    type="radio"
                    name="toolMode"
                    value={option.value}
                    checked={toolMode === option.value}
                    onChange={() => setToolMode(option.value)}
                    className="mt-1"
                  />
                  <span className="flex flex-col">
                    <span>{option.label}</span>
                    <span className="text-xs text-zinc-500 dark:text-zinc-400">{option.hint}</span>
                  </span>
                </label>
              ))}
            </div>
          </div>

          {/*
            The tool inputs are unmounted in "No tools" mode, so nothing about
            them would be submitted. Carry the current selection in hidden
            fields instead — switching the mode off and on again should not
            silently discard it.
          */}
          {!toolsActive && (
            <>
              {toolsetIds.map((id) => (
                <input key={id} type="hidden" name="toolsetIds" value={id} />
              ))}
              <input type="hidden" name="toolChoice" value={initialValues.toolChoice} />
              <input
                type="hidden"
                name="maxTurns"
                value={initialValues.maxTurns || DEFAULT_MAX_TURNS}
              />
            </>
          )}

          {toolsActive && (
            <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
              <div className="flex flex-col gap-4">
                <div className="flex flex-col gap-2">
                  <span className={labelClass}>Toolsets</span>
                  {toolsetOptions.length === 0 ? (
                    <p className="rounded-md border border-dashed border-zinc-300 p-3 text-xs text-zinc-500 dark:border-zinc-700 dark:text-zinc-400">
                      No toolsets defined yet — create one on the Toolsets page first.
                    </p>
                  ) : (
                    <div className="flex flex-col gap-1.5 text-sm text-zinc-700 dark:text-zinc-300">
                      {toolsetOptions.map((toolset) => (
                        <label key={toolset.id} className="flex items-center gap-2">
                          <input
                            type="checkbox"
                            name="toolsetIds"
                            value={toolset.id}
                            checked={toolsetIds.includes(toolset.id)}
                            onChange={() => toggleToolset(toolset.id)}
                          />
                          <span>
                            {toolset.name}{' '}
                            <span className="text-xs text-zinc-500 dark:text-zinc-400">
                              ({toolset.kind},{' '}
                              {toolset.tools.filter((tool) => tool.enabled).length} enabled)
                            </span>
                          </span>
                        </label>
                      ))}
                    </div>
                  )}
                </div>

                <div className="grid grid-cols-2 gap-4">
                  <div className="flex flex-col gap-1">
                    <label className={labelClass} htmlFor="toolChoice">
                      Tool choice
                    </label>
                    <select
                      id="toolChoice"
                      name="toolChoice"
                      defaultValue={initialValues.toolChoice}
                      className={inputClass}
                    >
                      <option value="">server default</option>
                      <option value="auto">auto</option>
                      <option value="required">required</option>
                      <option value="none">none</option>
                    </select>
                  </div>
                  <div className="flex flex-col gap-1">
                    <label className={labelClass} htmlFor="maxTurns">
                      Max turns
                    </label>
                    <input
                      id="maxTurns"
                      name="maxTurns"
                      type="number"
                      min={1}
                      max={MAX_TURNS_LIMIT}
                      defaultValue={initialValues.maxTurns || DEFAULT_MAX_TURNS}
                      className={inputClass}
                    />
                  </div>
                </div>
              </div>

              <div className="flex flex-col gap-1">
                <span className={labelClass}>Tools offered to the model</span>
                <div className="min-h-[4.5rem] rounded-md border border-dashed border-zinc-300 bg-zinc-50 p-3 dark:border-zinc-700 dark:bg-zinc-900">
                  {offeredTools.length === 0 ? (
                    <p className="text-xs text-zinc-500 dark:text-zinc-400">(no tools)</p>
                  ) : (
                    <ul className="flex flex-col gap-1">
                      {offeredTools.map((tool, index) => (
                        <li key={`${tool.name}-${index}`} className="text-xs">
                          <span className="font-mono text-zinc-800 dark:text-zinc-200">
                            {tool.name}
                          </span>
                          {tool.description && (
                            <span className="text-zinc-500 dark:text-zinc-400">
                              {' '}
                              — {tool.description}
                            </span>
                          )}
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
                {toolError && (
                  <p className="text-xs text-red-600 dark:text-red-400">{toolError}</p>
                )}
              </div>
            </div>
          )}
        </div>

        {error && <p className="text-sm text-red-600 dark:text-red-400">{error}</p>}

        <div className="flex items-center gap-2">
          <button
            type="submit"
            disabled={pending || toolError !== null}
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
