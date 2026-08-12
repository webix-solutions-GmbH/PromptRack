import { notFound } from 'next/navigation';
import { currentScope } from '@/db/scope';
import { getRun, listRunResults } from '@/db/repo/runs';
import { parseLlmInfo } from '@/lib/llm-info';
import { parseRating } from '@/lib/rating';
import type { RunResultStatus, RunStatus } from '@/lib/run-events';
import { parseTranscript, parseTurns, type StoppedReason } from '@/lib/tool-loop';
import { parseToolsSnapshot, type ToolChoice, type ToolMode } from '@/lib/tools';
import { onPage, requireActor } from '@/lib/auth/guards';
import { canWrite } from '@/lib/auth/policy';
import { RunDetail } from '@/components/runs/run-detail';
import type { ResultView, RunView } from '@/components/runs/types';

export const dynamic = 'force-dynamic';

interface MachineSnapshot {
  name?: unknown;
  base_url?: unknown;
  cpu?: unknown;
  ram?: unknown;
  gpu?: unknown;
}

function str(value: unknown): string | null {
  return typeof value === 'string' && value.length > 0 ? value : null;
}

function parseSnapshot(raw: string): MachineSnapshot {
  try {
    const parsed: unknown = JSON.parse(raw);
    return parsed && typeof parsed === 'object' ? (parsed as MachineSnapshot) : {};
  } catch {
    return {};
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

function parseGroupNames(raw: string): string[] {
  try {
    const parsed: unknown = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.filter((v): v is string => typeof v === 'string') : [];
  } catch {
    return [];
  }
}

export default async function RunDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id: idParam } = await params;
  const id = Number(idParam);
  if (!Number.isInteger(id)) {
    notFound();
  }

  const actor = await onPage(requireActor);
  const scope = await currentScope();
  const run = await getRun(scope, id);
  if (!run) {
    notFound();
  }

  const rows = await listRunResults(scope, id);

  const snapshot = parseSnapshot(run.machineSnapshot);

  const runView: RunView = {
    id: run.id,
    machineId: run.machineId,
    machineName: str(snapshot.name) ?? '(deleted machine)',
    baseUrl: str(snapshot.base_url),
    cpu: str(snapshot.cpu),
    ram: str(snapshot.ram),
    gpu: str(snapshot.gpu),
    modelId: run.modelId,
    params: parseParams(run.params),
    llmInfo: parseLlmInfo(run.llmInfo),
    comment: run.comment,
    groupNames: parseGroupNames(run.groupNames),
    status: run.status as RunStatus,
    archivedAt: run.archivedAt?.getTime() ?? null,
    createdAt: run.createdAt.getTime(),
    startedAt: run.startedAt?.getTime() ?? null,
    finishedAt: run.finishedAt?.getTime() ?? null,
  };

  const resultViews: ResultView[] = rows.map((row) => ({
    id: row.id,
    sortOrder: row.sortOrder,
    groupName: row.groupName,
    promptTitle: row.promptTitle,
    promptText: row.promptText,
    expectedOutput: row.expectedOutput,
    systemPromptText: row.systemPromptText,
    status: row.status as RunResultStatus,
    responseText: row.responseText,
    error: row.error,
    durationMs: row.durationMs,
    ttftMs: row.ttftMs,
    promptTokens: row.promptTokens,
    completionTokens: row.completionTokens,
    tokensPerSec: row.tokensPerSec,
    tokensEstimated: row.tokensEstimated,
    rating: parseRating(row.rating),
    ratingNote: row.ratingNote,
    toolMode: row.toolMode as ToolMode,
    toolChoice: row.toolChoice as ToolChoice | null,
    maxTurns: row.maxTurns,
    toolsSnapshot: parseToolsSnapshot(row.toolsSnapshot),
    transcript: parseTranscript(row.transcriptJson),
    turns: parseTurns(row.turnsJson),
    turnCount: row.turnCount,
    toolCallCount: row.toolCallCount,
    stoppedReason: row.stoppedReason as StoppedReason | null,
  }));

  return <RunDetail run={runView} results={resultViews} canWrite={canWrite(actor.role)} />;
}
