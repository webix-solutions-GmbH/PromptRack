<script setup lang="ts">
// New run: pick an endpoint + model, pick which test-case groups to run, and
// go. Port of `git show legacy-nextjs:src/components/runs/new-run-form.tsx` (model
// detection: probe on endpoint select, auto-select a single loaded model,
// degrade to a warning + previously-seen models, discard a slow probe's
// answer for an endpoint the user has since switched away from) plus the
// pivot's "Verify" entry point (spec §"Workflow & UI": a version's baseline
// run gets a Verify button that opens this page prefilled).
//
// Deviation from the old app, forced by what the landed backend contract
// actually exposes (flagged for reconciliation — see this task's report):
// `GET /endpoints/{id}/models` (the "previously seen" model history used by
// the old "Currently loaded" / "Previously seen" optgroups) has no route on
// `backend/app/api/endpoints.py` yet, though `../api/endpoints.ts` and
// `EndpointEditView.vue` already assume it exists. `historyModels` below
// calls it anyway (for symmetry with that existing convention, and so this
// view starts working the moment the route lands) but degrades silently to
// an empty list on failure, same as an unreachable endpoint's warning.
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import Button from 'primevue/button'
import Checkbox from 'primevue/checkbox'
import InputNumber from 'primevue/inputnumber'
import InputText from 'primevue/inputtext'
import Message from 'primevue/message'
import Select from 'primevue/select'
import Textarea from 'primevue/textarea'
import { endpointsApi, type Endpoint, type EndpointModel } from '../api/endpoints'
import { runsApi } from '../api/runs'
import { testGroupsApi, type TestGroup } from '../api/testCases'
import { ApiError } from '../api/client'
import { useAuthStore } from '../stores/auth'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const CUSTOM = '__custom__'

const endpoints = ref<Endpoint[]>([])
const groups = ref<TestGroup[]>([])
const loading = ref(true)
const loadError = ref<string | null>(null)

const endpointId = ref<number | null>(null)
const modelChoice = ref('')
const customModel = ref('')
const selectedGroupIds = ref<number[]>([])
const temperature = ref<number | null>(null)
const maxTokens = ref<number | null>(null)
const comment = ref('')

const submitting = ref(false)
const submitError = ref<string | null>(null)

async function load() {
  loading.value = true
  loadError.value = null
  try {
    const [endpointRows, groupRows] = await Promise.all([endpointsApi.list(), testGroupsApi.list()])
    endpoints.value = endpointRows
    groups.value = groupRows
    if (endpointRows.length > 0) endpointId.value = endpointRows[0]!.id
  } catch (err) {
    loadError.value = err instanceof ApiError ? err.message : 'Failed to load endpoints or groups.'
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  await load()
  await preloadBaseline()
})

// --- model detection -------------------------------------------------------

type ProbeState =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'ok'; models: string[] }
  | { status: 'error'; message: string }

const probe = ref<ProbeState>({ status: 'idle' })
const historyModels = ref<EndpointModel[]>([])
/** Discards a slow probe's answer once the user has switched to a different
 * endpoint — the same sequence guard the old app used a ref for. */
let probeSeq = 0

async function detectModels(id: number) {
  const seq = ++probeSeq
  probe.value = { status: 'loading' }

  // The model history fetch is best-effort (see the module comment above on
  // the endpoint it depends on) and must never block the discover probe.
  void endpointsApi
    .listModels(id)
    .then((rows) => {
      if (probeSeq === seq) historyModels.value = rows
    })
    .catch(() => {
      if (probeSeq === seq) historyModels.value = []
    })

  let result: Awaited<ReturnType<typeof endpointsApi.discover>>
  try {
    result = await endpointsApi.discover(id)
  } catch {
    if (probeSeq === seq) probe.value = { status: 'error', message: 'Could not reach the endpoint.' }
    return
  }

  if (probeSeq !== seq) return

  if (!result.ok) {
    probe.value = { status: 'error', message: result.error ?? 'Could not reach the endpoint.' }
    return
  }

  probe.value = { status: 'ok', models: result.models }
  // Exactly one served model is the common single-model-per-endpoint case
  // (vLLM), so pick it. With several, guessing would be worse than asking.
  if (result.models.length === 1) {
    modelChoice.value = result.models[0]!
  }
}

watch(endpointId, (id) => {
  modelChoice.value = ''
  historyModels.value = []
  probe.value = { status: 'idle' }
  if (id !== null) void detectModels(id)
})

const detected = computed(() => (probe.value.status === 'ok' ? new Set(probe.value.models) : null))

function isLoaded(model: EndpointModel): boolean {
  return detected.value ? detected.value.has(model.model_id) : model.currently_loaded
}

const loadedModels = computed(() => historyModels.value.filter(isLoaded))
const previouslySeenModels = computed(() => historyModels.value.filter((m) => !isLoaded(m)))

interface ModelOptionGroup {
  label: string
  items: { label: string; value: string }[]
}

// PrimeVue's `Select` grouping expects `options` shaped as
// `{ label, items }[]` (matching `option-group-children="items"` below), not
// a flat list with a per-row group tag.
const modelOptions = computed<ModelOptionGroup[]>(() => {
  const groups: ModelOptionGroup[] = []
  if (loadedModels.value.length > 0) {
    groups.push({
      label: 'Currently loaded',
      items: loadedModels.value.map((model) => ({ label: model.model_id, value: model.model_id })),
    })
  }
  if (previouslySeenModels.value.length > 0) {
    groups.push({
      label: 'Previously seen',
      items: previouslySeenModels.value.map((model) => ({
        label: model.model_id,
        value: model.model_id,
      })),
    })
  }
  groups.push({ label: '', items: [{ label: 'Other — type a model id…', value: CUSTOM }] })
  return groups
})

const showCustomModel = computed(
  () => modelChoice.value === CUSTOM || historyModels.value.length === 0,
)
const resolvedModelId = computed(() =>
  modelChoice.value === CUSTOM ? customModel.value.trim() : modelChoice.value,
)

// --- baseline preload ("Verify" entry point) -------------------------------

const baselineNote = ref<string | null>(null)

async function preloadBaseline() {
  const raw = route.query.baseline
  const baselineId = Number(Array.isArray(raw) ? raw[0] : raw)
  if (!Number.isInteger(baselineId) || baselineId <= 0) return

  try {
    const baselineRun = await runsApi.get(baselineId)
    const byName = new Map(groups.value.map((group) => [group.name, group.id]))
    const matched = baselineRun.group_names
      .map((name) => byName.get(name))
      .filter((id): id is number => id !== undefined)
    selectedGroupIds.value = [...new Set(matched)]
    baselineNote.value =
      matched.length > 0
        ? `Preloaded ${matched.length} group(s) from run #${baselineId}. Pick the model/endpoint to verify.`
        : `Run #${baselineId}'s groups no longer exist — pick groups manually.`
  } catch {
    baselineNote.value = `Could not load run #${baselineId} to preload its groups.`
  }
}

// The Select's own `#value` slot needs this to mark a shared endpoint even
// once picked, not only while it is still one option among several.
const selectedEndpoint = computed(
  () => endpoints.value.find((endpoint) => endpoint.id === endpointId.value) ?? null,
)

// --- submit ------------------------------------------------------------

const canSubmit = computed(
  () => endpointId.value !== null && resolvedModelId.value.length > 0 && selectedGroupIds.value.length > 0,
)

function toggleGroup(id: number) {
  const current = selectedGroupIds.value
  selectedGroupIds.value = current.includes(id)
    ? current.filter((value) => value !== id)
    : [...current, id]
}

async function submit() {
  if (!canSubmit.value || endpointId.value === null) return
  submitError.value = null
  submitting.value = true
  try {
    const created = await runsApi.create({
      endpoint_id: endpointId.value,
      model_id: resolvedModelId.value,
      group_ids: selectedGroupIds.value,
      temperature: temperature.value,
      max_tokens: maxTokens.value,
      comment: comment.value.trim() || null,
    })
    await router.push(`/runs/${created.id}`)
  } catch (err) {
    submitError.value = err instanceof ApiError ? err.message : 'Failed to start the run.'
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div class="page">
    <div class="page-heading">
      <h1>New run</h1>
      <p class="subtitle">
        Every test case in the selected groups is executed sequentially against one endpoint and
        model. Test cases, prompts and endpoint specs are snapshotted, so later edits never change
        this run's history.
      </p>
    </div>

    <Message v-if="loadError" severity="error" :closable="false">{{ loadError }}</Message>

    <div v-if="!loading && endpoints.length === 0" class="empty-state">
      Add an endpoint first — a run needs one to talk to.
    </div>

    <form v-else-if="!loading" class="form" @submit.prevent="submit">
      <div class="field-row">
        <div class="field">
          <label for="run-endpoint">Endpoint *</label>
          <Select
            id="run-endpoint"
            v-model="endpointId"
            :options="endpoints"
            option-label="name"
            option-value="id"
          >
            <template #option="{ option }: { option: Endpoint }">
              {{ option.name }}{{ option.is_global ? ' (Global)' : '' }}
            </template>
            <template #value>
              {{ selectedEndpoint?.name }}{{ selectedEndpoint?.is_global ? ' (Global)' : '' }}
            </template>
          </Select>
        </div>

        <div class="field">
          <div class="field-header">
            <label for="run-model">Model *</label>
            <Button
              v-if="auth.canWrite"
              type="button"
              label="Re-detect"
              text
              size="small"
              :disabled="probe.status === 'loading' || endpointId === null"
              @click="endpointId !== null && detectModels(endpointId)"
            />
          </div>
          <Select
            id="run-model"
            v-model="modelChoice"
            :options="modelOptions"
            option-label="label"
            option-value="value"
            option-group-label="label"
            option-group-children="items"
            placeholder="Select a model…"
          >
            <template #option="{ option }">{{ option.label }}</template>
          </Select>
          <p v-if="probe.status === 'loading'" class="hint">Asking the endpoint what it is serving…</p>
          <p v-else-if="probe.status === 'ok'" class="hint">
            {{
              probe.models.length === 0
                ? 'The endpoint reports no loaded model.'
                : probe.models.length === 1
                  ? `Serving ${probe.models[0]} — selected.`
                  : `${probe.models.length} models loaded — pick one.`
            }}
          </p>
          <p v-else-if="probe.status === 'error'" class="hint warn">
            {{ probe.message }} Previously seen models are still selectable.
          </p>
        </div>
      </div>

      <div v-if="showCustomModel" class="field">
        <label for="run-custom-model">Model id</label>
        <InputText
          id="run-custom-model"
          v-model="customModel"
          placeholder="llama-3.1-8b-instruct"
          class="mono-input"
          @input="modelChoice = CUSTOM"
        />
        <p class="hint">Free text — the model does not have to be discovered yet.</p>
      </div>

      <div class="field">
        <span class="field-label">Test-case groups *</span>
        <Message v-if="baselineNote" severity="info" :closable="false">{{ baselineNote }}</Message>
        <p v-if="groups.length === 0" class="hint">No groups yet — create one under Test Cases.</p>
        <div v-else class="group-list">
          <label
            v-for="group in groups"
            :key="group.id"
            class="group-item"
            :class="{ disabled: group.test_case_count === 0 }"
            :for="`run-group-${group.id}`"
          >
            <Checkbox
              :model-value="selectedGroupIds.includes(group.id)"
              :binary="true"
              :input-id="`run-group-${group.id}`"
              :disabled="group.test_case_count === 0"
              @update:model-value="toggleGroup(group.id)"
            />
            <span>{{ group.name }}</span>
            <span class="hint"
              >{{ group.test_case_count }} test case{{ group.test_case_count === 1 ? '' : 's' }}</span
            >
          </label>
        </div>
      </div>

      <div class="field-row">
        <div class="field">
          <label for="run-temperature">Temperature</label>
          <InputNumber
            id="run-temperature"
            v-model="temperature"
            :min="0"
            :max="2"
            :step="0.1"
            :max-fraction-digits="2"
            placeholder="server default"
            show-buttons
          />
        </div>
        <div class="field">
          <label for="run-max-tokens">Max tokens</label>
          <InputNumber
            id="run-max-tokens"
            v-model="maxTokens"
            :min="1"
            placeholder="server default"
            show-buttons
          />
        </div>
      </div>

      <div class="field">
        <label for="run-comment">Comment</label>
        <Textarea
          id="run-comment"
          v-model="comment"
          rows="3"
          auto-resize
          placeholder="What are you testing with this run?"
        />
      </div>

      <Message v-if="submitError" severity="error" :closable="false">{{ submitError }}</Message>

      <div v-if="auth.canWrite">
        <Button type="submit" label="Start run" :disabled="!canSubmit" :loading="submitting" />
      </div>
    </form>
  </div>
</template>

<style scoped>
.page {
  max-width: 42rem;
}

.form {
  display: flex;
  flex-direction: column;
  gap: 1.25rem;
  padding: 1.5rem;
  border: 1px solid var(--p-content-border-color);
  border-radius: var(--p-content-border-radius);
}

.field-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 1rem;
}

.field {
  min-width: 0;
}

.field-label {
  font-size: 0.8125rem;
  font-weight: 500;
  color: var(--p-text-muted-color);
}

.field-header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 0.5rem;
}

.hint.warn {
  color: var(--p-orange-500);
}

.mono-input {
  font-family: var(--p-font-family-mono, ui-monospace, monospace);
}

.group-list {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  border: 1px solid var(--p-content-border-color);
  border-radius: var(--p-content-border-radius);
  padding: 0.75rem;
}

.group-item {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.875rem;
  cursor: pointer;
}

.group-item.disabled {
  color: var(--p-text-muted-color);
  cursor: default;
}
</style>
