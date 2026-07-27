import type { RunResultStatus, RunStatus } from '@/lib/run-events';

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
  comment: string | null;
  groupNames: string[];
  status: RunStatus;
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
  rating: 'good' | 'bad' | null;
  ratingNote: string | null;
}
