/**
 * Creating a run, independent of how it was asked for.
 *
 * The snapshot invariant (freeze prompt text, resolved system prompt and tool
 * definitions into `run_results` at creation time) lives here so the new-run
 * form and the MCP server cannot drift apart: the server action parses
 * FormData and redirects, the MCP tool parses JSON, and both end up in this
 * one function.
 */

import { and, asc, eq, inArray } from 'drizzle-orm';
import { db } from '@/db';
import {
  machineModels,
  machines,
  promptGroups,
  promptToolsets,
  prompts,
  runResults,
  runs,
  systemPrompts,
  tools,
  toolsets,
} from '@/db/schema';
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
  promptIds: number[],
): Promise<Map<number, SnapshotTool[]>> {
  const byPrompt = new Map<number, SnapshotTool[]>();
  if (promptIds.length === 0) return byPrompt;

  const rows = await db
    .select({
      promptId: promptToolsets.promptId,
      toolsetId: toolsets.id,
      toolsetName: toolsets.name,
      toolName: tools.name,
      description: tools.description,
      parametersJson: tools.parametersJson,
      mockResponse: tools.mockResponse,
      enabled: tools.enabled,
      source: tools.source,
    })
    .from(promptToolsets)
    .innerJoin(toolsets, eq(promptToolsets.toolsetId, toolsets.id))
    .innerJoin(tools, eq(tools.toolsetId, toolsets.id))
    .where(inArray(promptToolsets.promptId, promptIds))
    .orderBy(
      asc(promptToolsets.promptId),
      asc(promptToolsets.sortOrder),
      asc(toolsets.id),
      asc(tools.name),
    );

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
export async function createRunRecord(input: CreateRunInput): Promise<CreateRunResult> {
  const groupIds = Array.from(new Set(input.groupIds));
  if (groupIds.length === 0) {
    throw new Error('Select at least one prompt group.');
  }

  const [machine] = await db.select().from(machines).where(eq(machines.id, input.machineId));
  if (!machine) {
    throw new Error('Machine not found.');
  }

  const groups = await db
    .select()
    .from(promptGroups)
    .where(inArray(promptGroups.id, groupIds))
    .orderBy(asc(promptGroups.sortOrder), asc(promptGroups.id));

  if (groups.length === 0) {
    throw new Error('The selected prompt groups no longer exist.');
  }

  const promptRows = await db
    .select()
    .from(prompts)
    .where(inArray(prompts.groupId, groups.map((group) => group.id)))
    .orderBy(asc(prompts.sortOrder), asc(prompts.id));

  if (promptRows.length === 0) {
    throw new Error('The selected prompt groups contain no prompts.');
  }

  const systemPromptRows = await db.select().from(systemPrompts);
  const systemPromptById = new Map(systemPromptRows.map((row) => [row.id, row]));

  const toolSnapshots = await resolveToolSnapshots(
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
  const created = await db.transaction(async (tx) => {
    const [run] = await tx
      .insert(runs)
      .values({
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
      })
      .returning({ id: runs.id });

    const resultRows: (typeof runResults.$inferInsert)[] = [];
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

    if (resultRows.length > 0) {
      await tx.insert(runResults).values(resultRows);
    }

    // Remember the model against the machine so the next run can offer it even
    // when it was typed by hand and never showed up in /models.
    const [existingModel] = await tx
      .select({ id: machineModels.id })
      .from(machineModels)
      .where(
        and(eq(machineModels.machineId, machine.id), eq(machineModels.modelId, input.modelId)),
      );

    if (existingModel) {
      await tx
        .update(machineModels)
        .set({ lastSeenAt: now })
        .where(eq(machineModels.id, existingModel.id));
    } else {
      await tx.insert(machineModels).values({
        machineId: machine.id,
        modelId: input.modelId,
        source: 'run',
        currentlyLoaded: false,
        firstSeenAt: now,
        lastSeenAt: now,
      });
    }

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
