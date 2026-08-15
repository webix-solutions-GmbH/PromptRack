// `/api/runs` and `/api/results` — creating, listing, executing a run, and
// rating its results. Built directly against `backend/app/api/runs.py` and
// `backend/app/services/run_events.py` (Tasks 4.3/4.4, already landed), so
// every shape below is read off those files rather than assumed.
//
// The one endpoint that does not fit `../api/client`'s JSON request/response
// wrapper is `POST /runs/{id}/execute`: it streams NDJSON (one JSON object
// per line, `Content-Type: application/x-ndjson`), so `executeRun` below
// does its own `fetch` + `ReadableStream` reader — port of
// `git show legacy-nextjs:src/components/runs/run-detail.tsx`'s `start()`.
import { ApiError } from './client'
import { api } from './client'
import type { ToolChoice, ToolMode } from './testCases'

export type RunStatus = 'pending' | 'running' | 'completed' | 'failed'
export type ResultStatus = 'pending' | 'running' | 'ok' | 'error'
export type StoppedReason = 'stop' | 'max_turns' | 'definitions_only'

export interface EndpointSnapshot {
  name?: string | null
  base_url?: string | null
  cpu?: string | null
  ram?: string | null
  gpu?: string | null
}

export interface LlmInfo {
  server: string | null
  version: string | null
  details: Record<string, string>
}

export interface RunView {
  id: number
  endpoint_id: number | null
  endpoint_snapshot: EndpointSnapshot | null
  model_id: string
  params: Record<string, unknown> | null
  comment: string | null
  group_names: string[]
  llm_info: LlmInfo | null
  status: RunStatus
  archived_at: string | null
  created_at: string
  started_at: string | null
  finished_at: string | null
}

/** One entry of the OpenAI-compatible `tools` array, frozen at run creation. */
export interface ToolDefinition {
  type: string
  function?: { name?: string; description?: string; parameters?: unknown }
}

export interface SnapshotTool {
  definition: ToolDefinition
  source: 'manual' | 'mcp'
  toolset_id: number
  toolset_name: string
  mock_response: string | null
}

export interface ToolCall {
  id: string
  name: string
  arguments: string
}

/** A persisted transcript entry — the wire shape plus display-only
 * annotations (`turn`, tool timing). Port of `TranscriptMessage.to_json()`
 * in `backend/app/services/tool_loop.py`. */
export interface TranscriptMessage {
  role: 'system' | 'user' | 'assistant' | 'tool'
  content: string
  tool_calls?: ToolCall[]
  tool_call_id?: string
  name?: string
  turn?: number
  tool_duration_ms?: number
  tool_is_error?: boolean
}

/** One model turn's metrics — `turns_json`'s element. */
export interface TurnMetrics {
  index: number
  ttft_ms: number | null
  duration_ms: number
  prompt_tokens: number | null
  completion_tokens: number
  tokens_estimated: boolean
  finish_reason: string | null
  tool_call_count: number
}

export interface RunResultView {
  id: number
  run_id: number
  test_case_id: number | null
  /** The committed version each slot's draft matched, if any — attribution,
   * not selection. Null means that slot tested a dirty draft, or is empty.
   * One per slot: the two prompts are versioned independently. */
  system_prompt_version_id: number | null
  task_prompt_version_id: number | null
  sort_order: number

  group_name: string
  test_case_title: string
  /** The test case's own `content` — the data half of the user message, frozen
   * on its own. The task prompt is the other half; the executor joins them
   * (`app.services.message_assembly.user_message`). */
  test_case_text: string | null
  expected_output: string | null
  /** The system prompt's draft text, verbatim, as it was at run creation. */
  system_prompt_text: string | null
  /** The task prompt's draft text, verbatim. Kept apart from `test_case_text`
   * so `/results` can say *the task prompt changed* rather than *the user
   * message changed*. */
  task_prompt_text: string | null
  tools_snapshot: SnapshotTool[] | null
  tool_mode: ToolMode
  tool_choice: ToolChoice | null
  max_turns: number

  status: ResultStatus
  response_text: string | null
  transcript: TranscriptMessage[] | null
  turns: TurnMetrics[] | null
  turn_count: number | null
  tool_call_count: number | null
  stopped_reason: StoppedReason | null
  error: string | null

  duration_ms: number | null
  ttft_ms: number | null
  prompt_tokens: number | null
  completion_tokens: number | null
  tokens_per_sec: number | null
  tokens_estimated: boolean

  rating: 'good' | 'meh' | 'bad' | null
  rating_note: string | null
  started_at: string | null
  finished_at: string | null
}

export interface RunDetail extends RunView {
  results: RunResultView[]
}

export interface RunCreateInput {
  endpoint_id: number
  model_id: string
  group_ids: number[]
  temperature?: number | null
  max_tokens?: number | null
  comment?: string | null
}

export type ArchivedFilter = 'exclude' | 'only' | 'all'

export interface RatingInput {
  /** `'unrated'` is the wire word for "clear it" — omit the key entirely to
   * leave the existing rating untouched. */
  rating?: 'good' | 'meh' | 'bad' | 'unrated'
  /** Omit to leave an existing note untouched; `''`/`null` clears it. */
  note?: string | null
}

export const runsApi = {
  list: (opts: { archived?: ArchivedFilter; status?: string; limit?: number } = {}) => {
    const params = new URLSearchParams()
    if (opts.archived) params.set('archived', opts.archived)
    if (opts.status) params.set('status', opts.status)
    if (opts.limit !== undefined) params.set('limit', String(opts.limit))
    const query = params.toString()
    return api.get<RunView[]>(query.length > 0 ? `/runs?${query}` : '/runs')
  },
  get: (id: number) => api.get<RunDetail>(`/runs/${id}`),
  create: (input: RunCreateInput) => api.post<RunView>('/runs', input),
  archive: (id: number) => api.post<RunView>(`/runs/${id}/archive`),
  unarchive: (id: number) => api.post<RunView>(`/runs/${id}/unarchive`),
  remove: (id: number) => api.delete<void>(`/runs/${id}`),
}

export const resultsApi = {
  get: (id: number) => api.get<RunResultView>(`/results/${id}`),
  rate: (id: number, input: RatingInput) => api.patch<RunResultView>(`/results/${id}`, input),
}

// ---------------------------------------------------------------------------
// NDJSON execution stream
// ---------------------------------------------------------------------------

export interface ResultMetricsPayload {
  duration_ms: number | null
  ttft_ms: number | null
  prompt_tokens: number | null
  completion_tokens: number | null
  tokens_per_sec: number | null
  tokens_estimated: boolean
  turn_count: number | null
  tool_call_count: number | null
}

export type RunEvent =
  | { type: 'runStart'; run_id: number; pending: number; total: number }
  | { type: 'resultStart'; result_id: number; index: number; total: number }
  /** A new model turn began. Only emitted for tool runs. */
  | { type: 'turnStart'; result_id: number; turn: number }
  /** Throttled progress. `text` is the full response of this turn so far.
   * `turn` is omitted entirely on a plain (non-tool) prompt. */
  | { type: 'delta'; result_id: number; text: string; turn?: number }
  | { type: 'toolCall'; result_id: number; turn: number; calls: ToolCall[] }
  | { type: 'toolResult'; result_id: number; turn: number; message: TranscriptMessage }
  | {
      type: 'resultDone'
      result_id: number
      text: string
      metrics: ResultMetricsPayload
      /** Present for tool runs only, so the card can render without a reload. */
      transcript?: TranscriptMessage[]
      turns?: TurnMetrics[]
      stopped_reason?: StoppedReason
    }
  | { type: 'resultError'; result_id: number; error: string }
  /** The client disconnected; `result_id` (when set) was reset to `pending`. */
  | { type: 'aborted'; result_id: number | null }
  | { type: 'runDone'; run_id: number; status: RunStatus; nothing_pending?: boolean }
  | { type: 'runError'; run_id: number; error: string }

function isRunEvent(value: unknown): value is RunEvent {
  return (
    typeof value === 'object' &&
    value !== null &&
    typeof (value as { type?: unknown }).type === 'string'
  )
}

export class RunAlreadyExecutingError extends Error {
  constructor() {
    super('This run is already executing in another tab.')
    this.name = 'RunAlreadyExecutingError'
  }
}

/**
 * Streams `POST /runs/{id}/execute` and applies each NDJSON line to `onEvent`
 * as it arrives. Resolves once the stream ends (cleanly or via `signal`
 * abort); a 409 (another tab is already driving this run) rejects with
 * `RunAlreadyExecutingError` rather than emitting anything.
 */
export async function executeRun(
  runId: number,
  onEvent: (event: RunEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch(`/api/runs/${runId}/execute`, {
    method: 'POST',
    credentials: 'same-origin',
    signal,
  })

  if (response.status === 409) {
    throw new RunAlreadyExecutingError()
  }
  if (!response.ok || !response.body) {
    throw new ApiError(response.status, `Execution request failed (HTTP ${response.status}).`)
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })

    const lines = buffer.split('\n')
    buffer = lines.pop() ?? ''
    for (const line of lines) {
      if (line.trim().length === 0) continue
      try {
        const parsed: unknown = JSON.parse(line)
        if (isRunEvent(parsed)) onEvent(parsed)
      } catch {
        // Ignore a line we cannot parse rather than killing the stream.
      }
    }
  }
}
