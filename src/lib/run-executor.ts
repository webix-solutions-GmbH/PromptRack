import { and, asc, eq, inArray } from 'drizzle-orm';
import { db } from '@/db';
import { machines, runResults, runs, toolsets } from '@/db/schema';
import { LlmError } from './llm';
import { callMcpTool, parseMcpHeaders, type McpServer } from './mcp-client';
import type { RunEvent, RunStatus } from './run-events';
import { runToolLoop, type ToolExecutor } from './tool-loop';
import { parseToolArguments, parseToolsSnapshot, type SnapshotTool, type ToolChoice, type ToolMode } from './tools';

/** How often, at most, a `delta` event is pushed to the client. */
export const DELTA_THROTTLE_MS = 250;

export type EmitRunEvent = (event: RunEvent) => void;

export class RunAlreadyExecutingError extends Error {
  constructor(runId: number) {
    super(`Run ${runId} is already executing.`);
    this.name = 'RunAlreadyExecutingError';
  }
}

/**
 * Runs currently being executed by this process.
 *
 * The app is a single-user, single-process tool, so an in-memory guard is
 * enough to stop a double-clicked "Resume" (or React re-mounting the driver)
 * from running the same result twice. It intentionally does not survive a
 * restart — rows left in 'running' by a crashed process are reclaimed as
 * 'pending' at the start of the next execution of that run.
 */
const executing = new Set<number>();

export function isRunExecuting(runId: number): boolean {
  return executing.has(runId);
}

interface Endpoint {
  baseUrl: string;
  apiKey: string | null;
}

function parseSnapshotBaseUrl(snapshot: string): string | null {
  try {
    const parsed: unknown = JSON.parse(snapshot);
    const value =
      parsed && typeof parsed === 'object'
        ? (parsed as { base_url?: unknown }).base_url
        : undefined;
    return typeof value === 'string' && value.length > 0 ? value : null;
  } catch {
    return null;
  }
}

function parseParams(raw: string | null): Record<string, unknown> | null {
  if (!raw) return null;
  try {
    const parsed: unknown = JSON.parse(raw);
    return parsed && typeof parsed === 'object' ? (parsed as Record<string, unknown>) : null;
  } catch {
    return null;
  }
}

function errorMessage(err: unknown): string {
  if (err instanceof Error) return err.message;
  return 'Unknown error.';
}

/**
 * Builds the executor for a result's MCP tools.
 *
 * The endpoint and its auth are credentials, so they are read live here rather
 * than taken from the frozen snapshot — the same tradeoff already made for a
 * machine's `base_url`. Returns null when the snapshot has no MCP tools, in
 * which case nothing needs to be looked up at all.
 */
async function buildMcpExecutor(snapshot: SnapshotTool[]): Promise<ToolExecutor | null> {
  const toolsetIds = [
    ...new Set(snapshot.filter((entry) => entry.source === 'mcp').map((entry) => entry.toolsetId)),
  ];
  if (toolsetIds.length === 0) return null;

  const rows = await db
    .select({ id: toolsets.id, mcpUrl: toolsets.mcpUrl, mcpHeaders: toolsets.mcpHeaders })
    .from(toolsets)
    .where(inArray(toolsets.id, toolsetIds));

  const serverById = new Map<number, McpServer>();
  for (const row of rows) {
    if (!row.mcpUrl) continue;
    serverById.set(row.id, { url: row.mcpUrl, headers: parseMcpHeaders(row.mcpHeaders) });
  }

  const toolsetIdByName = new Map(
    snapshot.map((entry) => [entry.definition.function.name, entry.toolsetId]),
  );

  return async (call, signal) => {
    const toolsetId = toolsetIdByName.get(call.function.name);
    const server = toolsetId === undefined ? undefined : serverById.get(toolsetId);
    if (!server) {
      // The toolset was deleted, switched to manual, or lost its URL after the
      // run was created. The model hears about it and can react.
      return {
        content: JSON.stringify({
          error: `The MCP server for "${call.function.name}" is no longer configured.`,
        }),
        isError: true,
      };
    }

    // Arguments were already validated by the loop; parse again for the typed
    // object the MCP call needs.
    const parsed = parseToolArguments(call.function.arguments);
    return callMcpTool(server, call.function.name, parsed.ok ? parsed.value : {}, signal);
  };
}

/**
 * Everything a half-written result must give up when it goes back to 'pending'
 * — kept in one place so a new output column cannot be forgotten in one of the
 * three rollback paths (stale 'running' reclaim, pre-attempt reset, abort).
 */
const RESET_TO_PENDING = {
  status: 'pending',
  startedAt: null,
  finishedAt: null,
  responseText: null,
  error: null,
  transcriptJson: null,
  turnsJson: null,
  turnCount: null,
  toolCallCount: null,
  stoppedReason: null,
} as const;

/**
 * Executes every still-pending result of a run, sequentially.
 *
 * Each result is written to the database the moment it finishes, so a crash or
 * a cancelled request never loses completed work — the run simply keeps its
 * remaining 'pending' rows and can be resumed.
 */
export async function executeRun(
  runId: number,
  emit: EmitRunEvent,
  requestSignal?: AbortSignal,
): Promise<void> {
  if (executing.has(runId)) {
    throw new RunAlreadyExecutingError(runId);
  }
  executing.add(runId);

  try {
    const [run] = await db.select().from(runs).where(eq(runs.id, runId));
    if (!run) {
      throw new Error(`Run ${runId} not found.`);
    }

    // Rows stuck in 'running' are leftovers from a crashed process — once the
    // guard above is held, no other execution of this run can be live, so
    // reclaim them as 'pending' and let this execution redo them.
    await db
      .update(runResults)
      .set(RESET_TO_PENDING)
      .where(and(eq(runResults.runId, runId), eq(runResults.status, 'running')));

    const allResults = await db
      .select({ id: runResults.id, status: runResults.status })
      .from(runResults)
      .where(eq(runResults.runId, runId))
      .orderBy(asc(runResults.sortOrder), asc(runResults.id));

    const total = allResults.length;
    const pendingIds = allResults
      .filter((row) => row.status === 'pending')
      .map((row) => row.id);
    const indexById = new Map(allResults.map((row, index) => [row.id, index + 1]));

    emit({ type: 'runStart', runId, pending: pendingIds.length, total });

    if (pendingIds.length === 0) {
      emit({ type: 'runDone', runId, status: run.status as RunStatus, nothingPending: true });
      return;
    }

    // The machine row may have been edited or deleted since the run was
    // created; prefer live credentials, fall back to the snapshot's URL.
    let endpoint: Endpoint | null = null;
    if (run.machineId !== null) {
      const [machine] = await db.select().from(machines).where(eq(machines.id, run.machineId));
      if (machine) {
        endpoint = { baseUrl: machine.baseUrl, apiKey: machine.apiKey };
      }
    }
    if (!endpoint) {
      const snapshotUrl = parseSnapshotBaseUrl(run.machineSnapshot);
      if (!snapshotUrl) {
        throw new Error('The machine for this run no longer exists and its snapshot has no base URL.');
      }
      endpoint = { baseUrl: snapshotUrl, apiKey: null };
    }

    const params = parseParams(run.params);

    await db
      .update(runs)
      .set({ status: 'running', startedAt: run.startedAt ?? Date.now(), finishedAt: null })
      .where(eq(runs.id, runId));

    let aborted = false;
    let succeeded = 0;
    let attempted = 0;
    let connectionErrors = 0;

    for (const resultId of pendingIds) {
      if (requestSignal?.aborted) {
        aborted = true;
        emit({ type: 'aborted', resultId: null });
        break;
      }

      const [result] = await db.select().from(runResults).where(eq(runResults.id, resultId));
      if (!result || result.status !== 'pending') continue;

      await db
        .update(runResults)
        .set({ ...RESET_TO_PENDING, status: 'running', startedAt: Date.now() })
        .where(eq(runResults.id, resultId));

      emit({
        type: 'resultStart',
        resultId,
        index: indexById.get(resultId) ?? 0,
        total,
      });

      const snapshot = parseToolsSnapshot(result.toolsSnapshot);
      const toolRun = snapshot.length > 0 && result.toolMode !== 'none';
      const executeTool =
        result.toolMode === 'execute' ? await buildMcpExecutor(snapshot) : null;

      let lastDeltaAt = 0;
      const startedAt = Date.now();
      attempted += 1;

      try {
        const outcome = await runToolLoop({
          baseUrl: endpoint.baseUrl,
          apiKey: endpoint.apiKey,
          model: run.modelId,
          params,
          signal: requestSignal,
          systemPrompt: result.systemPromptText,
          userMessage: result.promptText,
          snapshot,
          toolMode: result.toolMode as ToolMode,
          toolChoice: result.toolChoice as ToolChoice | null,
          maxTurns: result.maxTurns,
          ...(executeTool ? { executeTool } : {}),
          onTurnStart: (turn) => {
            if (!toolRun) return;
            // Each turn streams its own text, so the throttle window restarts
            // with it — otherwise a turn that begins right after a delta would
            // stay silent until the throttle expired.
            lastDeltaAt = 0;
            emit({ type: 'turnStart', resultId, turn });
          },
          onDelta: (turn, textSoFar) => {
            const now = Date.now();
            if (now - lastDeltaAt < DELTA_THROTTLE_MS) return;
            lastDeltaAt = now;
            // A plain prompt emits exactly the event shape it always did.
            emit(
              toolRun
                ? { type: 'delta', resultId, text: textSoFar, turn }
                : { type: 'delta', resultId, text: textSoFar },
            );
          },
          onToolCalls: (turn, calls) => emit({ type: 'toolCall', resultId, turn, calls }),
          onToolResult: (turn, message) =>
            emit({ type: 'toolResult', resultId, turn, message }),
        });

        await db
          .update(runResults)
          .set({
            status: 'ok',
            responseText: outcome.text,
            error: null,
            durationMs: outcome.durationMs,
            ttftMs: outcome.ttftMs,
            promptTokens: outcome.promptTokens,
            completionTokens: outcome.completionTokens,
            tokensPerSec: outcome.tokensPerSec,
            tokensEstimated: outcome.tokensEstimated,
            // Only tool runs carry transcript detail; a plain prompt keeps
            // these null so its result card renders exactly as it always did.
            transcriptJson: toolRun ? JSON.stringify(outcome.transcript) : null,
            turnsJson: toolRun ? JSON.stringify(outcome.turns) : null,
            turnCount: toolRun ? outcome.turns.length : null,
            toolCallCount: toolRun ? outcome.toolCallCount : null,
            stoppedReason: toolRun ? outcome.stoppedReason : null,
            finishedAt: Date.now(),
          })
          .where(eq(runResults.id, resultId));

        succeeded += 1;
        emit({
          type: 'resultDone',
          resultId,
          text: outcome.text,
          metrics: {
            durationMs: outcome.durationMs,
            ttftMs: outcome.ttftMs,
            promptTokens: outcome.promptTokens,
            completionTokens: outcome.completionTokens,
            tokensPerSec: outcome.tokensPerSec,
            tokensEstimated: outcome.tokensEstimated,
            turnCount: toolRun ? outcome.turns.length : null,
            toolCallCount: toolRun ? outcome.toolCallCount : null,
          },
          ...(toolRun
            ? {
                transcript: outcome.transcript,
                turns: outcome.turns,
                stoppedReason: outcome.stoppedReason,
              }
            : {}),
        });
      } catch (err) {
        const isAbort =
          requestSignal?.aborted || (err instanceof LlmError && err.kind === 'aborted');

        if (isAbort) {
          // The client hung up. Roll the result back to 'pending' so that
          // "Resume" picks it up again instead of leaving a half-written row.
          await db
            .update(runResults)
            .set(RESET_TO_PENDING)
            .where(eq(runResults.id, resultId));

          attempted -= 1;
          aborted = true;
          emit({ type: 'aborted', resultId });
          break;
        }

        if (err instanceof LlmError && err.isConnectionLevel) {
          connectionErrors += 1;
        }

        const message = errorMessage(err);
        await db
          .update(runResults)
          .set({
            status: 'error',
            error: message,
            durationMs: Date.now() - startedAt,
            finishedAt: Date.now(),
          })
          .where(eq(runResults.id, resultId));

        emit({ type: 'resultError', resultId, error: message });
      }
    }

    if (aborted) {
      await db
        .update(runs)
        .set({ status: 'pending', finishedAt: null })
        .where(eq(runs.id, runId));
      emit({ type: 'runDone', runId, status: 'pending' });
      return;
    }

    const remaining = await db
      .select({ id: runResults.id })
      .from(runResults)
      .where(and(eq(runResults.runId, runId), eq(runResults.status, 'pending')));

    // 'failed' is reserved for "the machine was never reachable": every result
    // we tried died at the connection level and nothing succeeded. A run where
    // the model merely errored on some prompts is still a completed run.
    const everythingUnreachable =
      succeeded === 0 && attempted > 0 && connectionErrors === attempted;
    const status: RunStatus =
      remaining.length > 0 ? 'pending' : everythingUnreachable ? 'failed' : 'completed';

    await db
      .update(runs)
      .set({
        status,
        finishedAt: status === 'pending' ? null : Date.now(),
      })
      .where(eq(runs.id, runId));

    emit({ type: 'runDone', runId, status });
  } finally {
    executing.delete(runId);
  }
}
