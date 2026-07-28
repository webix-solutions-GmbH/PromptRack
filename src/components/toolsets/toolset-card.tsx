'use client';

import { useState, useTransition } from 'react';
import { useRouter } from 'next/navigation';
import type { Tool, Toolset } from '@/db/schema';
import {
  createTool,
  deleteTool,
  deleteToolset,
  setToolEnabled,
  updateTool,
  updateToolset,
} from '@/actions/toolsets';
import { formatDateTime } from '@/lib/format';
import { DiscoverToolsButton } from './discover-tools-button';
import { ToolForm } from './tool-form';
import { ToolsetForm, type ToolsetKind } from './toolset-form';

const EMPTY_TOOL = {
  name: '',
  description: '',
  parametersJson: '',
  mockResponse: '',
};

function KindBadge({ kind }: { kind: ToolsetKind }) {
  const styles =
    kind === 'mcp'
      ? 'bg-indigo-100 text-indigo-700 dark:bg-indigo-950 dark:text-indigo-300'
      : 'bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300';

  return (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${styles}`}>
      {kind === 'mcp' ? 'MCP' : 'manual'}
    </span>
  );
}

export function ToolsetCard({ toolset, tools }: { toolset: Toolset; tools: Tool[] }) {
  const router = useRouter();
  const [editingToolset, setEditingToolset] = useState(false);
  const [editingToolId, setEditingToolId] = useState<number | 'new' | null>(null);
  const [pending, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);

  const enabledCount = tools.filter((tool) => tool.enabled).length;

  function run(action: () => Promise<void>, failure: string) {
    setError(null);
    startTransition(async () => {
      try {
        await action();
        router.refresh();
      } catch (err) {
        setError(err instanceof Error ? err.message : failure);
      }
    });
  }

  function handleDeleteToolset() {
    if (
      !window.confirm(
        `Delete toolset "${toolset.name}" and its ${tools.length} tool(s)? Past runs keep their frozen copies.`,
      )
    ) {
      return;
    }
    run(() => deleteToolset(toolset.id), 'Failed to delete toolset.');
  }

  return (
    <li className="flex flex-col gap-4 rounded-lg border border-zinc-200 p-5 dark:border-zinc-800">
      {editingToolset ? (
        <ToolsetForm
          initialValues={{
            name: toolset.name,
            description: toolset.description ?? '',
            kind: toolset.kind,
            mcpUrl: toolset.mcpUrl ?? '',
            mcpHeaders: toolset.mcpHeaders ?? '',
          }}
          submitLabel="Save changes"
          onCancel={() => setEditingToolset(false)}
          onSubmit={async (formData) => {
            await updateToolset(toolset.id, formData);
            setEditingToolset(false);
            router.refresh();
          }}
        />
      ) : (
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="flex min-w-0 flex-col gap-1">
            <div className="flex items-center gap-2">
              <span className="font-medium text-zinc-900 dark:text-zinc-50">{toolset.name}</span>
              <KindBadge kind={toolset.kind} />
            </div>
            {toolset.description && (
              <span className="text-sm text-zinc-600 dark:text-zinc-400">
                {toolset.description}
              </span>
            )}
            {toolset.mcpUrl && (
              <span className="truncate font-mono text-xs text-zinc-500 dark:text-zinc-400">
                {toolset.mcpUrl}
              </span>
            )}
            <span className="text-xs text-zinc-500 dark:text-zinc-400">
              {enabledCount} of {tools.length} tool(s) enabled · updated{' '}
              {formatDateTime(toolset.updatedAt)}
            </span>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            {toolset.kind === 'mcp' && <DiscoverToolsButton toolsetId={toolset.id} />}
            <button
              type="button"
              onClick={() => setEditingToolset(true)}
              className="rounded-md border border-zinc-300 px-3 py-1.5 text-sm font-medium text-zinc-700 transition-colors hover:bg-zinc-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800"
            >
              Edit
            </button>
            <button
              type="button"
              onClick={handleDeleteToolset}
              disabled={pending}
              className="rounded-md border border-red-300 px-3 py-1.5 text-sm font-medium text-red-600 transition-colors hover:bg-red-50 disabled:opacity-50 dark:border-red-900 dark:text-red-400 dark:hover:bg-red-950"
            >
              Delete
            </button>
          </div>
        </div>
      )}

      {error && <p className="text-sm text-red-600 dark:text-red-400" role="alert">{error}</p>}

      <div className="flex flex-col gap-3 border-t border-zinc-200 pt-4 dark:border-zinc-800">
        <div className="flex items-center justify-between">
          <h3 className="text-xs font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
            Tools
          </h3>
          {editingToolId === null && (
            <button
              type="button"
              onClick={() => setEditingToolId('new')}
              className="rounded-md border border-zinc-300 px-3 py-1.5 text-sm font-medium text-zinc-700 transition-colors hover:bg-zinc-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800"
            >
              Add tool
            </button>
          )}
        </div>

        {editingToolId === 'new' && (
          <ToolForm
            heading="New tool"
            initialValues={EMPTY_TOOL}
            submitLabel="Add tool"
            onCancel={() => setEditingToolId(null)}
            onSubmit={async (formData) => {
              await createTool(toolset.id, formData);
              setEditingToolId(null);
              router.refresh();
            }}
          />
        )}

        {tools.length === 0 && editingToolId !== 'new' && (
          <p className="rounded-md border border-dashed border-zinc-300 px-4 py-6 text-center text-sm text-zinc-500 dark:border-zinc-700 dark:text-zinc-400">
            {toolset.kind === 'mcp'
              ? 'No tools yet — run Discover to import them from the server.'
              : 'No tools yet — add one with “Add tool”.'}
          </p>
        )}

        <ul className="flex flex-col gap-2">
          {tools.map((tool) =>
            editingToolId === tool.id ? (
              <li key={tool.id}>
                <ToolForm
                  heading={`Edit "${tool.name}"`}
                  canEditName={tool.source === 'manual'}
                  initialValues={{
                    name: tool.name,
                    description: tool.description ?? '',
                    parametersJson: tool.parametersJson,
                    mockResponse: tool.mockResponse ?? '',
                  }}
                  submitLabel="Save tool"
                  onCancel={() => setEditingToolId(null)}
                  onSubmit={async (formData) => {
                    await updateTool(tool.id, formData);
                    setEditingToolId(null);
                    router.refresh();
                  }}
                />
              </li>
            ) : (
              <li
                key={tool.id}
                className={`flex flex-wrap items-start justify-between gap-3 rounded-md border border-zinc-200 p-3 dark:border-zinc-800 ${
                  tool.enabled ? '' : 'opacity-60'
                }`}
              >
                <div className="flex min-w-0 flex-col gap-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-mono text-sm text-zinc-900 dark:text-zinc-50">
                      {tool.name}
                    </span>
                    {!tool.enabled && (
                      <span className="inline-flex items-center rounded-full bg-zinc-100 px-2 py-0.5 text-xs font-medium text-zinc-500 dark:bg-zinc-800 dark:text-zinc-400">
                        disabled
                      </span>
                    )}
                    {tool.source === 'mcp' && (
                      <span className="text-xs text-zinc-500 dark:text-zinc-400">discovered</span>
                    )}
                  </div>
                  {tool.description && (
                    <span className="text-xs text-zinc-600 dark:text-zinc-400">
                      {tool.description}
                    </span>
                  )}
                  <details className="text-xs text-zinc-500 dark:text-zinc-400">
                    <summary className="cursor-pointer select-none">Schema &amp; response</summary>
                    <pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap rounded-md bg-zinc-50 p-2 font-mono dark:bg-zinc-900">
                      {tool.parametersJson}
                    </pre>
                    <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap rounded-md bg-zinc-50 p-2 font-mono dark:bg-zinc-900">
                      {tool.mockResponse ?? '(no canned response)'}
                    </pre>
                  </details>
                </div>

                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    disabled={pending}
                    onClick={() =>
                      run(
                        () => setToolEnabled(tool.id, !tool.enabled),
                        'Failed to change the tool.',
                      )
                    }
                    className="rounded-md border border-zinc-300 px-2 py-1 text-xs font-medium text-zinc-700 transition-colors hover:bg-zinc-100 disabled:opacity-50 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800"
                  >
                    {tool.enabled ? 'Disable' : 'Enable'}
                  </button>
                  <button
                    type="button"
                    onClick={() => setEditingToolId(tool.id)}
                    className="rounded-md border border-zinc-300 px-2 py-1 text-xs font-medium text-zinc-700 transition-colors hover:bg-zinc-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800"
                  >
                    Edit
                  </button>
                  <button
                    type="button"
                    disabled={pending}
                    onClick={() => {
                      if (!window.confirm(`Delete tool "${tool.name}"?`)) return;
                      run(() => deleteTool(tool.id), 'Failed to delete the tool.');
                    }}
                    className="rounded-md border border-red-300 px-2 py-1 text-xs font-medium text-red-600 transition-colors hover:bg-red-50 disabled:opacity-50 dark:border-red-900 dark:text-red-400 dark:hover:bg-red-950"
                  >
                    Delete
                  </button>
                </div>
              </li>
            ),
          )}
        </ul>
      </div>
    </li>
  );
}
