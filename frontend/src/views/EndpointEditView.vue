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
import Tag from 'primevue/tag'
import Textarea from 'primevue/textarea'
import { useConfirm } from 'primevue/useconfirm'
import { useToast } from 'primevue/usetoast'
import { endpointsApi, type Endpoint, type EndpointInput, type EndpointModel } from '../api/endpoints'
import { ApiError } from '../api/client'
import { formatDateTime } from '../lib/format'
import { useAuthStore } from '../stores/auth'

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
    toast.add({ severity: 'success', summary: 'Endpoint saved', life: 3000 })
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
        life: 4000,
      })
    } else {
      toast.add({ severity: 'error', summary: 'Test connection failed', detail: result.error, life: 6000 })
    }
  } catch (err) {
    toast.add({
      severity: 'error',
      summary: 'Test connection failed',
      detail: err instanceof ApiError ? err.message : 'Request failed unexpectedly.',
      life: 6000,
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
        life: 4000,
      })
      models.value = await endpointsApi.listModels(endpointId.value)
    } else {
      toast.add({ severity: 'error', summary: 'Discovery failed', detail: result.error, life: 6000 })
    }
  } catch (err) {
    toast.add({
      severity: 'error',
      summary: 'Discovery failed',
      detail: err instanceof ApiError ? err.message : 'Request failed unexpectedly.',
      life: 6000,
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
    message: `Delete endpoint "${endpoint.value.name}"? This cannot be undone.`,
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

      <section v-if="auth.canAdminister && endpoint.editable" class="panel">
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
          <div class="field-row three">
            <div class="field">
              <label for="endpoint-cpu">CPU</label>
              <InputText id="endpoint-cpu" v-model="form.cpu" />
            </div>
            <div class="field">
              <label for="endpoint-ram">RAM</label>
              <InputText id="endpoint-ram" v-model="form.ram" />
            </div>
            <div class="field">
              <label for="endpoint-gpu">GPU</label>
              <InputText id="endpoint-gpu" v-model="form.gpu" />
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

          <p class="meta">
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
        <DataTable :value="models" data-key="id" class="table">
          <template #empty>No models yet — discover or add one manually below.</template>
          <Column field="model_id" header="Model ID">
            <template #body="{ data }: { data: EndpointModel }">
              <span class="mono">{{ data.model_id }}</span>
            </template>
          </Column>
          <Column header="Status">
            <template #body="{ data }: { data: EndpointModel }">
              <Tag
                :value="data.currently_loaded ? 'loaded' : 'not loaded'"
                :severity="data.currently_loaded ? 'success' : 'secondary'"
              />
            </template>
          </Column>
          <Column field="source" header="Source" />
          <Column header="First seen">
            <template #body="{ data }: { data: EndpointModel }">{{
              formatDateTime(data.first_seen_at)
            }}</template>
          </Column>
          <Column header="Last seen">
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
          <Button type="submit" label="Add" severity="secondary" outlined :loading="addingModel" />
        </form>
      </section>
    </template>
  </div>
</template>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  gap: 1.5rem;
  max-width: 48rem;
}

.page-heading h1 {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 1.5rem;
  font-weight: 600;
  margin: 0 0 0.25rem;
}

.mono {
  font-family: var(--p-font-family-mono, ui-monospace, monospace);
  font-size: 0.875rem;
  color: var(--p-text-muted-color);
  margin: 0;
}

.probe-actions {
  display: flex;
  gap: 0.75rem;
  flex-wrap: wrap;
}

.panel {
  display: flex;
  flex-direction: column;
  gap: 1rem;
  padding: 1.5rem;
  border: 1px solid var(--p-content-border-color);
  border-radius: var(--p-content-border-radius);
}

.panel h2 {
  font-size: 1.0625rem;
  font-weight: 600;
  margin: 0;
}

.dialog-form {
  display: flex;
  flex-direction: column;
  gap: 1rem;
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
  display: flex;
  flex-direction: column;
  gap: 0.375rem;
}

.field.grow {
  flex: 1;
}

.field label {
  font-size: 0.8125rem;
  font-weight: 500;
  color: var(--p-text-muted-color);
}

.w-full {
  width: 100%;
}

.meta {
  font-size: 0.75rem;
  color: var(--p-text-muted-color);
  margin: 0;
}

.hint {
  font-size: 0.75rem;
  color: var(--p-text-muted-color);
  margin: 0;
}

.checkbox-option {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.8125rem;
  font-weight: 400;
}

.dialog-actions {
  display: flex;
  gap: 0.5rem;
}

.dialog-actions.start {
  justify-content: flex-start;
}

.danger-zone {
  border-top: 1px solid var(--p-content-border-color);
  padding-top: 1rem;
}

.add-model-form {
  display: flex;
  align-items: flex-end;
  gap: 0.75rem;
  max-width: 26rem;
}
</style>
