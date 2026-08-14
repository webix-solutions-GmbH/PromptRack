<script setup lang="ts">
// Test case create/edit — the port of the old prompt editor
// (`git show master:src/components/prompts/prompt-editor.tsx`) under the
// prompt-kinds terminology: a test case holds no prompt text of its own. It
// references up to two prompt *assets* by slot — a `system`-kind prompt sent
// as the system message and a `task`-kind prompt sent at the head of the user
// message — plus its own `content`, the data half of that user message. It
// offers any number of toolsets and carries the rubric that never reaches the
// model.
//
// One component handles both routes (`/test-cases/new` and
// `/test-cases/:id`), same shape as the old editor taking an optional
// `groupId` for creation — `props.id` absent means create.
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import Button from 'primevue/button'
import Checkbox from 'primevue/checkbox'
import Dialog from 'primevue/dialog'
import InputNumber from 'primevue/inputnumber'
import InputText from 'primevue/inputtext'
import Message from 'primevue/message'
import RadioButton from 'primevue/radiobutton'
import Select from 'primevue/select'
import Tag from 'primevue/tag'
import Textarea from 'primevue/textarea'
import { useConfirm } from 'primevue/useconfirm'
import { useToast } from 'primevue/usetoast'
import {
  testCasesApi,
  testGroupsApi,
  type TestCase,
  type TestGroup,
  type ToolChoice,
  type ToolMode,
} from '../api/testCases'
import { promptsApi, PROMPT_KINDS, type Prompt, type PromptKind } from '../api/prompts'
import { toolsetsApi, type Tool, type Toolset } from '../api/toolsets'
import { ApiError } from '../api/client'
import { collectToolNameCollisions, DEFAULT_MAX_TURNS, MAX_TURNS_LIMIT } from '../lib/tools'
import { useAuthStore } from '../stores/auth'

const props = defineProps<{ id?: string }>()
const isNew = computed(() => props.id === undefined)
const testCaseId = computed(() => (props.id === undefined ? null : Number(props.id)))

const auth = useAuthStore()
const route = useRoute()
const router = useRouter()
const confirm = useConfirm()
const toast = useToast()

const TOOL_MODES: { value: ToolMode; label: string; hint: string }[] = [
  { value: 'none', label: 'No tools', hint: 'One user message, one answer.' },
  {
    value: 'definitions',
    label: 'Definitions only',
    hint: 'Offer the tools and record what the model wanted to call. Nothing is executed.',
  },
  {
    value: 'execute',
    label: 'Execute',
    hint: 'Run each call, feed the result back, and keep going until the model answers.',
  },
]

const TOOL_CHOICE_OPTIONS: { label: string; value: ToolChoice | '' }[] = [
  { label: 'server default', value: '' },
  { label: 'auto', value: 'auto' },
  { label: 'required', value: 'required' },
  { label: 'none', value: 'none' },
]

interface FormState {
  groupId: number | null
  title: string
  content: string
  expectedOutput: string
  systemPromptId: number | null
  taskPromptId: number | null
  toolMode: ToolMode
  toolsetIds: number[]
  toolChoice: ToolChoice | ''
  maxTurns: number
}

function emptyForm(groupId: number | null): FormState {
  return {
    groupId,
    title: '',
    content: '',
    expectedOutput: '',
    systemPromptId: null,
    taskPromptId: null,
    toolMode: 'none',
    toolsetIds: [],
    toolChoice: '',
    maxTurns: DEFAULT_MAX_TURNS,
  }
}

function queryGroupId(): number | null {
  const raw = route.query.group
  const id = Number(Array.isArray(raw) ? raw[0] : raw)
  return Number.isFinite(id) && id > 0 ? id : null
}

const form = reactive<FormState>(emptyForm(queryGroupId()))

const testCase = ref<TestCase | null>(null)
const groups = ref<TestGroup[]>([])
const prompts = ref<Prompt[]>([])
const toolsets = ref<Toolset[]>([])
const toolsByToolset = ref<Record<number, Tool[]>>({})

const loading = ref(true)
const loadError = ref<string | null>(null)

function applyTestCase(row: TestCase) {
  testCase.value = row
  form.groupId = row.group_id
  form.title = row.title
  form.content = row.content ?? ''
  form.expectedOutput = row.expected_output ?? ''
  form.systemPromptId = row.system_prompt_id
  form.taskPromptId = row.task_prompt_id
  form.toolMode = row.tool_mode
  form.toolsetIds = [...row.toolset_ids]
  form.toolChoice = row.tool_choice ?? ''
  form.maxTurns = row.max_turns || DEFAULT_MAX_TURNS
}

async function load() {
  loading.value = true
  loadError.value = null
  try {
    const [groupRows, promptRows, toolsetRows] = await Promise.all([
      testGroupsApi.list(),
      promptsApi.list(),
      toolsetsApi.list(),
    ])
    groups.value = groupRows
    prompts.value = promptRows
    toolsets.value = toolsetRows

    if (testCaseId.value !== null) {
      applyTestCase(await testCasesApi.get(testCaseId.value))
    } else {
      Object.assign(form, emptyForm(queryGroupId()))
    }
  } catch (err) {
    loadError.value = err instanceof ApiError ? err.message : 'Failed to load the test case.'
  } finally {
    loading.value = false
  }
}

onMounted(load)
watch(testCaseId, load)

// --- prompt slots ------------------------------------------------------

/** Sentinel option id: picking it opens the "new prompt" dialog instead of
 * selecting anything. Negative, so it can never collide with a real row id. */
const NEW_PROMPT = -1

function promptsOfKind(kind: PromptKind): Prompt[] {
  return prompts.value.filter((prompt) => prompt.kind === kind)
}

/** The slot's own prompts plus the create-in-place item. Authoring a one-off
 * prompt used to be a textarea on this page; keeping it one click away is what
 * stops the pivot from making the common case slower (spec §Frontend,
 * "Authoring must not regress"). */
function slotOptions(kind: PromptKind): { id: number; name: string }[] {
  return [
    ...promptsOfKind(kind).map((prompt) => ({ id: prompt.id, name: prompt.name })),
    { id: NEW_PROMPT, name: '＋ New prompt…' },
  ]
}

const systemPromptOptions = computed(() => slotOptions('system'))
const taskPromptOptions = computed(() => slotOptions('task'))

/** The sentinel never lands in `form` — it opens the dialog and the select
 * keeps whatever was already chosen. */
function selectSlot(kind: PromptKind, value: number | null) {
  if (value === NEW_PROMPT) {
    openNewPrompt(kind)
    return
  }
  if (kind === 'system') form.systemPromptId = value
  else form.taskPromptId = value
}

const systemPrompt = computed<Prompt | null>(
  () => prompts.value.find((prompt) => prompt.id === form.systemPromptId) ?? null,
)
const taskPrompt = computed<Prompt | null>(
  () => prompts.value.find((prompt) => prompt.id === form.taskPromptId) ?? null,
)

// The selected prompt's draft, read without leaving the editor. No fetch: both
// slots' full text arrived with `promptsApi.list()` for the selects, so this is
// the same row the preview below already renders from. One dialog serves both
// slots — only its content swaps.
const viewedPrompt = ref<Prompt | null>(null)
const promptViewOpen = ref(false)

function viewPrompt(prompt: Prompt | null) {
  if (prompt === null) return
  viewedPrompt.value = prompt
  promptViewOpen.value = true
}

// --- assembled message preview -----------------------------------------

// Mirrors `backend/app/services/message_assembly.py`: whitespace-only text is
// absent on either side, and the user message is `task + "\n\n" + content`,
// with the data last. Computed here rather than fetched — both prompt drafts
// already arrived with `promptsApi.list()` for the selects — so the preview
// updates on every keystroke with no round trip, the same reasoning the old
// editor's effective-prompt preview had. Keep it identical to the backend's if
// either changes.
function present(value: string | null | undefined): string | null {
  const trimmed = (value ?? '').trim()
  return trimmed.length > 0 ? trimmed : null
}

const systemMessagePreview = computed(() => present(systemPrompt.value?.content))

// The user message's two halves kept apart for display — the preview shows
// which part each line came from, the same distinction `/results` draws when
// it reports drift. Assembled below, so the text on screen is still exactly
// what goes on the wire.
const taskMessagePreview = computed(() => present(taskPrompt.value?.content))
const contentPreview = computed(() => present(form.content))

const userMessagePreview = computed(() => {
  const task = taskMessagePreview.value
  const data = contentPreview.value
  if (task && data) return `${task}\n\n${data}`
  return task ?? data ?? ''
})

// The same guard the server enforces on save *and* again at run creation
// (`assert_user_message`), surfaced while the case is being written rather
// than when a save is refused.
const userMessageError = computed(() =>
  userMessagePreview.value.length > 0
    ? null
    : 'This test case has no user message. Give it content, or select a task prompt with text in it.',
)

// --- create a prompt without leaving the page ---------------------------

const newPromptOpen = ref(false)
const newPromptKind = ref<PromptKind>('system')
const newPromptForm = reactive({ name: '', content: '' })
const newPromptError = ref<string | null>(null)
const creatingPrompt = ref(false)

function openNewPrompt(kind: PromptKind) {
  newPromptKind.value = kind
  // Prefilled from the test case's title and suffixed with the slot, so the
  // two prompts of one case are still told apart in `/prompts`.
  const title = form.title.trim()
  newPromptForm.name = title.length > 0 ? `${title} — ${kind} prompt` : ''
  newPromptForm.content = ''
  newPromptError.value = null
  newPromptOpen.value = true
}

async function createPrompt() {
  newPromptError.value = null
  creatingPrompt.value = true
  try {
    const created = await promptsApi.create({
      name: newPromptForm.name,
      content: newPromptForm.content,
      // Fixed by the slot it was opened from: a prompt's kind decides which
      // channel its text goes out on, and this dialog knows the answer.
      kind: newPromptKind.value,
    })
    prompts.value = [...prompts.value, created]
    if (created.kind === 'system') form.systemPromptId = created.id
    else form.taskPromptId = created.id
    newPromptOpen.value = false
    toast.add({ severity: 'success', summary: `Prompt "${created.name}" created`, life: 3000 })
  } catch (err) {
    newPromptError.value = err instanceof ApiError ? err.message : 'Failed to create the prompt.'
  } finally {
    creatingPrompt.value = false
  }
}

// --- tool config preview + validation (mirrors backend assert_tool_config) --

// Fetches the tool list for every currently-selected toolset once, so the
// "tools offered" preview and collision check can be computed without a
// round trip per keystroke. Toolsets already fetched are never refetched.
//
// A toolset's tools travel inside its own detail response; there is no
// `/toolsets/{id}/tools` route to ask for them separately. A read that fails
// is recorded as "offers nothing" rather than left absent, so the preview
// cannot sit on "Loading tools…" forever — the server's own
// `assert_tool_config` still has the last word when the case is saved.
watch(
  () => form.toolsetIds,
  async (ids) => {
    const missing = ids.filter((id) => !(id in toolsByToolset.value))
    if (missing.length === 0) return
    const fetched = await Promise.all(
      missing.map(async (id) => {
        try {
          return (await toolsetsApi.get(id)).tools
        } catch (err) {
          toast.add({
            severity: 'error',
            summary: 'Failed to load a toolset’s tools',
            detail: err instanceof ApiError ? err.message : undefined,
            life: 5000,
          })
          return []
        }
      }),
    )
    missing.forEach((id, index) => {
      toolsByToolset.value = { ...toolsByToolset.value, [id]: fetched[index] }
    })
  },
  { immediate: true, deep: true },
)

const toolsLoading = computed(() =>
  form.toolsetIds.some((id) => !(id in toolsByToolset.value)),
)

const selectedToolsets = computed(() => toolsets.value.filter((t) => form.toolsetIds.includes(t.id)))

const offeredTools = computed(() =>
  selectedToolsets.value.flatMap((toolset) =>
    (toolsByToolset.value[toolset.id] ?? []).filter((tool) => tool.enabled),
  ),
)

const toolsActive = computed(() => form.toolMode !== 'none')

// The same checks the backend's `assert_tool_config` enforces (Task 3.4's
// `backend/app/services/tool_config.py`), surfaced while the test case is
// being written rather than when a save or a run refuses to start.
const toolError = computed(() => {
  if (!toolsActive.value || toolsLoading.value) return null
  if (offeredTools.value.length === 0) {
    return 'Pick at least one toolset with an enabled tool, or set the mode back to "No tools".'
  }
  const collisions = collectToolNameCollisions(offeredTools.value)
  if (collisions.length > 0) {
    return `Two selected toolsets both define: ${collisions.join(', ')}. Tool names must be unique within one test case.`
  }
  return null
})

// --- save --------------------------------------------------------------

const saving = ref(false)
const saveError = ref<string | null>(null)

function buildInput() {
  return {
    group_id: form.groupId as number,
    title: form.title,
    content: form.content || null,
    expected_output: form.expectedOutput || null,
    system_prompt_id: form.systemPromptId,
    task_prompt_id: form.taskPromptId,
    tool_mode: form.toolMode,
    toolset_ids: form.toolsetIds,
    tool_choice: form.toolChoice || null,
    max_turns: form.maxTurns || DEFAULT_MAX_TURNS,
  }
}

const saveBlocked = computed(
  () => toolError.value !== null || userMessageError.value !== null || form.groupId === null,
)

async function save() {
  if (saveBlocked.value) return
  saveError.value = null
  saving.value = true
  try {
    if (isNew.value) {
      const created = await testCasesApi.create(buildInput())
      toast.add({ severity: 'success', summary: 'Test case created', life: 3000 })
      await router.replace(`/test-cases/${created.id}`)
    } else if (testCaseId.value !== null) {
      applyTestCase(await testCasesApi.update(testCaseId.value, buildInput()))
      toast.add({ severity: 'success', summary: 'Test case saved', life: 3000 })
    }
  } catch (err) {
    saveError.value = err instanceof ApiError ? err.message : 'Failed to save the test case.'
  } finally {
    saving.value = false
  }
}

// --- delete ----------------------------------------------------------

const deleting = ref(false)

function confirmDelete() {
  if (!testCase.value) return
  confirm.require({
    header: 'Delete test case',
    message: `Delete test case "${testCase.value.title}"? This cannot be undone.`,
    acceptProps: { label: 'Delete', severity: 'danger' },
    rejectProps: { label: 'Cancel', text: true },
    accept: () => void removeTestCase(),
  })
}

async function removeTestCase() {
  if (testCaseId.value === null) return
  deleting.value = true
  try {
    await testCasesApi.remove(testCaseId.value)
    const groupId = form.groupId
    await router.push(groupId ? `/test-cases?group=${groupId}` : '/test-cases')
  } catch (err) {
    toast.add({
      severity: 'error',
      summary: 'Failed to delete test case',
      detail: err instanceof ApiError ? err.message : undefined,
      life: 5000,
    })
  } finally {
    deleting.value = false
  }
}
</script>

<template>
  <div class="page">
    <Message v-if="loadError" severity="error" :closable="false">{{ loadError }}</Message>

    <template v-if="!loading">
      <div class="page-heading">
        <h1>{{ isNew ? 'New test case' : testCase?.title }}</h1>
      </div>

      <!--
        Sections in send order: what the case *is*, then what the model
        receives, then the rubric it is judged against, then the tools it may
        call. The old two-column split put the content the user message ends
        with above the prompts it starts with, which is not the order anything
        downstream reads them in.
      -->
      <form class="editor" @submit.prevent="save">
        <section class="panel">
          <div class="panel-header"><h2>Basics</h2></div>
          <div class="field-row">
            <div class="field">
              <label for="tc-group">Group *</label>
              <Select
                id="tc-group"
                v-model="form.groupId"
                :options="groups"
                option-label="name"
                option-value="id"
                placeholder="Select a group"
                :disabled="!isNew"
              />
              <p v-if="!isNew" class="hint">Groups cannot be changed after creation.</p>
            </div>

            <div class="field">
              <label for="tc-title">Title *</label>
              <InputText id="tc-title" v-model="form.title" required />
            </div>
          </div>
        </section>

        <section class="panel">
          <div class="panel-header"><h2>Model input</h2></div>
          <div class="columns">
            <div class="column">
              <div class="field">
                <label for="tc-system-prompt">System prompt</label>
                <div class="slot-row">
                  <Select
                    id="tc-system-prompt"
                    class="slot-select"
                    :model-value="form.systemPromptId"
                    :options="systemPromptOptions"
                    option-label="name"
                    option-value="id"
                    placeholder="(none)"
                    show-clear
                    @update:model-value="(value) => selectSlot('system', value)"
                  />
                  <Button
                    type="button"
                    icon="pi pi-search"
                    text
                    rounded
                    severity="secondary"
                    aria-label="View prompt"
                    :disabled="systemPrompt === null"
                    @click="viewPrompt(systemPrompt)"
                  />
                </div>
                <p class="hint">Frames the model. Sent as the system message.</p>
              </div>

              <div class="field">
                <label for="tc-task-prompt">Task prompt</label>
                <div class="slot-row">
                  <Select
                    id="tc-task-prompt"
                    class="slot-select"
                    :model-value="form.taskPromptId"
                    :options="taskPromptOptions"
                    option-label="name"
                    option-value="id"
                    placeholder="(none)"
                    show-clear
                    @update:model-value="(value) => selectSlot('task', value)"
                  />
                  <Button
                    type="button"
                    icon="pi pi-search"
                    text
                    rounded
                    severity="secondary"
                    aria-label="View prompt"
                    :disabled="taskPrompt === null"
                    @click="viewPrompt(taskPrompt)"
                  />
                </div>
                <p class="hint">The instruction for this call. Sent at the head of the user message.</p>
              </div>

              <div class="field">
                <label for="tc-content">Content</label>
                <Textarea id="tc-content" v-model="form.content" rows="6" auto-resize class="mono-input" />
                <p class="hint">
                  The data this case varies — sent after the task prompt, at the end of the user
                  message. Optional only when a task prompt supplies the whole user message.
                </p>
              </div>
            </div>

            <!--
              Sticky, and a plain block rather than a flex column: a flex item
              is only as tall as its content, which leaves a sticky child no
              box to slide inside. As a grid item this stretches to the row's
              height instead, so the preview stays on screen while the fields
              beside it scroll.
            -->
            <div class="preview-column">
              <div class="preview-sticky">
                <span class="label">As it will be sent</span>
                <div class="assembled">
                  <div class="segment segment-system">
                    <span class="segment-label">System message</span>
                    <pre v-if="systemMessagePreview" class="segment-text">{{ systemMessagePreview }}</pre>
                    <p v-else class="segment-empty">(no system message)</p>
                  </div>

                  <!-- The task prompt and the content are one message, so they
                       share a box and are separated only by a rule; the system
                       message is a different channel and stands apart. -->
                  <div class="user-group">
                    <span class="group-caption">User message</span>
                    <div class="user-message">
                      <template v-if="userMessagePreview">
                        <div v-if="taskMessagePreview" class="segment segment-task">
                          <span class="segment-label">Task prompt</span>
                          <pre class="segment-text">{{ taskMessagePreview }}</pre>
                        </div>
                        <div v-if="contentPreview" class="segment segment-case">
                          <span class="segment-label">Content</span>
                          <pre class="segment-text">{{ contentPreview }}</pre>
                        </div>
                      </template>
                      <div v-else class="segment segment-case">
                        <p class="segment-empty">(nothing to send)</p>
                      </div>
                    </div>
                  </div>
                </div>
                <Message v-if="userMessageError" severity="error" :closable="false">
                  {{ userMessageError }}
                </Message>
              </div>
            </div>
          </div>
        </section>

        <section class="panel">
          <div class="panel-header"><h2>Expected output</h2></div>
          <div class="field">
            <Textarea
              id="tc-expected"
              v-model="form.expectedOutput"
              rows="4"
              auto-resize
              placeholder="optional"
              class="mono-input"
              aria-label="Expected output"
            />
            <p class="hint">Never sent to the model — used only when rating results.</p>
          </div>
        </section>

        <section class="panel tools-section">
          <div class="panel-header"><h2>Tools</h2></div>
          <div class="field">
            <div class="radio-column">
              <label v-for="option in TOOL_MODES" :key="option.value" class="radio-option block">
                <RadioButton v-model="form.toolMode" :value="option.value" name="tc-tool-mode" />
                <span class="radio-text">
                  <span>{{ option.label }}</span>
                  <span class="hint">{{ option.hint }}</span>
                </span>
              </label>
            </div>
          </div>

          <div v-if="toolsActive" class="columns">
            <div class="column">
              <div class="field">
                <span class="label">Toolsets</span>
                <p v-if="toolsets.length === 0" class="hint bordered">
                  No toolsets defined yet — create one on the Toolsets page first.
                </p>
                <div v-else class="checkbox-column">
                  <label
                    v-for="toolset in toolsets"
                    :key="toolset.id"
                    class="checkbox-option"
                    :for="`toolset-${toolset.id}`"
                  >
                    <Checkbox v-model="form.toolsetIds" :value="toolset.id" :input-id="`toolset-${toolset.id}`" />
                    <span>
                      {{ toolset.name }}
                      <Tag v-if="toolset.is_global" value="Global" severity="info" />
                      <span class="hint">({{ toolset.kind }}, {{ toolset.enabled_tool_count }} enabled)</span>
                    </span>
                  </label>
                </div>
              </div>

              <div class="field-row">
                <div class="field">
                  <label for="tc-tool-choice">Tool choice</label>
                  <Select
                    id="tc-tool-choice"
                    v-model="form.toolChoice"
                    :options="TOOL_CHOICE_OPTIONS"
                    option-label="label"
                    option-value="value"
                  />
                </div>
                <div class="field">
                  <label for="tc-max-turns">Max turns</label>
                  <InputNumber
                    id="tc-max-turns"
                    v-model="form.maxTurns"
                    :min="1"
                    :max="MAX_TURNS_LIMIT"
                    show-buttons
                    button-layout="horizontal"
                  />
                </div>
              </div>
            </div>

            <div class="column">
              <div class="field">
                <span class="label">Tools offered to the model</span>
                <div class="preview">
                  <p v-if="toolsLoading" class="hint">Loading tools…</p>
                  <p v-else-if="offeredTools.length === 0" class="hint">(no tools)</p>
                  <ul v-else class="offered-tools">
                    <li v-for="(tool, index) in offeredTools" :key="`${tool.name}-${index}`">
                      <span class="mono">{{ tool.name }}</span>
                      <span v-if="tool.description" class="hint"> — {{ tool.description }}</span>
                    </li>
                  </ul>
                </div>
                <Message v-if="toolError" severity="error" :closable="false">{{ toolError }}</Message>
              </div>
            </div>
          </div>
        </section>

        <Message v-if="saveError" severity="error" :closable="false">{{ saveError }}</Message>

        <div class="actions">
          <Button
            type="submit"
            :label="isNew ? 'Create test case' : 'Save changes'"
            :loading="saving"
            :disabled="saveBlocked"
          />
          <Button type="button" label="Cancel" text @click="router.back()" />
        </div>

        <div v-if="!isNew && auth.canWrite" class="danger-zone">
          <Button label="Delete test case" severity="danger" outlined :loading="deleting" @click="confirmDelete" />
        </div>
      </form>
    </template>

    <!--
      Create-in-place: a prompt is a versioned asset now, so a one-off prompt is
      a row in /prompts rather than a textarea here — but it is still authored
      without leaving the page. The kind is fixed by the slot this was opened
      from and is therefore stated, not chosen.
    -->
    <Dialog
      v-model:visible="newPromptOpen"
      modal
      :header="newPromptKind === 'system' ? 'New system prompt' : 'New task prompt'"
      class="form-dialog"
    >
      <form class="dialog-form" @submit.prevent="createPrompt">
        <p class="hint">
          {{ PROMPT_KINDS.find((kind) => kind.value === newPromptKind)?.hint }}
        </p>
        <div class="field">
          <label for="np-name">Name *</label>
          <InputText id="np-name" v-model="newPromptForm.name" required autofocus />
        </div>
        <div class="field">
          <label for="np-content">Draft content</label>
          <Textarea
            id="np-content"
            v-model="newPromptForm.content"
            rows="8"
            auto-resize
            class="mono-input"
          />
        </div>
        <Message v-if="newPromptError" severity="error" :closable="false">{{ newPromptError }}</Message>
        <div class="dialog-actions">
          <Button type="button" label="Cancel" text @click="newPromptOpen = false" />
          <Button type="submit" label="Create and select" :loading="creatingPrompt" />
        </div>
      </form>
    </Dialog>

    <!-- Read-only: the draft belongs to the prompt's own editor, and a slot
         reference is not the place to change what every other test case using
         it sends. -->
    <Dialog
      v-model:visible="promptViewOpen"
      modal
      :header="viewedPrompt?.name ?? 'Prompt'"
      class="prompt-view-dialog"
    >
      <div v-if="viewedPrompt" class="prompt-view">
        <Tag
          :value="viewedPrompt.kind"
          :severity="viewedPrompt.kind === 'system' ? 'info' : 'secondary'"
        />
        <pre class="prompt-view-text">{{ viewedPrompt.content || '(empty draft)' }}</pre>
      </div>
    </Dialog>
  </div>
</template>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  gap: 1.5rem;
  max-width: 72rem;
}

.page-heading h1 {
  font-size: 1.5rem;
  font-weight: 600;
  margin: 0;
}

.editor {
  display: flex;
  flex-direction: column;
  gap: 1.5rem;
}

/* The app's section idiom, same as `PromptEditView`'s panels. */
.panel {
  display: flex;
  flex-direction: column;
  gap: 1rem;
  padding: 1.5rem;
  border: 1px solid var(--p-content-border-color);
  border-radius: var(--p-content-border-radius);
}

.panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.panel h2 {
  font-size: 1.0625rem;
  font-weight: 600;
  margin: 0;
}

.columns {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 2rem;
}

.column {
  display: flex;
  flex-direction: column;
  gap: 1rem;
  min-width: 0;
}

.slot-row {
  display: flex;
  align-items: center;
  gap: 0.25rem;
}

.slot-select {
  flex: 1;
  min-width: 0;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 0.375rem;
}

.field-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 1rem;
}

.field label,
.field .label,
.preview-sticky .label {
  font-size: 0.8125rem;
  font-weight: 500;
  color: var(--p-text-muted-color);
}

.mono-input :deep(textarea) {
  font-family: var(--p-font-family-mono, ui-monospace, monospace);
  font-size: 0.8125rem;
}

.preview {
  min-height: 4.5rem;
  white-space: pre-wrap;
  border: 1px dashed var(--p-content-border-color);
  border-radius: var(--p-content-border-radius);
  padding: 0.75rem;
  font-family: var(--p-font-family-mono, ui-monospace, monospace);
  font-size: 0.75rem;
  color: var(--p-text-color);
  margin: 0;
}

/* Plain block, not a flex column — see the template comment: a sticky child
   needs an ancestor box taller than itself, which only the stretched grid
   item provides. */
.preview-column {
  min-width: 0;
}

/* A small gap, not the topbar's height: `AppLayout`'s `.app-content` is the
   scroll container (`overflow: auto` inside a `.app-body` sized to
   `100vh - 3.5rem`), so its scrollport already starts below the pinned
   topbar and an offset of 3.5rem here would only strand the preview that far
   down. */
.preview-sticky {
  position: sticky;
  top: 1rem;
  display: flex;
  flex-direction: column;
  gap: 0.375rem;
}

.assembled {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
  border: 1px solid var(--p-content-border-color);
  border-radius: var(--p-content-border-radius);
  padding: 0.75rem;
  /* A long prompt must not push the sticky box past the viewport, or it
     stops being visible while scrolling — which is the whole point of it. */
  max-height: calc(100vh - 9rem);
  overflow: auto;
}

/* One colour per channel, the same tokens the results matrix's prompt peeks
   read (`src/style.css`): system blue, task violet, the case's own content
   neutral. */
.segment {
  border-left: 3px solid var(--pr-case-accent);
  background: var(--pr-case-bg);
  padding: 0.5rem 0.625rem;
}

.segment-system {
  border-left-color: var(--pr-system-accent);
  background: var(--pr-system-bg);
}

.segment-task {
  border-left-color: var(--pr-task-accent);
  background: var(--pr-task-bg);
}

.segment-label {
  display: block;
  font-size: 0.6875rem;
  text-transform: uppercase;
  letter-spacing: 0.03em;
  color: var(--p-text-muted-color);
}

.segment-text {
  margin: 0.25rem 0 0;
  white-space: pre-wrap;
  word-break: break-word;
  font-family: var(--p-font-family-mono, ui-monospace, monospace);
  font-size: 0.75rem;
  color: var(--p-text-color);
}

.segment-empty {
  margin: 0.25rem 0 0;
  font-size: 0.75rem;
  color: var(--p-text-muted-color);
}

.user-group {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}

.group-caption {
  font-size: 0.6875rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.03em;
  color: var(--p-text-muted-color);
}

/* Task prompt and content are one message, so they share a box and only a
   rule separates them. */
.user-message {
  border: 1px solid var(--p-content-border-color);
  border-radius: var(--p-content-border-radius);
  overflow: hidden;
}

.user-message .segment + .segment {
  border-top: 1px dashed var(--p-content-border-color);
}

.hint {
  font-size: 0.75rem;
  color: var(--p-text-muted-color);
  margin: 0;
}

.hint.bordered {
  border: 1px dashed var(--p-content-border-color);
  border-radius: var(--p-content-border-radius);
  padding: 0.75rem;
}

.radio-column {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  font-size: 0.875rem;
}

.radio-option {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.radio-option.block {
  align-items: flex-start;
}

.radio-text {
  display: flex;
  flex-direction: column;
  gap: 0.125rem;
}

/* Wider gap than the other panels: the mode radios and the toolset picker
   below them are two decisions, not one field after another. */
.tools-section {
  gap: 1.5rem;
}

.checkbox-column {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  font-size: 0.875rem;
}

.checkbox-option {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.offered-tools {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  font-size: 0.75rem;
}

.mono {
  font-family: var(--p-font-family-mono, ui-monospace, monospace);
  color: var(--p-text-color);
}

.actions {
  display: flex;
  gap: 0.5rem;
}

.danger-zone {
  border-top: 1px solid var(--p-content-border-color);
  padding-top: 1rem;
}

.dialog-form {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.dialog-actions {
  display: flex;
  justify-content: flex-end;
  gap: 0.5rem;
}

/* The dialog root is teleported and its width lives in `src/style.css`
   (`.prompt-view-dialog`); everything inside it is rendered from this
   template, so it keeps the scope attribute and these rules apply. */
.prompt-view {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 0.75rem;
}

.prompt-view-text {
  align-self: stretch;
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
  border: 1px solid var(--p-content-border-color);
  border-radius: var(--p-content-border-radius);
  padding: 0.75rem;
  font-family: var(--p-font-family-mono, ui-monospace, monospace);
  font-size: 0.8125rem;
  color: var(--p-text-color);
}
</style>
