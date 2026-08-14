// Contract this is built against (Task 3.3, backend/app/api/prompts.py —
// not yet landed alongside this task; see the plan's Task 3.3 section, the
// spec's "Data model" §prompts/prompt_versions, and backend/app/repos/{prompts,
// prompt_versions}.py + backend/app/services/attribution.py, which this
// mirrors field-for-field). Assumed shape:
//
//   GET    /api/prompts                        -> Prompt[]
//   POST   /api/prompts                          PromptInput -> Prompt
//   GET    /api/prompts/{id}                    -> Prompt
//   PATCH  /api/prompts/{id}                     PromptDraftInput -> Prompt   (edits the draft)
//   DELETE /api/prompts/{id}                    -> (204; cascades its versions)
//   POST   /api/prompts/{id}/commit              { message } -> PromptVersion
//   GET    /api/prompts/{id}/versions           -> PromptVersion[]           (newest first)
//   GET    /api/prompts/versions/{versionId}    -> PromptVersion
//   POST   /api/prompts/{id}/deploy              { version_id } -> Prompt
//   POST   /api/prompts/{id}/restore             { version_id } -> Prompt    (copies content -> draft only; does not commit)
//   POST   /api/prompts/versions/{versionId}/baseline  { run_id } -> PromptVersion
//   GET    /api/prompts/{id}/diff?from=&to=     -> DiffResult                (from/to: a version id, or the literal "draft")
//
// `Prompt.dirty`, `head_version` and `deployed_version` are computed by the
// backend (plan: "Response for a prompt includes head_version, deployed_version,
// dirty (draft ≠ head)") — this module never re-derives dirtiness client-side,
// the same way `machines.ts` never re-derives `loaded_count`.
//
// `PromptVersion.created_by_name` and `Prompt.deployed_by_name` are resolved
// server-side (`backend/app/api/prompts.py`, `app.auth.users.list_display_names`)
// so the history panel and the editor never render a bare user id.
//
// `setBaseline` is part of the Task 3.3 contract but has no caller in this
// task's UI — Task 3.6's own action list for a version is view/diff/deploy/
// restore only; baseline-setting is a run-side workflow (Task 4.5's "Verify"
// flow). Included here for the same reason `machinesApi.test` ships alongside
// `discover` even where only one is wired into a given view: the module is the
// full client for its backend contract.
import { api } from './client'

export interface PromptVersionSummary {
  id: number
  version: number
}

/** Which channel a prompt is sent on — a property of the asset, not of a test
 * case's reference to it (prompt-kinds spec, decision 1). A `system` prompt
 * becomes the system message; a `task` prompt is the head of the user message,
 * with the test case's own `content` concatenated after it. */
export type PromptKind = 'system' | 'task'

export interface Prompt {
  id: number
  name: string
  kind: PromptKind
  /** The mutable draft — what the editor writes and what a run always tests. */
  content: string
  /** How many live test cases reference this prompt, across **both** slots.
   * Computed server-side (`app.repos.prompts.test_case_reference_counts`) so
   * the list and the editor can never disagree about whether a kind change is
   * still allowed — it is refused with a 409 while this is non-zero. */
  used_by_test_case_count: number
  /** When the pointer was moved. */
  deployed_at: string | null
  /** Who moved it — `prompts.deployed_by` resolved to a name server-side.
   * `null` when nothing is deployed yet, or that user's account is gone. */
  deployed_by_name: string | null
  created_at: string
  updated_at: string
  head_version: PromptVersionSummary | null
  /** The version claimed live at the customer. Read its `.id` for comparisons —
   * the prompt row carries no flat `deployed_version_id`. */
  deployed_version: PromptVersionSummary | null
  /** `content` differs from `head_version`'s frozen text (or nothing is
   * committed yet at all) — the editor's dirty indicator, computed server-side. */
  dirty: boolean
}

export interface PromptInput {
  name: string
  /** Optional both here and server-side: an asset can exist before its text is
   * written, and an empty draft is a legitimate starting state. */
  content?: string
  /** Omitted defaults to `'system'` server-side — the channel everything
   * authored before the prompt-kinds pivot was sent on. */
  kind?: PromptKind
}

/** `PATCH /api/prompts/{id}` — only the keys present change.
 *
 * `kind` rides on this same body, but it is the one field the server can refuse
 * for a reason unrelated to the draft (409 while test cases reference the
 * prompt), and nothing at all is written when that fires — so a caller sending
 * both must not read the refusal as "the draft was not saved". */
export interface PromptDraftInput {
  name?: string
  content?: string
  kind?: PromptKind
}

export interface PromptVersion {
  id: number
  prompt_id: number
  version: number
  content: string
  message: string
  created_at: string
  created_by: number | null
  /** The author's display name, resolved server-side. `null` alongside
   * `created_by === null`, or when that user's account has since been
   * deleted. */
  created_by_name: string | null
  baseline_run_id: number | null
}

export interface DiffResult {
  /** One unified-diff line per entry, without trailing newlines. */
  diff: string[]
}

/** A version id or the literal `"draft"`, as the diff endpoint's `from`/`to`
 * accept per the plan. */
export type DiffRef = 'draft' | number

function diffRefParam(ref: DiffRef): string {
  return ref === 'draft' ? 'draft' : String(ref)
}

export const promptsApi = {
  list: () => api.get<Prompt[]>('/prompts'),
  get: (id: number) => api.get<Prompt>(`/prompts/${id}`),
  create: (input: PromptInput) => api.post<Prompt>('/prompts', input),
  updateDraft: (id: number, input: PromptDraftInput) =>
    api.patch<Prompt>(`/prompts/${id}`, input),
  remove: (id: number) => api.delete<void>(`/prompts/${id}`),
  commit: (id: number, message: string) =>
    api.post<PromptVersion>(`/prompts/${id}/commit`, { message }),
  listVersions: (id: number) => api.get<PromptVersion[]>(`/prompts/${id}/versions`),
  getVersion: (versionId: number) => api.get<PromptVersion>(`/prompts/versions/${versionId}`),
  deploy: (id: number, versionId: number) =>
    api.post<Prompt>(`/prompts/${id}/deploy`, { version_id: versionId }),
  restore: (id: number, versionId: number) =>
    api.post<Prompt>(`/prompts/${id}/restore`, { version_id: versionId }),
  setBaseline: (versionId: number, runId: number) =>
    api.post<PromptVersion>(`/prompts/versions/${versionId}/baseline`, { run_id: runId }),
  diff: (id: number, from: DiffRef, to: DiffRef) =>
    api.get<DiffResult>(
      `/prompts/${id}/diff?from=${encodeURIComponent(diffRefParam(from))}&to=${encodeURIComponent(diffRefParam(to))}`,
    ),
}

/** The two kinds with the sentence each one's channel is worth explaining
 * with, so the list filter, the prompt editor and the test-case editor's "new
 * prompt" dialog all say the identical thing about the identical state. */
export const PROMPT_KINDS: { value: PromptKind; label: string; hint: string }[] = [
  { value: 'system', label: 'System', hint: 'Frames the model. Sent as the system message.' },
  {
    value: 'task',
    label: 'Task',
    hint: "The instruction for this call. Sent at the head of the user message, before the test case's own content.",
  },
]

/** The list/editor's one-glance "is what's live what we last verified"
 * signal (spec: "deployed v3, head is v5"). Pure so both `PromptsView` and
 * `PromptEditView` render the identical sentence for the identical state. */
export function describeVersionStatus(prompt: Prompt): string {
  if (!prompt.head_version) return 'not committed yet'
  if (!prompt.deployed_version) return `head v${prompt.head_version.version} · not deployed`
  if (prompt.deployed_version.version === prompt.head_version.version) {
    return `deployed v${prompt.head_version.version}`
  }
  return `deployed v${prompt.deployed_version.version}, head is v${prompt.head_version.version}`
}
