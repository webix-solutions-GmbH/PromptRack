/**
 * MCP tools for running the evaluation and reading the measurements back.
 *
 * Creating a run goes through `createRunRecord`, the same function the new-run
 * form uses, so an MCP-created run is snapshotted identically and shows up in
 * the UI as an ordinary run.
 *
 * Execution is fire-and-forget: `execute_run` starts the sequential executor and
 * returns immediately, because a run of a dozen prompts outlives any sane
 * tool-call timeout. Progress is read back with `get_run`, and
 * the invariants that make that safe already exist — every result row is
 * persisted as it finishes, and a crashed execution leaves the rest of the run
 * `pending` for a later resume.
 */

import { revalidatePath } from 'next/cache';
import type { Scope } from '@/db/scope';
import { listLoadedModels, listMachineModels, listMachines as listMachineRows } from '@/db/repo/machines';
import { listGroups } from '@/db/repo/prompts';
import {
  getRun as getRunRow,
  getRunResult as getRunResultRow,
  listResultRatings,
  listResultStatuses,
  listRunResults,
  listRuns as listRunRows,
  rateResult,
  runResultTallies,
} from '@/db/repo/runs';
import { parseLlmInfo } from '@/lib/llm-info';
import { countRatings, type Rating } from '@/lib/rating';
import { createRunRecord } from '@/lib/run-create';
import { executeRun, isRunExecuting } from '@/lib/run-executor';
import { parseToolsSnapshot, snapshotToolNames } from '@/lib/tools';
import {
  McpToolError,
  hasKey,
  optionalBoolean,
  optionalEnum,
  optionalInteger,
  optionalNumber,
  optionalRowRefList,
  optionalString,
  optionalText,
  requireInteger,
  requireRowRef,
  resolveRowRef,
  truncate,
  type ToolArgs,
} from './args';
import { CUSTOMER_ARG, resolveMcpScope } from './customer';
import type { McpCallContext, McpToolSpec } from './protocol';

const DEFAULT_RUN_LIMIT = 20;
const DEFAULT_RESPONSE_CHARS = 4000;

function revalidateRuns(runId?: number) {
  revalidatePath('/runs');
  revalidatePath('/');
  if (runId !== undefined) revalidatePath(`/runs/${runId}`);
}

function parseJson<T>(raw: string | null): T | null {
  if (!raw) return null;
  try {
    return JSON.parse(raw) as T;
  } catch {
    return null;
  }
}

/**
 * Starts execution outside the request that asked for it.
 *
 * The route handler's response is sent while the executor keeps running in the
 * same process — fine here because the server is long-lived and single-user, and
 * because an interrupted run is recoverable by design.
 */
function startBackgroundExecution(runId: number) {
  void executeRun(runId, () => {}).catch((err: unknown) => {
    console.error(
      `[mcp] background execution of run ${runId} failed:`,
      err instanceof Error ? err.message : err,
    );
  });
}

async function loadRun(scope: Scope, runId: number) {
  const run = await getRunRow(scope, runId);
  if (!run) {
    throw new McpToolError(`No run with id ${runId}.`);
  }
  return run;
}

/** Progress and verdict tallies over a run's result rows. */
function summarizeResults(rows: { status: string; rating: string | null }[]) {
  return {
    total: rows.length,
    ok: rows.filter((row) => row.status === 'ok').length,
    error: rows.filter((row) => row.status === 'error').length,
    pending: rows.filter((row) => row.status === 'pending').length,
    running: rows.filter((row) => row.status === 'running').length,
    ratings: countRatings(rows.map((row) => row.rating)),
  };
}

/** The same shape as {@link summarizeResults}, out of a SQL aggregate. */
function summaryFromTally(
  tally:
    | {
        total: number;
        ok: number;
        error: number;
        pending: number;
        running: number;
        good: number;
        meh: number;
        bad: number;
        unrated: number;
      }
    | undefined,
) {
  return {
    total: tally?.total ?? 0,
    ok: tally?.ok ?? 0,
    error: tally?.error ?? 0,
    pending: tally?.pending ?? 0,
    running: tally?.running ?? 0,
    ratings: {
      good: tally?.good ?? 0,
      meh: tally?.meh ?? 0,
      bad: tally?.bad ?? 0,
      unrated: tally?.unrated ?? 0,
    },
  };
}

const listMachines: McpToolSpec = {
  name: 'list_machines',
  description:
    'List the registered endpoints (a machine is an OpenAI-compatible base URL plus hardware notes) and every model ever seen on each, flagging which are currently loaded. API keys are never returned.',
  readOnly: true,
  inputSchema: { type: 'object', properties: { customer: CUSTOMER_ARG } },
  handler: async (args: ToolArgs, ctx: McpCallContext) => {
    const scope = await resolveMcpScope(args, ctx.source);
    const rows = await listMachineRows(scope, 'name');
    const modelRows = await listMachineModels(scope);

    return {
      machines: rows.map((machine) => ({
        id: machine.id,
        name: machine.name,
        base_url: machine.baseUrl,
        cpu: machine.cpu,
        ram: machine.ram,
        gpu: machine.gpu,
        notes: machine.notes,
        models: modelRows
          .filter((model) => model.machineId === machine.id)
          .map((model) => ({
            model_id: model.modelId,
            currently_loaded: model.currentlyLoaded,
            source: model.source,
            last_seen_at: model.lastSeenAt.getTime(),
          })),
      })),
    };
  },
};

const createRunTool: McpToolSpec = {
  name: 'create_run',
  description:
    'Create a run of one or more prompt groups against a model on a machine. The prompt text, resolved system prompt and tool definitions are frozen into the run, so later edits never rewrite it. Set execute: true to start it immediately; otherwise it stays pending and can be started with execute_run or from the UI.',
  inputSchema: {
    type: 'object',
    properties: {
      customer: CUSTOMER_ARG,
      machine: {
        type: ['string', 'integer'],
        description: 'Name or id of the machine to run against (see list_machines).',
      },
      model: {
        type: 'string',
        description:
          'Model id as the endpoint names it. May be omitted when the machine reports exactly one currently loaded model.',
      },
      groups: {
        type: 'array',
        items: { type: ['string', 'integer'] },
        description: 'Names or ids of the prompt groups to run. Every prompt in them is included.',
      },
      temperature: { type: 'number', description: 'Optional; 0-2. Omitted means the server default.' },
      max_tokens: { type: 'integer', description: 'Optional completion limit.' },
      comment: {
        type: 'string',
        description: 'Note describing the conditions, e.g. "Q4_K_M, 8k ctx".',
      },
      execute: { type: 'boolean', description: 'Start executing right away. Default false.' },
    },
    required: ['machine', 'groups'],
  },
  handler: async (args: ToolArgs, ctx: McpCallContext) => {
    const scope = await resolveMcpScope(args, ctx.source);
    const machineRows = await listMachineRows(scope, 'name');
    const machine = resolveRowRef(requireRowRef(args, 'machine'), machineRows, 'machine');

    const groupRefs = optionalRowRefList(args, 'groups');
    if (!groupRefs || groupRefs.length === 0) {
      throw new McpToolError('"groups" must name at least one prompt group.');
    }

    const groupRows = await listGroups(scope, 'sort-id');
    const groupIds = groupRefs.map(
      (ref) => resolveRowRef(ref, groupRows, 'prompt group').id,
    );

    let modelId = optionalString(args, 'model');
    if (!modelId) {
      const loaded = await listLoadedModels(scope, machine.id);

      if (loaded.length === 1) {
        modelId = loaded[0].modelId;
      } else if (loaded.length === 0) {
        throw new McpToolError(
          `"model" is required: machine "${machine.name}" has no model marked as currently loaded. Run Discover on the machine page, or pass the model id.`,
        );
      } else {
        throw new McpToolError(
          `"model" is required: machine "${machine.name}" has several loaded models (${loaded
            .map((model) => model.modelId)
            .join(', ')}).`,
        );
      }
    }

    const params: Record<string, number> = {};
    const temperature = optionalNumber(args, 'temperature');
    if (temperature !== null) {
      if (temperature < 0 || temperature > 2) {
        throw new McpToolError('"temperature" must be between 0 and 2.');
      }
      params.temperature = temperature;
    }
    const maxTokens = optionalInteger(args, 'max_tokens');
    if (maxTokens !== null) {
      if (maxTokens < 1) {
        throw new McpToolError('"max_tokens" must be a positive whole number.');
      }
      params.max_tokens = maxTokens;
    }

    let created;
    try {
      created = await createRunRecord(scope, {
        machineId: machine.id,
        modelId,
        groupIds,
        params,
        comment: optionalString(args, 'comment'),
      });
    } catch (err) {
      // createRunRecord's refusals (a tool test without tools, an empty group)
      // are messages for the caller, not server faults.
      throw new McpToolError(err instanceof Error ? err.message : 'Could not create the run.');
    }

    const execute = optionalBoolean(args, 'execute', false);
    if (execute) startBackgroundExecution(created.runId);

    revalidateRuns(created.runId);
    revalidatePath(`/machines/${created.machineId}`);

    return {
      run: {
        id: created.runId,
        machine: created.machineName,
        model: created.modelId,
        groups: created.groupNames,
        prompt_count: created.resultCount,
        status: execute ? 'running' : 'pending',
      },
      executing: execute,
      note: execute
        ? 'Execution started in the background. Poll get_run for progress.'
        : 'The run is pending. Call execute_run to start it, or press Start in the UI.',
    };
  },
};

const executeRunTool: McpToolSpec = {
  name: 'execute_run',
  description:
    'Start (or resume) execution of a run in the background and return immediately. Only rows still pending are executed, so this doubles as Resume. Poll get_run for progress.',
  inputSchema: {
    type: 'object',
    properties: {
      customer: CUSTOMER_ARG,
      run_id: { type: 'integer', description: 'Run id.' },
    },
    required: ['run_id'],
  },
  handler: async (args: ToolArgs, ctx: McpCallContext) => {
    const scope = await resolveMcpScope(args, ctx.source);
    const runId = requireInteger(args, 'run_id');
    const run = await loadRun(scope, runId);

    if (await isRunExecuting(runId)) {
      throw new McpToolError(`Run ${runId} is already executing.`);
    }

    const rows = await listResultStatuses(scope, runId);

    const pending = rows.filter(
      (row) => row.status === 'pending' || row.status === 'running',
    ).length;

    if (pending === 0) {
      return {
        started: false,
        run_id: runId,
        status: run.status,
        note: 'Nothing left to execute. Errored rows are not retried automatically; recreate the run to re-measure.',
      };
    }

    startBackgroundExecution(runId);
    revalidateRuns(runId);

    return {
      started: true,
      run_id: runId,
      pending,
      note: 'Execution runs in the background; poll get_run for progress.',
    };
  },
};

const listRuns: McpToolSpec = {
  name: 'list_runs',
  description:
    'List runs newest first, with progress and rating tallies. Archived runs are excluded unless asked for.',
  readOnly: true,
  inputSchema: {
    type: 'object',
    properties: {
      customer: CUSTOMER_ARG,
      status: {
        type: 'string',
        enum: ['pending', 'running', 'completed', 'failed'],
        description: 'Only runs in this state.',
      },
      archived: {
        type: 'string',
        enum: ['exclude', 'only', 'all'],
        description: 'Default "exclude".',
      },
      model: { type: 'string', description: 'Only runs whose model id contains this substring.' },
      limit: { type: 'integer', description: `Default ${DEFAULT_RUN_LIMIT}.` },
    },
  },
  handler: async (args: ToolArgs, ctx: McpCallContext) => {
    const status = optionalEnum(args, 'status', [
      'pending',
      'running',
      'completed',
      'failed',
    ] as const);
    const archived = optionalEnum(args, 'archived', ['exclude', 'only', 'all'] as const) ?? 'exclude';
    const modelFilter = optionalString(args, 'model');
    const limit = Math.max(1, optionalInteger(args, 'limit') ?? DEFAULT_RUN_LIMIT);

    const scope = await resolveMcpScope(args, ctx.source);

    // Status and the archived vocabulary go into SQL. The model substring stays
    // in JS: it is applied before the limit, so pushing it down would need
    // LIKE-escaping the user's value for no gain at this table size.
    const rows = await listRunRows(scope, {
      ...(status ? { status } : {}),
      archived,
      ...(modelFilter ? {} : { limit }),
    });

    const filtered = (
      modelFilter
        ? rows.filter((run) => run.modelId.toLowerCase().includes(modelFilter.toLowerCase()))
        : rows
    ).slice(0, limit);

    const tallies = await runResultTallies(
      scope,
      filtered.map((run) => run.id),
    );

    return {
      count: filtered.length,
      runs: filtered.map((run) => {
        const tally = tallies.get(run.id);
        const machine = parseJson<{ name?: string }>(run.machineSnapshot);

        return {
          id: run.id,
          created_at: run.createdAt.getTime(),
          finished_at: run.finishedAt?.getTime() ?? null,
          machine: machine?.name ?? null,
          machine_id: run.machineId,
          model: run.modelId,
          params: parseJson<Record<string, unknown>>(run.params),
          comment: run.comment,
          groups: parseJson<string[]>(run.groupNames) ?? [],
          status: run.status,
          archived: run.archivedAt !== null,
          results: summaryFromTally(tally),
          avg_tokens_per_sec:
            tally?.avgRate === null || tally?.avgRate === undefined
              ? null
              : Number(tally.avgRate.toFixed(2)),
        };
      }),
    };
  },
};

const getRun: McpToolSpec = {
  name: 'get_run',
  description:
    'Fetch one run with every result: status, measurements (TTFT, duration, tokens, tokens/s), manual rating and the response text. Use this to poll a running execution and to read the outcome.',
  readOnly: true,
  inputSchema: {
    type: 'object',
    properties: {
      customer: CUSTOMER_ARG,
      run_id: { type: 'integer', description: 'Run id.' },
      include_responses: {
        type: 'boolean',
        description: 'Include response text. Default true.',
      },
      max_response_chars: {
        type: 'integer',
        description: `Truncate each response to this many characters. Default ${DEFAULT_RESPONSE_CHARS}; 0 means no limit (get_run_result also returns one in full).`,
      },
      rating: {
        type: 'string',
        enum: ['good', 'meh', 'bad', 'unrated'],
        description: 'Only results with this manual verdict.',
      },
    },
    required: ['run_id'],
  },
  handler: async (args: ToolArgs, ctx: McpCallContext) => {
    const scope = await resolveMcpScope(args, ctx.source);
    const runId = requireInteger(args, 'run_id');
    const run = await loadRun(scope, runId);
    const includeResponses = optionalBoolean(args, 'include_responses', true);
    const maxChars = optionalInteger(args, 'max_response_chars') ?? DEFAULT_RESPONSE_CHARS;
    const ratingFilter = optionalEnum(args, 'rating', [
      'good',
      'meh',
      'bad',
      'unrated',
    ] as const);

    const rows = await listRunResults(scope, runId);

    const machine = parseJson<Record<string, unknown>>(run.machineSnapshot);
    const visible = rows.filter((row) => {
      if (!ratingFilter) return true;
      if (ratingFilter === 'unrated') return row.rating === null;
      return row.rating === ratingFilter;
    });

    const executing = await isRunExecuting(run.id);

    return {
      run: {
        id: run.id,
        created_at: run.createdAt.getTime(),
        started_at: run.startedAt?.getTime() ?? null,
        finished_at: run.finishedAt?.getTime() ?? null,
        machine: machine?.name ?? null,
        machine_id: run.machineId,
        base_url: machine?.base_url ?? null,
        model: run.modelId,
        params: parseJson<Record<string, unknown>>(run.params),
        llm_info: parseLlmInfo(run.llmInfo),
        comment: run.comment,
        groups: parseJson<string[]>(run.groupNames) ?? [],
        status: run.status,
        archived: run.archivedAt !== null,
        executing,
        results: summarizeResults(rows),
      },
      results: visible.map((row) => {
        const response = includeResponses
          ? truncate(row.responseText, maxChars)
          : { text: null, truncated: false };
        const snapshot = parseToolsSnapshot(row.toolsSnapshot);

        return {
          result_id: row.id,
          prompt_id: row.promptId,
          group: row.groupName,
          title: row.promptTitle,
          status: row.status,
          error: row.error,
          expected_output: row.expectedOutput,
          response: response.text,
          response_truncated: response.truncated,
          rating: row.rating,
          rating_note: row.ratingNote,
          metrics: {
            ttft_ms: row.ttftMs,
            duration_ms: row.durationMs,
            prompt_tokens: row.promptTokens,
            completion_tokens: row.completionTokens,
            tokens_per_sec: row.tokensPerSec,
            tokens_estimated: row.tokensEstimated,
          },
          tool_mode: row.toolMode,
          ...(row.toolMode === 'none'
            ? {}
            : {
                tools_offered: snapshotToolNames(snapshot),
                turn_count: row.turnCount,
                tool_call_count: row.toolCallCount,
                stopped_reason: row.stoppedReason,
              }),
        };
      }),
    };
  },
};

const getRunResult: McpToolSpec = {
  name: 'get_run_result',
  description:
    'Fetch one result in full: the frozen prompt and system prompt it was sent, the untruncated response, and for a tool test the whole transcript (every tool call, its arguments and what came back) with per-turn metrics.',
  readOnly: true,
  inputSchema: {
    type: 'object',
    properties: {
      customer: CUSTOMER_ARG,
      result_id: {
        type: 'integer',
        description: 'Result id, as returned by get_run (result_id).',
      },
      include_transcript: {
        type: 'boolean',
        description: 'Include the tool-call transcript. Default true.',
      },
    },
    required: ['result_id'],
  },
  handler: async (args: ToolArgs, ctx: McpCallContext) => {
    const id = requireInteger(args, 'result_id');
    const row = await getRunResultRow(await resolveMcpScope(args, ctx.source), id);
    if (!row) {
      throw new McpToolError(`No run result with id ${id}.`);
    }

    const includeTranscript = optionalBoolean(args, 'include_transcript', true);
    const snapshot = parseToolsSnapshot(row.toolsSnapshot);

    return {
      result: {
        result_id: row.id,
        run_id: row.runId,
        prompt_id: row.promptId,
        group: row.groupName,
        title: row.promptTitle,
        prompt_text: row.promptText,
        system_prompt_text: row.systemPromptText,
        expected_output: row.expectedOutput,
        status: row.status,
        error: row.error,
        response: row.responseText,
        rating: row.rating,
        rating_note: row.ratingNote,
        metrics: {
          ttft_ms: row.ttftMs,
          duration_ms: row.durationMs,
          prompt_tokens: row.promptTokens,
          completion_tokens: row.completionTokens,
          tokens_per_sec: row.tokensPerSec,
          tokens_estimated: row.tokensEstimated,
        },
        started_at: row.startedAt?.getTime() ?? null,
        finished_at: row.finishedAt?.getTime() ?? null,
        tool_mode: row.toolMode,
        ...(row.toolMode === 'none'
          ? {}
          : {
              tool_choice: row.toolChoice,
              max_turns: row.maxTurns,
              tools_offered: snapshot.map((entry) => ({
                name: entry.definition.function.name,
                toolset: entry.toolsetName,
                source: entry.source,
              })),
              turn_count: row.turnCount,
              tool_call_count: row.toolCallCount,
              stopped_reason: row.stoppedReason,
              turns: parseJson<unknown[]>(row.turnsJson),
              ...(includeTranscript
                ? { transcript: parseJson<unknown[]>(row.transcriptJson) }
                : {}),
            }),
      },
    };
  },
};

/**
 * The rating vocabulary as it travels on the wire.
 *
 * `unrated` is how a caller clears a verdict. JSON-RPC cannot usefully
 * distinguish "key absent" from "key present and null" by the time it reaches
 * `optionalEnum`, and an explicit word beats that ambiguity — it also matches
 * what the UI already calls the empty state.
 */
const RATING_ARGS = ['good', 'meh', 'bad', 'unrated'] as const;

/**
 * Writes the same `rating` column the UI writes, with no provenance flag — a
 * rating set here is deliberately indistinguishable from one clicked by hand.
 * Judging policy therefore lives entirely in the caller, which is the point: the
 * rubric is already in `expected_output`, and most of it (canary strings, whether
 * a given tool was called) can be checked mechanically without a judge model.
 *
 * Two guards matter for an automated loop:
 * - A row still `pending`/`running` is refused. `execute_run` is fire-and-forget,
 *   so a grading loop can easily outrun it and would otherwise leave a verdict on
 *   a row that had not answered yet.
 * - Omitting `note` leaves an existing note untouched, exactly like the UI's
 *   rating buttons; passing an empty string clears it.
 */
const setRatingTool: McpToolSpec = {
  name: 'set_rating',
  description:
    'Set or clear one result\'s manual verdict: "good", "meh", "bad", or "unrated" to clear it. Writes the same column the UI writes, so nothing afterwards distinguishes this from a hand-clicked rating — record why in `note`. The grading rubric for a result is its `expected_output` (see get_run_result); much of it is mechanically checkable (a canary string in `response`, or whether a given tool appears in the transcript) and needs no judge model. Results that have not answered yet (pending/running) are refused. Omitting `note` keeps any existing note; passing "" clears it.',
  inputSchema: {
    type: 'object',
    properties: {
      customer: CUSTOMER_ARG,
      result_id: {
        type: 'integer',
        description: 'Result id, from get_run (result_id) or get_run_result.',
      },
      rating: {
        type: 'string',
        enum: [...RATING_ARGS],
        description:
          'good = would ship this. meh = not wrong, but not good enough — often a sign the prompt needs work rather than the model. bad = wrong or unusable. unrated = clear the verdict.',
      },
      note: {
        type: 'string',
        description:
          'Why this verdict. Shown beside the rating in the UI. Worth stating which check decided it, since the rating itself carries no record of having been set by an agent.',
      },
    },
    required: ['result_id', 'rating'],
  },
  handler: async (args: ToolArgs, ctx: McpCallContext) => {
    const resultId = requireInteger(args, 'result_id');
    const rating = optionalEnum(args, 'rating', RATING_ARGS);
    if (rating === null) {
      throw new McpToolError(`"rating" is required and must be one of: ${RATING_ARGS.join(', ')}.`);
    }

    const scope = await resolveMcpScope(args, ctx.source);
    const row = await getRunResultRow(scope, resultId);

    if (!row) {
      throw new McpToolError(`No run result with id ${resultId}.`);
    }
    if (row.status === 'pending' || row.status === 'running') {
      throw new McpToolError(
        `Result ${resultId} ("${row.promptTitle}") is still ${row.status}, so there is nothing to judge yet. Poll get_run until it reports ok or error.`,
      );
    }

    const values: { rating: Rating | null; ratingNote?: string | null } = {
      rating: rating === 'unrated' ? null : rating,
    };
    if (hasKey(args, 'note')) {
      values.ratingNote = optionalText(args, 'note');
    }

    const updated = await rateResult(scope, resultId, values);
    if (!updated) {
      throw new McpToolError(`No run result with id ${resultId}.`);
    }

    // The run's tally, so a grading loop can watch its own progress (and spot
    // what it has not reached yet) without a second call.
    const siblings = await listResultRatings(scope, row.runId);

    revalidateRuns(row.runId);

    return {
      result: {
        result_id: row.id,
        run_id: row.runId,
        title: row.promptTitle,
        status: row.status,
        rating: updated.rating,
        rating_note: updated.ratingNote,
      },
      run: {
        run_id: row.runId,
        ratings: countRatings(siblings.map((sibling) => sibling.rating)),
      },
    };
  },
};

export const RUN_TOOLS: readonly McpToolSpec[] = [
  listMachines,
  createRunTool,
  executeRunTool,
  listRuns,
  getRun,
  getRunResult,
  setRatingTool,
];
