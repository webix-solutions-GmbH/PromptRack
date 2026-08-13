<script setup lang="ts">
// New run: pick a machine + model, pick which test-case groups to run, and
// go. Port of `git show master:src/components/runs/new-run-form.tsx` (model
// detection: probe on machine select, auto-select a single loaded model,
// degrade to a warning + previously-seen models, discard a slow probe's
// answer for a machine the user has since switched away from) plus the
// pivot's "Verify" entry point (spec §"Workflow & UI": a version's baseline
// run gets a Verify button that opens this page prefilled).
//
// Deviation from the old app, forced by what the landed backend contract
// actually exposes (flagged for reconciliation — see this task's report):
// `GET /machines/{id}/models` (the "previously seen" model history used by
// the old "Currently loaded" / "Previously seen" optgroups) has no route on
// `backend/app/api/machines.py` yet, though `../api/machines.ts` and
// `MachineEditView.vue` already assume it exists. `historyModels` below
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
import { machinesApi, type Machine, type MachineModel } from '../api/machines'
import { runsApi } from '../api/runs'
import { testGroupsApi, type TestGroup } from '../api/testCases'
import { ApiError } from '../api/client'

const route = useRoute()
const router = useRouter()

const CUSTOM = '__custom__'

const machines = ref<Machine[]>([])
const groups = ref<TestGroup[]>([])
const loading = ref(true)
const loadError = ref<string | null>(null)

const machineId = ref<number | null>(null)
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
    const [machineRows, groupRows] = await Promise.all([machinesApi.list(), testGroupsApi.list()])
    machines.value = machineRows
    groups.value = groupRows
    if (machineRows.length > 0) machineId.value = machineRows[0]!.id
  } catch (err) {
    loadError.value = err instanceof ApiError ? err.message : 'Failed to load machines or groups.'
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
const historyModels = ref<MachineModel[]>([])
/** Discards a slow probe's answer once the user has switched to a different
 * machine — the same sequence guard the old app used a ref for. */
let probeSeq = 0

async function detectModels(id: number) {
  const seq = ++probeSeq
  probe.value = { status: 'loading' }

  // The model history fetch is best-effort (see the module comment above on
  // the endpoint it depends on) and must never block the discover probe.
  void machinesApi
    .listModels(id)
    .then((rows) => {
      if (probeSeq === seq) historyModels.value = rows
    })
    .catch(() => {
      if (probeSeq === seq) historyModels.value = []
    })

  let result: Awaited<ReturnType<typeof machinesApi.discover>>
  try {
    result = await machinesApi.discover(id)
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

watch(machineId, (id) => {
  modelChoice.value = ''
  historyModels.value = []
  probe.value = { status: 'idle' }
  if (id !== null) void detectModels(id)
})

const detected = computed(() => (probe.value.status === 'ok' ? new Set(probe.value.models) : null))

function isLoaded(model: MachineModel): boolean {
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
        ? `Preloaded ${matched.length} group(s) from run #${baselineId}. Pick the model/machine to verify.`
        : `Run #${baselineId}'s groups no longer exist — pick groups manually.`
  } catch {
    baselineNote.value = `Could not load run #${baselineId} to preload its groups.`
  }
}

// --- submit ------------------------------------------------------------

const canSubmit = computed(
  () => machineId.value !== null && resolvedModelId.value.length > 0 && selectedGroupIds.value.length > 0,
)

function toggleGroup(id: number) {
  const current = selectedGroupIds.value
  selectedGroupIds.value = current.includes(id)
    ? current.filter((value) => value !== id)
    : [...current, id]
}

async function submit() {
  if (!canSubmit.value || machineId.value === null) return
  submitError.value = null
  submitting.value = true
  try {
    const created = await runsApi.create({
      machine_id: machineId.value,
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
        Every test case in the selected groups is executed sequentially against one machine and
        model. Test cases, prompts and machine specs are snapshotted, so later edits never change
        this run's history.
      </p>
    </div>

    <Message v-if="loadError" severity="error" :closable="false">{{ loadError }}</Message>

    <div v-if="!loading && machines.length === 0" class="empty-state">
      Add a machine first — a run needs an endpoint to talk to.
    </div>

    <form v-else-if="!loading" class="form" @submit.prevent="submit">
      <div class="field-row">
        <div class="field">
          <label for="run-machine">Machine *</label>
          <Select
            id="run-machine"
            v-model="machineId"
            :options="machines"
            option-label="name"
            option-value="id"
          />
        </div>

        <div class="field">
          <div class="field-header">
            <label for="run-model">Model *</label>
            <Button
              type="button"
              label="Re-detect"
              text
              size="small"
              :disabled="probe.status === 'loading' || machineId === null"
              @click="machineId !== null && detectModels(machineId)"
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
          >
            <Checkbox
              :model-value="selectedGroupIds.includes(group.id)"
              :binary="true"
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

      <div>
        <Button type="submit" label="Start run" :disabled="!canSubmit" :loading="submitting" />
      </div>
    </form>
  </div>
</template>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  gap: 1.5rem;
  max-width: 42rem;
}

.page-heading h1 {
  font-size: 1.5rem;
  font-weight: 600;
  margin: 0 0 0.375rem;
}

.subtitle {
  color: var(--p-text-muted-color);
  font-size: 0.875rem;
  margin: 0;
}

.empty-state {
  border: 1px solid var(--p-content-border-color);
  border-radius: var(--p-content-border-radius);
  padding: 3rem 1.5rem;
  text-align: center;
  color: var(--p-text-muted-color);
  font-size: 0.875rem;
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
  display: flex;
  flex-direction: column;
  gap: 0.375rem;
  min-width: 0;
}

.field label,
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

.hint {
  font-size: 0.75rem;
  color: var(--p-text-muted-color);
  margin: 0;
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
