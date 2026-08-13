<script setup lang="ts">
// Test case create/edit — the port of the old prompt editor
// (`git show master:src/components/prompts/prompt-editor.tsx`) under the
// pivot's terminology: a test case references a *prompt* asset (the
// versioned system prompt) plus append/override custom text, offers any
// number of toolsets, and carries the rubric that never reaches the model.
//
// One component handles both routes (`/test-cases/new` and
// `/test-cases/:id`), same shape as the old editor taking an optional
// `groupId` for creation — `props.id` absent means create.
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import Button from 'primevue/button'
import Checkbox from 'primevue/checkbox'
import InputNumber from 'primevue/inputnumber'
import InputText from 'primevue/inputtext'
import Message from 'primevue/message'
import RadioButton from 'primevue/radiobutton'
import Select from 'primevue/select'
import Textarea from 'primevue/textarea'
import { useConfirm } from 'primevue/useconfirm'
import { useToast } from 'primevue/usetoast'
import {
  testCasesApi,
  testGroupsApi,
  type PromptMode,
  type TestCase,
  type TestGroup,
  type ToolChoice,
  type ToolMode,
} from '../api/testCases'
import { promptsApi, type Prompt } from '../api/prompts'
import { toolsetsApi, type Tool, type Toolset } from '../api/toolsets'
import { ApiError } from '../api/client'
import { collectToolNameCollisions, DEFAULT_MAX_TURNS, MAX_TURNS_LIMIT } from '../lib/tools'
import { resolveEffectivePrompt } from '../lib/effectivePrompt'
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
  promptId: number | null
  mode: PromptMode
  customText: string
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
    promptId: null,
    mode: 'append',
    customText: '',
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
  form.content = row.content
  form.expectedOutput = row.expected_output ?? ''
  form.promptId = row.prompt_id
  form.mode = row.mode
  form.customText = row.custom_text ?? ''
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

// --- effective prompt preview (pure, client-side — see ../lib/effectivePrompt.ts) --

const selectedBasePrompt = computed<Prompt | undefined>(() =>
  prompts.value.find((p) => p.id === form.promptId),
)

const effectivePreview = computed(() =>
  resolveEffectivePrompt({
    mode: form.mode,
    baseContent: selectedBasePrompt.value?.content ?? null,
    customText: form.customText,
  }),
)

// --- tool config preview + validation (mirrors backend assert_tool_config) --

// Fetches the tool list for every currently-selected toolset once, so the
// "tools offered" preview and collision check can be computed without a
// round trip per keystroke. Toolsets already fetched are never refetched.
watch(
  () => form.toolsetIds,
  async (ids) => {
    const missing = ids.filter((id) => !(id in toolsByToolset.value))
    if (missing.length === 0) return
    const fetched = await Promise.all(missing.map((id) => toolsetsApi.listTools(id)))
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
    content: form.content,
    expected_output: form.expectedOutput || null,
    prompt_id: form.promptId,
    mode: form.mode,
    custom_text: form.customText || null,
    tool_mode: form.toolMode,
    toolset_ids: form.toolsetIds,
    tool_choice: form.toolChoice || null,
    max_turns: form.maxTurns || DEFAULT_MAX_TURNS,
  }
}

async function save() {
  if (toolError.value !== null || form.groupId === null) return
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

      <form class="editor" @submit.prevent="save">
        <div class="columns">
          <div class="column">
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

            <div class="field">
              <label for="tc-content">Prompt (user message) *</label>
              <Textarea id="tc-content" v-model="form.content" required rows="6" auto-resize class="mono-input" />
            </div>

            <div class="field">
              <label for="tc-expected">Expected output</label>
              <Textarea
                id="tc-expected"
                v-model="form.expectedOutput"
                rows="4"
                auto-resize
                placeholder="optional"
                class="mono-input"
              />
            </div>
          </div>

          <div class="column">
            <div class="field">
              <label for="tc-prompt">Base prompt</label>
              <Select
                id="tc-prompt"
                v-model="form.promptId"
                :options="prompts"
                option-label="name"
                option-value="id"
                placeholder="(none)"
                show-clear
              />
            </div>

            <div class="field">
              <span class="label">Mode</span>
              <div class="radio-row">
                <label class="radio-option">
                  <RadioButton v-model="form.mode" value="append" name="tc-mode" />
                  Append
                </label>
                <label class="radio-option">
                  <RadioButton v-model="form.mode" value="override" name="tc-mode" />
                  Override
                </label>
              </div>
            </div>

            <div class="field">
              <label for="tc-custom-text">
                {{
                  form.mode === 'override'
                    ? 'Custom system text (replaces base)'
                    : 'Custom system text (appended after base)'
                }}
              </label>
              <Textarea id="tc-custom-text" v-model="form.customText" rows="4" auto-resize class="mono-input" />
            </div>

            <div class="field">
              <span class="label">Effective system prompt preview</span>
              <pre class="preview">{{ effectivePreview ?? '(no system message)' }}</pre>
            </div>
          </div>
        </div>

        <div class="tools-section">
          <div class="field">
            <span class="label">Tools</span>
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
        </div>

        <Message v-if="saveError" severity="error" :closable="false">{{ saveError }}</Message>

        <div class="actions">
          <Button
            type="submit"
            :label="isNew ? 'Create test case' : 'Save changes'"
            :loading="saving"
            :disabled="toolError !== null || form.groupId === null"
          />
          <Button type="button" label="Cancel" text @click="router.back()" />
        </div>

        <div v-if="!isNew && auth.canWrite" class="danger-zone">
          <Button label="Delete test case" severity="danger" outlined :loading="deleting" @click="confirmDelete" />
        </div>
      </form>
    </template>
  </div>
</template>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  gap: 1.5rem;
  max-width: 64rem;
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
.field .label {
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

.radio-row {
  display: flex;
  gap: 1.25rem;
  font-size: 0.875rem;
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

.tools-section {
  display: flex;
  flex-direction: column;
  gap: 1.5rem;
  border-top: 1px solid var(--p-content-border-color);
  padding-top: 1.5rem;
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
</style>
