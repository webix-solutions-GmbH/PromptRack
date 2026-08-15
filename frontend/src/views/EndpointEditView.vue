<script setup lang="ts">
// Endpoint detail/edit. Test probes the endpoint with its stored credentials,
// so it is admin-only; discovery only reads model ids and every writer needs
// it (it is what the new-run page also triggers on endpoint select).
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import Button from 'primevue/button'
import Checkbox from 'primevue/checkbox'
import Column from 'primevue/column'
import DataTable from 'primevue/datatable'
import InputText from 'primevue/inputtext'
import Message from 'primevue/message'
import Password from 'primevue/password'
import Select from 'primevue/select'
import Tag from 'primevue/tag'
import Textarea from 'primevue/textarea'
import { useConfirm } from 'primevue/useconfirm'
import { useToast } from 'primevue/usetoast'
import {
  endpointsApi,
  type Endpoint,
  type EndpointInput,
  type EndpointModel,
  type EndpointPlatform,
} from '../api/endpoints'
import { ApiError } from '../api/client'
import ParamsEditor from '../components/ParamsEditor.vue'
import { formatDateTime } from '../lib/format'
import { PARAM_CATALOG } from '../lib/paramCatalog'
import { useAuthStore } from '../stores/auth'

/** Options for the platform `Select` — same construction as the create
 * dialog's, kept here rather than shared since each file already imports
 * `PARAM_CATALOG` for its own reason (this one also feeds `ParamsEditor`). */
const platformOptions = (Object.keys(PARAM_CATALOG) as EndpointPlatform[]).map((key) => ({
  label: PARAM_CATALOG[key].label,
  value: key,
}))

const props = defineProps<{ id: string }>()
const endpointId = computed(() => Number(props.id))

const auth = useAuthStore()
const router = useRouter()
const confirm = useConfirm()
const toast = useToast()

const endpoint = ref<Endpoint | null>(null)
const models = ref<EndpointModel[]>([])
const loading = ref(true)
const loadError = ref<string | null>(null)

// Same gate as the Details section's own `v-if` — shared so the two-column
// grid can drop to one column when that section isn't rendered at all.
const showDetailsPanel = computed(() => auth.canAdminister && endpoint.value?.editable === true)

interface EndpointFormState {
  name: string
  base_url: string
  /** Always starts blank: the stored key is never sent to the client, and a
   * blank field means "leave it alone", not "clear it". */
  api_key: string
  cpu: string
  ram: string
  gpu: string
  notes: string
  platform: EndpointPlatform
  default_params: Record<string, unknown> | null
  is_global: boolean
}

const form = ref<EndpointFormState>({
  name: '',
  base_url: '',
  api_key: '',
  cpu: '',
  ram: '',
  gpu: '',
  notes: '',
  platform: 'generic',
  default_params: null,
  is_global: false,
})

/** Clearing a stored key has to be deliberate — see `buildInput`. */
const clearApiKey = ref(false)

function applyEndpoint(row: Endpoint) {
  endpoint.value = row
  form.value = {
    name: row.name,
    base_url: row.base_url,
    api_key: '',
    cpu: row.cpu ?? '',
    ram: row.ram ?? '',
    gpu: row.gpu ?? '',
    notes: row.notes ?? '',
    platform: row.platform,
    default_params: row.default_params,
    is_global: row.is_global,
  }
  clearApiKey.value = false
}

async function load() {
  loading.value = true
  loadError.value = null
  try {
    const [endpointRow, modelRows] = await Promise.all([
      endpointsApi.get(endpointId.value),
      endpointsApi.listModels(endpointId.value),
    ])
    applyEndpoint(endpointRow)
    models.value = modelRows
  } catch (err) {
    loadError.value = err instanceof ApiError ? err.message : 'Failed to load the endpoint.'
  } finally {
    loading.value = false
  }
}

onMounted(load)
watch(endpointId, load)

// --- save --------------------------------------------------------------

const saving = ref(false)
const saveError = ref<string | null>(null)

/** `api_key` is the one field this form does not replace wholesale: the route
 * treats it patch-like, so it is present in the body only when the admin typed
 * a new key or explicitly asked for the stored one to be removed. Anything
 * else — including a save that never touched the field — leaves it intact.
 */
function buildInput(): EndpointInput {
  const input: EndpointInput = {
    name: form.value.name,
    base_url: form.value.base_url,
    cpu: form.value.cpu || null,
    ram: form.value.ram || null,
    gpu: form.value.gpu || null,
    notes: form.value.notes || null,
    // The route treats both patch-like (omitted = keep the stored value, same
    // as `api_key`), but this form knows the fields and edits them, so it
    // always sends them — that is what lets clearing the editor clear the row.
    platform: form.value.platform,
    default_params: form.value.default_params,
  }
  if (clearApiKey.value) {
    input.api_key = null
  } else if (form.value.api_key.length > 0) {
    input.api_key = form.value.api_key
  }
  // Only the Base workspace can see or change this flag (the checkbox is not
  // rendered elsewhere) — omitting it outside Base leaves the stored value
  // untouched, matching the route's patch-like handling of the field.
  if (auth.isBaseWorkspace) {
    input.is_global = form.value.is_global
  }
  return input
}

async function save() {
  saveError.value = null
  saving.value = true
  try {
    const updated = await endpointsApi.update(endpointId.value, buildInput())
    applyEndpoint(updated)
    toast.add({ severity: 'success', summary: 'Endpoint saved', life: 5000 })
  } catch (err) {
    saveError.value = err instanceof ApiError ? err.message : 'Failed to save the endpoint.'
  } finally {
    saving.value = false
  }
}

// --- test connection -----------------------------------------------------

const testing = ref(false)

async function testConnection() {
  testing.value = true
  try {
    const result = await endpointsApi.test(endpointId.value)
    if (result.ok) {
      toast.add({
        severity: 'success',
        summary: `Reachable — HTTP ${result.status} in ${result.latency_ms}ms`,
        life: 5000,
      })
    } else {
      toast.add({ severity: 'error', summary: 'Test connection failed', detail: result.error, life: 5000 })
    }
  } catch (err) {
    toast.add({
      severity: 'error',
      summary: 'Test connection failed',
      detail: err instanceof ApiError ? err.message : 'Request failed unexpectedly.',
      life: 5000,
    })
  } finally {
    testing.value = false
  }
}

// --- discover models -----------------------------------------------------

const discovering = ref(false)

async function discoverModels() {
  discovering.value = true
  try {
    const result = await endpointsApi.discover(endpointId.value)
    if (result.ok) {
      toast.add({
        severity: 'success',
        summary: `Found ${result.discovered} model${result.discovered === 1 ? '' : 's'}`,
        detail: result.retired > 0 ? `${result.retired} no longer loaded` : undefined,
        life: 5000,
      })
      models.value = await endpointsApi.listModels(endpointId.value)
    } else {
      toast.add({ severity: 'error', summary: 'Discovery failed', detail: result.error, life: 5000 })
    }
  } catch (err) {
    toast.add({
      severity: 'error',
      summary: 'Discovery failed',
      detail: err instanceof ApiError ? err.message : 'Request failed unexpectedly.',
      life: 5000,
    })
  } finally {
    discovering.value = false
  }
}

// --- add model manually --------------------------------------------------

const newModelId = ref('')
const addingModel = ref(false)

async function addModel() {
  if (!newModelId.value.trim()) return
  addingModel.value = true
  try {
    await endpointsApi.addModel(endpointId.value, newModelId.value.trim())
    newModelId.value = ''
    models.value = await endpointsApi.listModels(endpointId.value)
  } catch (err) {
    toast.add({
      severity: 'error',
      summary: 'Failed to add the model',
      detail: err instanceof ApiError ? err.message : undefined,
      life: 5000,
    })
  } finally {
    addingModel.value = false
  }
}

// --- delete ----------------------------------------------------------

const deleting = ref(false)

function confirmDelete() {
  if (!endpoint.value) return
  confirm.require({
    header: 'Delete endpoint',
    message: `Delete endpoint "${endpoint.value.name}"? Past runs keep their own frozen copies.`,
    acceptProps: { label: 'Delete', severity: 'danger' },
    rejectProps: { label: 'Cancel', text: true },
    accept: () => void removeEndpoint(),
  })
}

async function removeEndpoint() {
  deleting.value = true
  try {
    await endpointsApi.remove(endpointId.value)
    await router.push('/endpoints')
  } catch (err) {
    toast.add({
      severity: 'error',
      summary: 'Failed to delete endpoint',
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

    <template v-if="!loading && endpoint">
      <div class="page-heading">
        <h1>
          {{ endpoint.name }}
          <Tag v-if="endpoint.is_global" value="Global" severity="info" />
        </h1>
        <p class="mono">{{ endpoint.base_url }}</p>
        <Message v-if="!endpoint.editable" severity="info" :closable="false">
          Shared from the Base workspace. Switch to Base to change it.
        </Message>
      </div>

      <div class="probe-actions">
        <Button
          v-if="auth.canAdminister"
          label="Test connection"
          severity="secondary"
          outlined
          :loading="testing"
          @click="testConnection"
        />
        <Button
          v-if="auth.canWrite"
          label="Discover models"
          severity="secondary"
          outlined
          :loading="discovering"
          @click="discoverModels"
        />
      </div>

      <div class="page-grid" :class="{ single: !showDetailsPanel }">
        <section v-if="showDetailsPanel" class="panel">
          <h2>Details</h2>
          <form class="dialog-form" @submit.prevent="save">
            <div class="field-row">
              <div class="field">
                <label for="endpoint-name">Name *</label>
                <InputText id="endpoint-name" v-model="form.name" required />
              </div>
              <div class="field">
                <label for="endpoint-base-url">Base URL *</label>
                <InputText id="endpoint-base-url" v-model="form.base_url" required />
              </div>
            </div>
            <div class="field">
              <label for="endpoint-api-key">API key</label>
              <Password
                id="endpoint-api-key"
                v-model="form.api_key"
                :feedback="false"
                toggle-mask
                :disabled="clearApiKey"
                :placeholder="endpoint.has_api_key ? 'leave blank to keep the stored key' : 'optional'"
                input-class="w-full"
              />
              <p v-if="endpoint.has_api_key" class="hint">
                An API key is stored — leave this blank to keep it, or type a new one to replace it.
              </p>
              <label v-if="endpoint.has_api_key" class="checkbox-option" for="endpoint-clear-api-key">
                <Checkbox v-model="clearApiKey" binary input-id="endpoint-clear-api-key" />
                Remove the stored key on save
              </label>
            </div>
            <div class="field">
              <label for="endpoint-platform">Platform</label>
              <Select
                id="endpoint-platform"
                v-model="form.platform"
                :options="platformOptions"
                option-label="label"
                option-value="value"
              />
              <p class="hint">Drives which parameter suggestions the default-parameters editor below offers.</p>
            </div>
            <div class="field-row three">
              <div class="field">
                <label for="endpoint-cpu">CPU</label>
                <InputText id="endpoint-cpu" v-model="form.cpu" class="w-full" />
              </div>
              <div class="field">
                <label for="endpoint-ram">RAM</label>
                <InputText id="endpoint-ram" v-model="form.ram" class="w-full" />
              </div>
              <div class="field">
                <label for="endpoint-gpu">GPU</label>
                <InputText id="endpoint-gpu" v-model="form.gpu" class="w-full" />
              </div>
            </div>
            <div class="field">
              <label for="endpoint-notes">Notes</label>
              <Textarea id="endpoint-notes" v-model="form.notes" rows="3" auto-resize />
            </div>
            <label v-if="auth.isBaseWorkspace" class="checkbox-option" for="endpoint-is-global">
              <Checkbox v-model="form.is_global" binary input-id="endpoint-is-global" />
              Global — share this endpoint with every workspace
            </label>

            <div class="field">
              <span class="label">Default parameters</span>
              <p class="hint">
                Extra request-body params sent on every run against this endpoint, merged under
                that run's own params (the run's keys win).
              </p>
              <ParamsEditor v-model="form.default_params" :platform="form.platform" />
            </div>

            <p class="hint">
              Created {{ formatDateTime(endpoint.created_at) }} · Updated
              {{ formatDateTime(endpoint.updated_at) }}
            </p>

            <Message v-if="saveError" severity="error" :closable="false">{{ saveError }}</Message>
            <div class="dialog-actions start">
              <Button type="submit" label="Save changes" :loading="saving" />
            </div>
          </form>

          <div class="danger-zone">
            <Button
              label="Delete endpoint"
              severity="danger"
              outlined
              :loading="deleting"
              @click="confirmDelete"
            />
          </div>
        </section>

        <section class="panel">
          <h2>Models</h2>
          <DataTable :value="models" :loading="loading" data-key="id" class="table list-table" removable-sort>
            <template #empty>No models yet — discover or add one manually below.</template>
            <Column field="model_id" header="Model ID" sortable>
              <template #body="{ data }: { data: EndpointModel }">
                <span class="mono">{{ data.model_id }}</span>
              </template>
            </Column>
            <Column field="currently_loaded" header="Status" sortable>
              <template #body="{ data }: { data: EndpointModel }">
                <Tag
                  :value="data.currently_loaded ? 'loaded' : 'not loaded'"
                  :severity="data.currently_loaded ? 'success' : 'secondary'"
                />
              </template>
            </Column>
            <Column field="source" header="Source" sortable />
            <Column field="first_seen_at" header="First seen" sortable>
              <template #body="{ data }: { data: EndpointModel }">{{
                formatDateTime(data.first_seen_at)
              }}</template>
            </Column>
            <Column field="last_seen_at" header="Last seen" sortable>
              <template #body="{ data }: { data: EndpointModel }">{{
                formatDateTime(data.last_seen_at)
              }}</template>
            </Column>
          </DataTable>

          <form v-if="auth.canAdminister" class="add-model-form" @submit.prevent="addModel">
            <div class="field grow">
              <label for="new-model-id">Add model manually</label>
              <InputText id="new-model-id" v-model="newModelId" placeholder="llama-3.1-8b-instruct" />
            </div>
            <Button type="submit" label="Add model" severity="secondary" outlined :loading="addingModel" />
          </form>
        </section>
      </div>
    </template>
  </div>
</template>

<style scoped>
.page {
  max-width: 90rem;
}

/* Details (left) and Models (right) side by side on wide screens. Details is
 * only rendered for admins on editable endpoints (see `showDetailsPanel`) —
 * `.single` drops to one track so Models doesn't get stranded in a 1fr column
 * with an empty 1.2fr beside it. */
.page-grid {
  display: grid;
  grid-template-columns: 1fr 1.2fr;
  gap: 1.5rem;
  align-items: start;
}

.page-grid.single {
  grid-template-columns: 1fr;
}

/* `rem` inside `@media` resolves against the browser's 16px default, never
 * this app's 17px root — see the note in src/style.css. Written in `px` here
 * to name the actual pixel breakpoint rather than imply a false precision. */
@media (max-width: 1100px) {
  .page-grid {
    grid-template-columns: 1fr;
  }
}

/* The heading carries a Tag beside the name, so it lays its children out on
 * one line instead of taking the global's plain block flow. */
.page-heading h1 {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

/* The base URL under the heading is a `<p>`, which the global paragraph rule
 * would give a bottom margin the heading block does not want. */
.mono {
  margin: 0;
}

.probe-actions {
  display: flex;
  gap: 0.75rem;
  flex-wrap: wrap;
}

.field-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 1rem;
}

.field-row.three {
  grid-template-columns: 1fr 1fr 1fr;
}

.field {
  /* Grid items default to min-width:auto, which lets an InputText's intrinsic
   * width push a three-column row wider than its track and bleed out of the
   * panel. 0 lets the 1fr tracks actually constrain it. */
  min-width: 0;
}

.field.grow {
  flex: 1;
}

.dialog-actions.start {
  justify-content: flex-start;
}

.add-model-form {
  display: flex;
  align-items: flex-end;
  gap: 0.75rem;
  max-width: 26rem;
}
</style>
