<script setup lang="ts">
// Machine detail/edit. Test probes the endpoint with its stored credentials,
// so it is admin-only; discovery only reads model ids and every writer needs
// it (it is what the new-run page also triggers on machine select).
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
import { machinesApi, type Machine, type MachineInput, type MachineModel } from '../api/machines'
import { ApiError } from '../api/client'
import { formatDateTime } from '../lib/format'
import { useAuthStore } from '../stores/auth'

const props = defineProps<{ id: string }>()
const machineId = computed(() => Number(props.id))

const auth = useAuthStore()
const router = useRouter()
const confirm = useConfirm()
const toast = useToast()

const machine = ref<Machine | null>(null)
const models = ref<MachineModel[]>([])
const loading = ref(true)
const loadError = ref<string | null>(null)

interface MachineFormState {
  name: string
  base_url: string
  /** Always starts blank: the stored key is never sent to the client, and a
   * blank field means "leave it alone", not "clear it". */
  api_key: string
  cpu: string
  ram: string
  gpu: string
  notes: string
}

const form = ref<MachineFormState>({
  name: '',
  base_url: '',
  api_key: '',
  cpu: '',
  ram: '',
  gpu: '',
  notes: '',
})

/** Clearing a stored key has to be deliberate — see `buildInput`. */
const clearApiKey = ref(false)

function applyMachine(row: Machine) {
  machine.value = row
  form.value = {
    name: row.name,
    base_url: row.base_url,
    api_key: '',
    cpu: row.cpu ?? '',
    ram: row.ram ?? '',
    gpu: row.gpu ?? '',
    notes: row.notes ?? '',
  }
  clearApiKey.value = false
}

async function load() {
  loading.value = true
  loadError.value = null
  try {
    const [machineRow, modelRows] = await Promise.all([
      machinesApi.get(machineId.value),
      machinesApi.listModels(machineId.value),
    ])
    applyMachine(machineRow)
    models.value = modelRows
  } catch (err) {
    loadError.value = err instanceof ApiError ? err.message : 'Failed to load the machine.'
  } finally {
    loading.value = false
  }
}

onMounted(load)
watch(machineId, load)

// --- save --------------------------------------------------------------

const saving = ref(false)
const saveError = ref<string | null>(null)

/** `api_key` is the one field this form does not replace wholesale: the route
 * treats it patch-like, so it is present in the body only when the admin typed
 * a new key or explicitly asked for the stored one to be removed. Anything
 * else — including a save that never touched the field — leaves it intact.
 */
function buildInput(): MachineInput {
  const input: MachineInput = {
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
  return input
}

async function save() {
  saveError.value = null
  saving.value = true
  try {
    const updated = await machinesApi.update(machineId.value, buildInput())
    applyMachine(updated)
    toast.add({ severity: 'success', summary: 'Machine saved', life: 3000 })
  } catch (err) {
    saveError.value = err instanceof ApiError ? err.message : 'Failed to save the machine.'
  } finally {
    saving.value = false
  }
}

// --- test connection -----------------------------------------------------

const testing = ref(false)

async function testConnection() {
  testing.value = true
  try {
    const result = await machinesApi.test(machineId.value)
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
    const result = await machinesApi.discover(machineId.value)
    if (result.ok) {
      toast.add({
        severity: 'success',
        summary: `Found ${result.discovered} model${result.discovered === 1 ? '' : 's'}`,
        detail: result.retired > 0 ? `${result.retired} no longer loaded` : undefined,
        life: 4000,
      })
      models.value = await machinesApi.listModels(machineId.value)
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
    await machinesApi.addModel(machineId.value, newModelId.value.trim())
    newModelId.value = ''
    models.value = await machinesApi.listModels(machineId.value)
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
  if (!machine.value) return
  confirm.require({
    header: 'Delete machine',
    message: `Delete machine "${machine.value.name}"? This cannot be undone.`,
    acceptProps: { label: 'Delete', severity: 'danger' },
    rejectProps: { label: 'Cancel', text: true },
    accept: () => void removeMachine(),
  })
}

async function removeMachine() {
  deleting.value = true
  try {
    await machinesApi.remove(machineId.value)
    await router.push('/machines')
  } catch (err) {
    toast.add({
      severity: 'error',
      summary: 'Failed to delete machine',
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

    <template v-if="!loading && machine">
      <div class="page-heading">
        <h1>{{ machine.name }}</h1>
        <p class="mono">{{ machine.base_url }}</p>
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

      <section v-if="auth.canAdminister" class="panel">
        <h2>Details</h2>
        <form class="dialog-form" @submit.prevent="save">
          <div class="field-row">
            <div class="field">
              <label for="machine-name">Name *</label>
              <InputText id="machine-name" v-model="form.name" required />
            </div>
            <div class="field">
              <label for="machine-base-url">Base URL *</label>
              <InputText id="machine-base-url" v-model="form.base_url" required />
            </div>
          </div>
          <div class="field">
            <label for="machine-api-key">API key</label>
            <Password
              id="machine-api-key"
              v-model="form.api_key"
              :feedback="false"
              toggle-mask
              :disabled="clearApiKey"
              :placeholder="machine.has_api_key ? 'leave blank to keep the stored key' : 'optional'"
              input-class="w-full"
            />
            <p v-if="machine.has_api_key" class="hint">
              An API key is stored — leave this blank to keep it, or type a new one to replace it.
            </p>
            <label v-if="machine.has_api_key" class="checkbox-option" for="machine-clear-api-key">
              <Checkbox v-model="clearApiKey" binary input-id="machine-clear-api-key" />
              Remove the stored key on save
            </label>
          </div>
          <div class="field-row three">
            <div class="field">
              <label for="machine-cpu">CPU</label>
              <InputText id="machine-cpu" v-model="form.cpu" />
            </div>
            <div class="field">
              <label for="machine-ram">RAM</label>
              <InputText id="machine-ram" v-model="form.ram" />
            </div>
            <div class="field">
              <label for="machine-gpu">GPU</label>
              <InputText id="machine-gpu" v-model="form.gpu" />
            </div>
          </div>
          <div class="field">
            <label for="machine-notes">Notes</label>
            <Textarea id="machine-notes" v-model="form.notes" rows="3" auto-resize />
          </div>

          <p class="meta">
            Created {{ formatDateTime(machine.created_at) }} · Updated
            {{ formatDateTime(machine.updated_at) }}
          </p>

          <Message v-if="saveError" severity="error" :closable="false">{{ saveError }}</Message>
          <div class="dialog-actions start">
            <Button type="submit" label="Save changes" :loading="saving" />
          </div>
        </form>

        <div class="danger-zone">
          <Button
            label="Delete machine"
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
            <template #body="{ data }: { data: MachineModel }">
              <span class="mono">{{ data.model_id }}</span>
            </template>
          </Column>
          <Column header="Status">
            <template #body="{ data }: { data: MachineModel }">
              <Tag
                :value="data.currently_loaded ? 'loaded' : 'not loaded'"
                :severity="data.currently_loaded ? 'success' : 'secondary'"
              />
            </template>
          </Column>
          <Column field="source" header="Source" />
          <Column header="First seen">
            <template #body="{ data }: { data: MachineModel }">{{
              formatDateTime(data.first_seen_at)
            }}</template>
          </Column>
          <Column header="Last seen">
            <template #body="{ data }: { data: MachineModel }">{{
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
