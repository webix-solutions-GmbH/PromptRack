// `/api/results/matrix` — the comparison matrix, both pivots, in one payload.
// Built directly against `backend/app/api/results.py` and the dataclasses in
// `backend/app/services/compare.py` (Task 5.1, already landed), so every
// field below is read off those files rather than assumed. The response
// carries no "other pivot" fields as absent — they come back as empty
// arrays/lists — so a client can switch modes without branching on presence.
//
// The one-off `GET /api/results/{id}` + `PATCH` pair used by run detail
// lives in `./runs.ts` (`resultsApi`); this module is only the matrix.
import { api } from './client'
import type { Rating } from '../lib/rating'
import type { ResultStatus } from './runs'
import type { ToolChoice, ToolMode } from './testCases'

export type CompareMode = 'runs' | 'models'

export interface CompareRunView {
  id: number
  model_id: string
  machine_name: string
  status: string
  /** Archived runs are kept out of the picker unless already selected. */
  archived: boolean
  created_at: string
  group_names: string[]
  good: number
  meh: number
  bad: number
  ok: number
  error: number
  avg_rate: number | null
}

/** A newer attempt at the same test case that was *not* used as the cell —
 * model mode shows the latest *usable* result, so an endpoint that was down
 * during the most recent run cannot silently hide a good older answer. */
export interface SupersededAttempt {
  run_id: number
  status: ResultStatus
  created_at: string
}

/** One cell of the matrix: a single `run_results` row. */
export interface CompareCellView {
  id: number
  run_id: number
  run_created_at: string
  /** Opaque workspace key; only used to key the deleted-test-case text
   * fallback client-side never needs to read it. */
  scope_key: string
  test_case_id: number | null
  /** The committed prompt version this result tested, when the draft was
   * clean at run creation. Null = a dirty draft, or no prompt at all. */
  prompt_version_id: number | null
  sort_order: number
  group_name: string
  test_case_title: string
  test_case_text: string
  effective_prompt_text: string | null
  /** Raw `tools_snapshot` JSON string, or null. */
  tools_snapshot: string | null
  tool_mode: ToolMode
  tool_choice: ToolChoice | null
  max_turns: number
  /** Raw `runs.params` JSON string, or null for server defaults. */
  run_params: string | null
  status: ResultStatus
  response_text: string | null
  error: string | null
  duration_ms: number | null
  ttft_ms: number | null
  completion_tokens: number | null
  tokens_per_sec: number | null
  tokens_estimated: boolean
  rating: Rating | null
  rating_note: string | null
  /** Null on an ordinary test case; set for a tool test. */
  turn_count: number | null
  tool_call_count: number | null
  /** The tool names the model called, in order, with repeats. */
  tool_call_names: string[]
  superseded: SupersededAttempt | null
  /** Which model column this cell belongs to; empty in run mode. */
  column_key: string
}

/** One row of the matrix: a test case, plus one cell (or `null`) per column. */
export interface CompareRowView {
  key: string
  test_case_id: number | null
  group_name: string
  test_case_title: string
  test_case_text: string
  cells: (CompareCellView | null)[]
  /** Conditions not held constant across this row's cells — e.g. "tools",
   * "params", or (model mode) "test case edited since". Already computed
   * server-side; nothing here re-derives it. */
  drift: string[]
}

/** One selectable model column, as the picker and column headers show it. */
export interface ModelColumnView {
  key: string
  model_id: string
  machine_name: string
  run_count: number
  latest_run_at: string
  test_case_count: number
  good: number
  meh: number
  bad: number
  avg_rate: number | null
}

export interface GroupOption {
  id: number
  name: string
  test_case_count: number
}

/** Per-column totals over the cells **on screen** — not a whole-run total,
 * which would count test cases this comparison filtered out. */
export interface ColumnTally {
  answered: number
  good: number
  meh: number
  bad: number
  unrated: number
  avg_rate: number | null
}

export interface MatrixResponse {
  mode: CompareMode
  /** How many columns this pivot needs before a matrix means anything. */
  min_columns: number
  rows: CompareRowView[]

  // --- run mode ---
  available_runs: CompareRunView[]
  selected_run_ids: number[]
  run_columns: CompareRunView[]
  /** Archived runs the picker is hiding — the one sentence the page carries. */
  hidden_archived_runs: number

  // --- model mode ---
  available_models: ModelColumnView[]
  selected_model_keys: string[]
  model_columns: ModelColumnView[]
  column_tallies: ColumnTally[]
  groups: GroupOption[]
  selected_group_ids: number[]
  uncovered_test_cases: number
}

export interface MatrixQuery {
  mode?: CompareMode
  /** Run ids for run mode. */
  runs?: number[]
  /** `<machine_id>|<model_id>` column keys for model mode. */
  models?: string[]
  group?: number[]
}

export const resultsApi = {
  matrix: (query: MatrixQuery = {}) => {
    const params = new URLSearchParams()
    if (query.mode) params.set('mode', query.mode)
    // `runs`/`group` accept a single comma-joined value server-side
    // (`app.services.compare.parse_id_list`); `models` must stay repeated
    // params since a model id is free-form text that must never need
    // escaping (mirrors the old app's query contract).
    if (query.runs && query.runs.length > 0) params.set('runs', query.runs.join(','))
    for (const key of query.models ?? []) params.append('models', key)
    if (query.group && query.group.length > 0) params.set('group', query.group.join(','))
    const qs = params.toString()
    return api.get<MatrixResponse>(qs.length > 0 ? `/results/matrix?${qs}` : '/results/matrix')
  },
}
