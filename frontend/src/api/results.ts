// `/api/results/matrix` — the comparison matrix, both pivots, in one payload.
// Built directly against `backend/app/api/results.py` and the dataclasses in
// `backend/app/services/compare.py`, so every
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
  endpoint_name: string
  status: string
  /** Archived runs are kept out of the picker unless already selected. */
  archived: boolean
  created_at: string
  group_names: string[]
  /** Raw `runs.params` JSON string, or null for server defaults — the same
   * shape `formatParams` renders on a run's own page. */
  params: string | null
  /** The note whoever started the run left on it, or null. */
  comment: string | null
  good: number
  meh: number
  bad: number
  ok: number
  error: number
  avg_rate: number | null
  /** Sum of `duration_ms` over the run's own results — model generation time
   * only, tool wait excluded, so a high tok/s can't hide an over-reasoning
   * model's actual cost in time. `null` when nothing was measured. */
  total_duration_ms: number | null
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
  /** The committed version each slot's prompt was at, when that draft was
   * clean at run creation. Null = a dirty draft, or an empty slot. One per
   * slot: the two drafts are independent. */
  system_prompt_version_id: number | null
  task_prompt_version_id: number | null
  sort_order: number
  group_name: string
  test_case_title: string
  /** The test case's own `content` — the data half of the user message. */
  test_case_text: string | null
  /** The system prompt's text as frozen into the row; compared on its own. */
  system_prompt_text: string | null
  /** The task prompt's text as frozen into the row; compared on its own, which
   * is what lets "the instruction changed" and "the data changed" be two
   * different drift sentences. */
  task_prompt_text: string | null
  /** The rubric as frozen into this row — never rendered per cell. The row
   * carries the copy its cells agree on (`CompareRowView.expected_output`);
   * this is only what that decision is made from, server-side. */
  expected_output: string | null
  /** Raw `tools_snapshot` JSON string, or null. */
  tools_snapshot: string | null
  tool_mode: ToolMode
  tool_choice: ToolChoice | null
  max_turns: number
  /** Raw `runs.params` JSON string, or null for server defaults. */
  run_params: string | null
  status: ResultStatus
  response_text: string | null
  /** Null when the model inlined `<think>` tags instead — then the thinking is
   * inside `response_text`. `lib/thinking.ts` resolves both shapes. */
  reasoning_text: string | null
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
  test_case_text: string | null
  cells: (CompareCellView | null)[]
  /** The rubric every cell of this row froze — `null` both when there is no
   * rubric and when the cells disagree about it, in which case `drift` carries
   * `"expected output"` instead. Computed server-side
   * (`app.services.compare.shared_expected_output`) for the same reason
   * `drift` is: one answer to "identical across the row", under one
   * normalization. */
  expected_output: string | null
  /** The rubric the live test case carries **today**, when that is something
   * `expected_output` above does not already say: it was edited since these
   * runs, or there is no frozen copy to show. `null` otherwise, and always
   * when the test case itself is gone. Set in *both* pivots
   * (`app.services.compare.annotate_live_rubric`) — the model never saw the
   * rubric, so an edit does not invalidate a result, it moves the standard the
   * result is graded by, which is worth knowing about a hand-picked pair of
   * runs as much as about a model column. */
  live_expected_output: string | null
  /** Whether the live rubric differs from the one this row's cells froze —
   * only ever true when there *was* a single frozen rubric to differ from, so
   * "added after the runs" and "the cells disagree" are both false here. */
  rubric_edited_since: boolean
  /** Conditions not held constant across this row's cells. The three frozen
   * texts are named separately — `"system prompt"`, `"task prompt"`,
   * `"test case text"` — alongside `"expected output"`, `"tools"`,
   * `"tool mode"`, `"tool choice"`, `"params"` and `"max turns"`; model mode
   * adds `"<part> edited since"`, for a *sent* part rewritten after every
   * compared run and for the rubric, which is not sent and so carries the
   * different meaning `live_expected_output` describes. Computed server-side
   * (`app.services.compare.describe_row_drift`); nothing here re-derives it. */
  drift: string[]
}

/** One selectable model column, as the picker and column headers show it. */
export interface ModelColumnView {
  key: string
  model_id: string
  endpoint_name: string
  run_count: number
  latest_run_at: string
  test_case_count: number
  good: number
  meh: number
  bad: number
  avg_rate: number | null
  /** Sum of `duration_ms` over this column's `ok` results, tallied across
   * every run of the model rather than read off any one run. */
  total_duration_ms: number | null
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
  /** Sum of `duration_ms` over the cells **on screen** — same "not a whole-run
   * total" caveat as the rest of this tally. */
  total_duration_ms: number | null
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
  /** `<endpoint_id>|<model_id>` column keys for model mode — format unchanged
   * by the machines→endpoints rename; a shared link is a contract. */
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
    // escaping.
    if (query.runs && query.runs.length > 0) params.set('runs', query.runs.join(','))
    for (const key of query.models ?? []) params.append('models', key)
    if (query.group && query.group.length > 0) params.set('group', query.group.join(','))
    const qs = params.toString()
    return api.get<MatrixResponse>(qs.length > 0 ? `/results/matrix?${qs}` : '/results/matrix')
  },
}
