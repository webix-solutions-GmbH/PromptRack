/**
 * Creating a run, independent of how it was asked for.
 *
 * The snapshot invariant (freeze prompt text, resolved system prompt and tool
 * definitions into `run_results` at creation time) lives here so the new-run
 * form and the MCP server cannot drift apart: the server action parses
 * FormData and redirects, the MCP tool parses JSON, and both end up in this
 * one function.
 */

import type { NewRunResult } from '@/db/schema';
import type { Scope } from '@/db/scope';
import { getMachine, touchMachineModel } from '@/db/repo/machines';
import { listGroupsByIds, listPrompts, listSnapshotToolRows } from '@/db/repo/prompts';
import { createRun, insertRunResults } from '@/db/repo/runs';
import { withTransaction } from '@/db/repo/scoped';
import { listSystemPromptsByIds } from '@/db/repo/system-prompts';
import { probeLlmInfo } from '@/lib/llm-info';
import { resolveEffectiveSystemPrompt, type SystemPromptMode } from '@/lib/system-prompt';
import {
  buildToolDefinitions,
  collectToolNameCollisions,
  type SnapshotTool,
  type ToolChoice,
  type ToolMode,
} from '@/lib/tools';

export interface CreateRunInput {
  machineId: number;
  modelId: string;
  groupIds: number[];
  /** Extra request body fields (temperature, max_tokens), or null for none. */
  params?: Record<string, number> | null;
  comment?: string | null;
}

export interface CreateRunResult {
  runId: number;
  machineId: number;
  machineName: string;
  modelId: string;
  groupNames: string[];
  resultCount: number;
}

/**
 * Freezes each prompt's tool configuration.
 *
 * Definitions and canned responses are content and travel with the run; an MCP
 * tool only records which toolset it came from, because its endpoint and auth
 * are credentials that must be read live at execution time.
 */
async function resolveToolSnapshots(
  scope: Scope,
  promptIds: number[],
): Promise<Map<number, SnapshotTool[]>> {
  const byPrompt = new Map<number, SnapshotTool[]>();
  if (promptIds.length === 0) return byPrompt;

  const rows = await listSnapshotToolRows(scope, promptIds);

  for (const row of rows) {
    if (!row.enabled) continue;

    const [definition] = buildToolDefinitions([
      {
        name: row.toolName,
        description: row.description,
        parametersJson: row.parametersJson,
      },
    ]);

    const list = byPrompt.get(row.promptId) ?? [];
    list.push({
      definition,
      source: row.source,
      toolsetId: row.toolsetId,
      toolsetName: row.toolsetName,
      mockResponse: row.mockResponse,
    });
    byPrompt.set(row.promptId, list);
  }

  return byPrompt;
}

/**
 * Creates a run and materializes one `run_results` row per prompt.
 *
 * Everything the run needs later — machine specs, group names, prompt text, the
 * *resolved* system prompt and the tool definitions — is snapshotted here, so
 * editing or deleting prompts and toolsets afterwards can never rewrite
 * history.
 */
export async function createRunRecord(
  scope: Scope,
  input: CreateRunInput,
): Promise<CreateRunResult> {
  const groupIds = Array.from(new Set(input.groupIds));
  if (groupIds.length === 0) {
    throw new Error('Select at least one prompt group.');
  }

  const machine = await getMachine(scope, input.machineId);
  if (!machine) {
    throw new Error('Machine not found.');
  }

  const groups = await listGroupsByIds(scope, groupIds);

  if (groups.length === 0) {
    throw new Error('The selected prompt groups no longer exist.');
  }

  const promptRows = await listPrompts(scope, {
    groupIds: groups.map((group) => group.id),
  });

  if (promptRows.length === 0) {
    throw new Error('The selected prompt groups contain no prompts.');
  }

  // Only the system prompts these prompts actually reference — this used to
  // read the whole table to build the same lookup map.
  const systemPromptIds = [
    ...new Set(
      promptRows
        .map((prompt) => prompt.systemPromptId)
        .filter((id): id is number => id !== null),
    ),
  ];
  const systemPromptRows = await listSystemPromptsByIds(scope, systemPromptIds);
  const systemPromptById = new Map(systemPromptRows.map((row) => [row.id, row]));

  const toolSnapshots = await resolveToolSnapshots(
    scope,
    promptRows.filter((prompt) => prompt.toolMode !== 'none').map((prompt) => prompt.id),
  );

  // A tool test with no usable tools would quietly become an ordinary prompt
  // and produce a result that looks meaningful but measured nothing. Refuse
  // before anything is written, and name the prompt that needs fixing.
  for (const prompt of promptRows) {
    if (prompt.toolMode === 'none') continue;

    const snapshot = toolSnapshots.get(prompt.id) ?? [];
    if (snapshot.length === 0) {
      throw new Error(
        `Prompt "${prompt.title}" has tool mode "${prompt.toolMode}" but no enabled tools. Pick a toolset or set the mode back to none.`,
      );
    }

    const collisions = collectToolNameCollisions(
      snapshot.map((entry) => ({ name: entry.definition.function.name })),
    );
    if (collisions.length > 0) {
      throw new Error(
        `Prompt "${prompt.title}" selects toolsets that both define: ${collisions.join(', ')}. Tool names must be unique within one prompt.`,
      );
    }
  }

  // Ask the endpoint about itself (server software, model metadata) and freeze
  // the answer with the run. Best-effort: an unreachable or tight-lipped server
  // just leaves the snapshot empty.
  const llmInfo = await probeLlmInfo({
    baseUrl: machine.baseUrl,
    apiKey: machine.apiKey,
    modelId: input.modelId,
  });

  const now = new Date();
  const params = input.params ?? null;
  const comment = input.comment?.trim() ? input.comment.trim() : null;

  // The run and its result rows are one unit: a crash between them used to
  // leave a run with no prompts in it, which Resume would report as finished.
  const created = await withTransaction(async (tx) => {
    const run = await createRun(
      scope,
      {
        machineId: machine.id,
        machineSnapshot: JSON.stringify({
          name: machine.name,
          base_url: machine.baseUrl,
          cpu: machine.cpu,
          ram: machine.ram,
          gpu: machine.gpu,
        }),
        modelId: input.modelId,
        params: params && Object.keys(params).length > 0 ? JSON.stringify(params) : null,
        llmInfo: llmInfo ? JSON.stringify(llmInfo) : null,
        comment,
        groupNames: JSON.stringify(groups.map((group) => group.name)),
        status: 'pending',
        createdAt: now,
      },
      tx,
    );

    const resultRows: NewRunResult[] = [];
    let sortOrder = 0;
    for (const group of groups) {
      const groupPrompts = promptRows.filter((prompt) => prompt.groupId === group.id);
      for (const prompt of groupPrompts) {
        const base = prompt.systemPromptId
          ? (systemPromptById.get(prompt.systemPromptId)?.content ?? null)
          : null;

        const snapshot = prompt.toolMode === 'none' ? [] : (toolSnapshots.get(prompt.id) ?? []);

        resultRows.push({
          runId: run.id,
          promptId: prompt.id,
          sortOrder: sortOrder++,
          groupName: group.name,
          promptTitle: prompt.title,
          promptText: prompt.content,
          expectedOutput: prompt.expectedOutput,
          systemPromptText: resolveEffectiveSystemPrompt({
            mode: prompt.systemPromptMode as SystemPromptMode,
            baseContent: base,
            customText: prompt.customSystemText,
          }),
          toolsSnapshot: snapshot.length > 0 ? JSON.stringify(snapshot) : null,
          toolMode: prompt.toolMode as ToolMode,
          toolChoice: prompt.toolChoice as ToolChoice | null,
          maxTurns: prompt.maxTurns,
          status: 'pending',
        });
      }
    }

    await insertRunResults(scope, run.id, resultRows, tx);

    // Remember the model against the machine so the next run can offer it even
    // when it was typed by hand and never showed up in /models.
    await touchMachineModel(
      scope,
      { machineId: machine.id, modelId: input.modelId, source: 'run', at: now },
      tx,
    );

    return { runId: run.id, resultCount: sortOrder };
  });

  return {
    runId: created.runId,
    machineId: machine.id,
    machineName: machine.name,
    modelId: input.modelId,
    groupNames: groups.map((group) => group.name),
    resultCount: created.resultCount,
  };
}
