'use client';

import { useState, useTransition } from 'react';
import { useRouter } from 'next/navigation';
import type { Prompt } from '@/db/schema';
import { createPrompt, deletePrompt, updatePrompt } from '@/actions/prompts';
import { resolveEffectiveSystemPrompt, type SystemPromptMode } from '@/lib/system-prompt';
import { DEFAULT_MAX_TURNS } from '@/lib/tools';
import { formatDateTime } from '@/lib/format';
import {
  PromptEditor,
  type SystemPromptOption,
  type ToolsetOption,
} from './prompt-editor';

function asMode(value: string): SystemPromptMode {
  return value === 'override' ? 'override' : 'append';
}

export function PromptsPanel({
  groupId,
  prompts,
  systemPrompts,
  toolsets,
  toolsetIdsByPrompt,
  canWrite,
}: {
  groupId: number;
  prompts: Prompt[];
  systemPrompts: SystemPromptOption[];
  toolsets: ToolsetOption[];
  toolsetIdsByPrompt: Record<number, number[]>;
  canWrite: boolean;
}) {
  const router = useRouter();
  const [editingId, setEditingId] = useState<number | 'new' | null>(null);
  const [, startTransition] = useTransition();
  const [actionError, setActionError] = useState<string | null>(null);

  const systemPromptById = new Map(systemPrompts.map((sp) => [sp.id, sp]));

  function handleDelete(prompt: Prompt) {
    if (!window.confirm(`Delete prompt "${prompt.title}"? This cannot be undone.`)) {
      return;
    }
    setActionError(null);
    startTransition(async () => {
      try {
        await deletePrompt(prompt.id);
        router.refresh();
      } catch {
        setActionError('Failed to delete prompt.');
      }
    });
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-zinc-900 dark:text-zinc-50">Prompts</h2>
        {canWrite && editingId === null && (
          <button
            type="button"
            onClick={() => setEditingId('new')}
            className="rounded-md bg-zinc-900 px-3 py-2 text-sm font-medium text-zinc-50 transition-colors hover:bg-zinc-700 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-300"
          >
            New prompt
          </button>
        )}
      </div>

      {editingId === 'new' && (
        <PromptEditor
          heading="New prompt"
          groupId={groupId}
          initialValues={{
            title: '',
            content: '',
            expectedOutput: '',
            systemPromptId: '',
            systemPromptMode: 'append',
            customSystemText: '',
            toolMode: 'none',
            toolChoice: '',
            maxTurns: DEFAULT_MAX_TURNS,
            toolsetIds: [],
          }}
          systemPromptOptions={systemPrompts}
          toolsetOptions={toolsets}
          submitLabel="Create prompt"
          onCancel={() => setEditingId(null)}
          onSubmit={async (formData) => {
            await createPrompt(formData);
            setEditingId(null);
            router.refresh();
          }}
        />
      )}

      {actionError && <p className="text-sm text-red-600 dark:text-red-400">{actionError}</p>}

      <ul className="flex flex-col gap-3">
        {prompts.length === 0 && editingId !== 'new' && (
          <li className="rounded-lg border border-zinc-200 px-4 py-6 text-center text-sm text-zinc-500 dark:border-zinc-800 dark:text-zinc-400">
            No prompts in this group yet.
          </li>
        )}
        {prompts.map((prompt) => {
          if (editingId === prompt.id) {
            return (
              <li key={prompt.id}>
                <PromptEditor
                  heading={`Edit "${prompt.title}"`}
                  initialValues={{
                    title: prompt.title,
                    content: prompt.content,
                    expectedOutput: prompt.expectedOutput ?? '',
                    systemPromptId: prompt.systemPromptId ? String(prompt.systemPromptId) : '',
                    systemPromptMode: asMode(prompt.systemPromptMode),
                    customSystemText: prompt.customSystemText ?? '',
                    toolMode: prompt.toolMode,
                    toolChoice: prompt.toolChoice ?? '',
                    maxTurns: prompt.maxTurns,
                    toolsetIds: toolsetIdsByPrompt[prompt.id] ?? [],
                  }}
                  systemPromptOptions={systemPrompts}
                  toolsetOptions={toolsets}
                  submitLabel="Save changes"
                  onCancel={() => setEditingId(null)}
                  onSubmit={async (formData) => {
                    await updatePrompt(prompt.id, formData);
                    setEditingId(null);
                    router.refresh();
                  }}
                />
              </li>
            );
          }

          const base = prompt.systemPromptId ? systemPromptById.get(prompt.systemPromptId) : undefined;
          const effective = resolveEffectiveSystemPrompt({
            mode: asMode(prompt.systemPromptMode),
            baseContent: base?.content ?? null,
            customText: prompt.customSystemText,
          });

          return (
            <li
              key={prompt.id}
              className="flex flex-col gap-2 rounded-lg border border-zinc-200 p-4 dark:border-zinc-800"
            >
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="flex flex-col gap-1">
                  <span className="font-medium text-zinc-900 dark:text-zinc-50">
                    {prompt.title}
                  </span>
                  <span className="text-xs text-zinc-500 dark:text-zinc-400">
                    Updated {formatDateTime(prompt.updatedAt)}
                  </span>
                </div>
                {canWrite && (
                  <div className="flex items-center gap-2">
                    <button
                      type="button"
                      onClick={() => setEditingId(prompt.id)}
                      className="rounded-md border border-zinc-300 px-3 py-1.5 text-sm font-medium text-zinc-700 transition-colors hover:bg-zinc-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800"
                    >
                      Edit
                    </button>
                    <button
                      type="button"
                      onClick={() => handleDelete(prompt)}
                      className="rounded-md border border-red-300 px-3 py-1.5 text-sm font-medium text-red-600 transition-colors hover:bg-red-50 dark:border-red-900 dark:text-red-400 dark:hover:bg-red-950"
                    >
                      Delete
                    </button>
                  </div>
                )}
              </div>

              <p className="line-clamp-2 text-sm text-zinc-600 dark:text-zinc-400">
                {prompt.content}
              </p>

              <div className="flex flex-wrap items-center gap-2 text-xs text-zinc-500 dark:text-zinc-400">
                <span
                  className={`inline-flex items-center rounded-full px-2 py-0.5 font-medium ${
                    prompt.expectedOutput
                      ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-400'
                      : 'bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400'
                  }`}
                >
                  {prompt.expectedOutput ? 'has expected output' : 'no expected output'}
                </span>
                <span aria-hidden>·</span>
                <span>
                  {base ? base.name : 'no base prompt'} ({prompt.systemPromptMode})
                </span>
                {prompt.toolMode !== 'none' && (
                  <>
                    <span aria-hidden>·</span>
                    <span className="inline-flex items-center rounded-full bg-indigo-100 px-2 py-0.5 font-medium text-indigo-700 dark:bg-indigo-950 dark:text-indigo-300">
                      tools: {prompt.toolMode}
                      {prompt.toolMode === 'execute' ? ` (max ${prompt.maxTurns} turns)` : ''}
                    </span>
                    <span>
                      {(toolsetIdsByPrompt[prompt.id] ?? [])
                        .map((id) => toolsets.find((toolset) => toolset.id === id)?.name ?? '?')
                        .join(', ') || 'no toolset'}
                    </span>
                  </>
                )}
              </div>

              <details className="text-xs text-zinc-500 dark:text-zinc-400">
                <summary className="cursor-pointer select-none">Effective system prompt</summary>
                <pre className="mt-2 whitespace-pre-wrap rounded-md bg-zinc-50 p-3 font-mono dark:bg-zinc-900">
                  {effective ?? '(no system message)'}
                </pre>
              </details>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
