// Contract this is built against (backend/app/api/{test_cases,test_groups}.py
// and backend/app/repos/test_cases.py + backend/app/models/test_cases.py,
// which this mirrors field-for-field):
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
//   PATCH  /api/test-cases/{id}                   Partial<TestCaseInput> -> TestCase (patch
//                                                   semantics + post-patch tool-config check)
//   DELETE /api/test-cases/{id}                  -> (204)
//
// A test case holds **no prompt text of its own** (prompt-kinds spec): it
// references up to two prompt assets by slot — `system_prompt_id` (sent as the
// system message) and `task_prompt_id` (sent at the head of the user message) —
// plus its own `content`, the data half of that user message. Both slots are
// checked server-side by `app.repos.prompts.assert_prompt_slot`: same workspace
// **and** matching `kind`, so a `task` prompt in the system slot is a 400.
// `POST /api/test-cases/effective-prompt` is gone with `mode`/`custom_text` —
// there is nothing left to derive, and the editor's preview is a client-side
// concatenation of two texts it already fetched.
//
// `toolset_ids` travels on `TestCase`/`TestCaseInput` directly rather than
// through a separate link endpoint — `create_test_case`/`update_test_case`
// are assumed to call `repos/test_cases.py`'s `replace_toolset_links`
// internally. `assert_tool_config` (`backend/app/services/
// tool_config.py`) is the server-side authority refusing "no enabled tools"
// or a duplicate tool name across selected toolsets; `collectToolNameCollisions`
// in `../lib/tools.ts` mirrors it client-side only for instant feedback while
// editing, same reasoning as the assembled-message preview in the editor.
import { api } from './client'

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
  /** The data half of the user message. Nullable: a task prompt can be the
   * whole user message on its own ("this prompt takes no input"). */
  content: string | null
  /** The rubric. Never sent to the model. */
  expected_output: string | null
  /** The `system`-kind prompt asset sent as the system message, or `null`. */
  system_prompt_id: number | null
  /** That prompt's current name, resolved server-side so the list never
   * renders a bare id. `null` alongside `system_prompt_id === null`, or when
   * the prompt has since been deleted (`SET NULL`). */
  system_prompt_name: string | null
  /** The `task`-kind prompt asset sent at the head of the user message. */
  task_prompt_id: number | null
  /** Same as `system_prompt_name`, for the task slot. */
  task_prompt_name: string | null
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
  /** Optional here, but not free: the server refuses a case where **both**
   * `task_prompt_id` and `content` resolve to blank, since that request would
   * carry no user message at all (`assert_user_message`, a 400). */
  content?: string | null
  expected_output?: string | null
  system_prompt_id?: number | null
  task_prompt_id?: number | null
  tool_mode?: ToolMode
  tool_choice?: ToolChoice | null
  max_turns?: number
  toolset_ids?: number[]
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
}
