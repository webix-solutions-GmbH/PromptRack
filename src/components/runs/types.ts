import type { LlmInfo } from '@/lib/llm-info';
import type { Rating } from '@/lib/rating';
import type { RunResultStatus, RunStatus } from '@/lib/run-events';
import type { StoppedReason, TranscriptMessage, TurnMetrics } from '@/lib/tool-loop';
import type { SnapshotTool, ToolChoice, ToolMode } from '@/lib/tools';

/** Serializable projection of a run, handed from the page to the client driver. */
export interface RunView {
  id: number;
  machineId: number | null;
  machineName: string;
  baseUrl: string | null;
  cpu: string | null;
  ram: string | null;
  gpu: string | null;
  modelId: string;
  params: Record<string, unknown> | null;
  llmInfo: LlmInfo | null;
  comment: string | null;
  groupNames: string[];
  status: RunStatus;
  /** Set while the run is archived — hidden from the default lists. */
  archivedAt: number | null;
  createdAt: number;
  startedAt: number | null;
  finishedAt: number | null;
}

export interface ResultView {
  id: number;
  sortOrder: number;
  groupName: string;
  promptTitle: string;
  promptText: string;
  expectedOutput: string | null;
  systemPromptText: string | null;
  status: RunResultStatus;
  responseText: string | null;
  error: string | null;
  durationMs: number | null;
  ttftMs: number | null;
  promptTokens: number | null;
  completionTokens: number | null;
  tokensPerSec: number | null;
  tokensEstimated: boolean;
  rating: Rating | null;
  ratingNote: string | null;
  /**
   * Tool detail. `toolMode` is `'none'` and everything else is null/empty for
   * an ordinary prompt, which is what keeps its card rendering unchanged.
   */
  toolMode: ToolMode;
  toolChoice: ToolChoice | null;
  maxTurns: number;
  toolsSnapshot: SnapshotTool[];
  transcript: TranscriptMessage[] | null;
  turns: TurnMetrics[];
  turnCount: number | null;
  toolCallCount: number | null;
  stoppedReason: StoppedReason | null;
}
