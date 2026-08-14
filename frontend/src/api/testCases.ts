// Contract this is built against (Task 3.4, backend/app/api/{test_cases,
// test_groups}.py — not yet landed alongside this task; see the plan's
// Task 3.4 section and backend/app/repos/test_cases.py + backend/app/models/
// test_cases.py, which this mirrors field-for-field). Assumed shape:
//
//   GET    /api/test-groups                    -> TestGroup[]   (test_case_count embedded)
//   POST   /api/test-groups                      TestGroupInput -> TestGroup
//   PUT    /api/test-groups/{id}                  TestGroupInput -> TestGroup (full
//                                                   replacement except `sort_order`,
//                                                   which is patch-like: omit it and
//                                                   the stored order survives, so a
//                                                   rename cannot silently reset it)
//   DELETE /api/test-groups/{id}                 -> (204; cascades its test cases)
//
//   GET    /api/test-cases?group_id=            -> TestCase[]   (all test cases,
//                                                   narrowed to one group when
//                                                   group_id is given; otherwise
//                                                   grouped by group_id, per
//                                                   repos/test_cases.py's
//                                                   `list_test_cases`)
//   POST   /api/test-cases                        TestCaseInput -> TestCase
//   GET    /api/test-cases/{id}                  -> TestCase
//   PATCH  /api/test-cases/{id}                   Partial<TestCaseInput> -> TestCase (patch semantics,
//                                                   per the plan's "update_test_case (patch
//                                                   semantics + post-patch tool-config check)")
//   DELETE /api/test-cases/{id}                  -> (204)
//
//   POST   /api/test-cases/effective-prompt
//          { prompt_id: number | null, mode: PromptMode, custom_text: string | null }
//          -> { content: string | null }
//
// `toolset_ids` travels on `TestCase`/`TestCaseInput` directly rather than
// through a separate link endpoint — `create_test_case`/`update_test_case`
// are assumed to call `repos/test_cases.py`'s `replace_toolset_links`
// internally, the same way `createRun`'s snapshot did not need its own tool
// route in the old app. `assert_tool_config` (`backend/app/services/
// tool_config.py`) is the server-side authority refusing "no enabled tools"
// or a duplicate tool name across selected toolsets; `collectToolNameCollisions`
// in `../lib/tools.ts` mirrors it client-side only for instant feedback while
// editing, same reasoning as the effective-prompt preview below.
//
// Deviation flagged for reconciliation with Task 3.4: `previewEffectivePrompt`
// below is included for contract completeness (the same reason `promptsApi`
// ships `setBaseline` unused by Task 3.6) but `TestCaseEditView` does not call
// it — the live preview is computed purely client-side by
// `../lib/effectivePrompt.ts`, a byte-for-byte port of the same pure function
// the backend endpoint wraps (`resolve_effective_prompt`), so the preview
// updates on every keystroke with no round trip, matching the old React
// editor's behavior (`git show master:src/components/prompts/prompt-editor.tsx`,
// which imported `resolveEffectiveSystemPrompt` directly rather than calling
// an API).
import { api } from './client'

export type PromptMode = 'append' | 'override'
export type ToolMode = 'none' | 'definitions' | 'execute'
export type ToolChoice = 'auto' | 'required' | 'none'

export interface TestGroup {
  id: number
  name: string
  description: string | null
  sort_order: number
  created_at: string
  test_case_count: number
}

export interface TestGroupInput {
  name: string
  description?: string | null
  sort_order?: number
}

export interface TestCase {
  id: number
  group_id: number
  title: string
  /** The user message sent to the model. */
  content: string
  /** The rubric. Never sent to the model. */
  expected_output: string | null
  /** The prompt asset this case runs against, or `null` for no base prompt. */
  prompt_id: number | null
  mode: PromptMode
  custom_text: string | null
  tool_mode: ToolMode
  tool_choice: ToolChoice | null
  max_turns: number
  sort_order: number
  created_at: string
  updated_at: string
  toolset_ids: number[]
}

export interface TestCaseInput {
  group_id: number
  title: string
  content: string
  expected_output?: string | null
  prompt_id?: number | null
  mode?: PromptMode
  custom_text?: string | null
  tool_mode?: ToolMode
  tool_choice?: ToolChoice | null
  max_turns?: number
  toolset_ids?: number[]
}

export interface EffectivePromptPreview {
  /** `null` when the resolved prompt is empty or whitespace-only — i.e. the
   * run sends no system message at all. */
  content: string | null
}

export const testGroupsApi = {
  list: () => api.get<TestGroup[]>('/test-groups'),
  create: (input: TestGroupInput) => api.post<TestGroup>('/test-groups', input),
  update: (id: number, input: TestGroupInput) => api.put<TestGroup>(`/test-groups/${id}`, input),
  remove: (id: number) => api.delete<void>(`/test-groups/${id}`),
}

export const testCasesApi = {
  list: (groupId?: number) =>
    api.get<TestCase[]>(groupId === undefined ? '/test-cases' : `/test-cases?group_id=${groupId}`),
  get: (id: number) => api.get<TestCase>(`/test-cases/${id}`),
  create: (input: TestCaseInput) => api.post<TestCase>('/test-cases', input),
  update: (id: number, input: Partial<TestCaseInput>) =>
    api.patch<TestCase>(`/test-cases/${id}`, input),
  remove: (id: number) => api.delete<void>(`/test-cases/${id}`),
  previewEffectivePrompt: (input: {
    prompt_id: number | null
    mode: PromptMode
    custom_text: string | null
  }) => api.post<EffectivePromptPreview>('/test-cases/effective-prompt', input),
}
