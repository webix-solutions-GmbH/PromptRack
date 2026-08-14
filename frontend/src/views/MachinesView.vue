<script setup lang="ts">
// Machines list. A machine IS an endpoint (base URL + optional API key +
// free-text hardware notes) — creating/editing one is admin-only, since it
// holds credentials; every signed-in user can still read the list to pick a
// machine for a run.
import { onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import Button from 'primevue/button'
import Column from 'primevue/column'
import DataTable from 'primevue/datatable'
import Dialog from 'primevue/dialog'
import InputText from 'primevue/inputtext'
import Message from 'primevue/message'
import Password from 'primevue/password'
import Textarea from 'primevue/textarea'
import { useToast } from 'primevue/usetoast'
import { machinesApi, type Machine } from '../api/machines'
import { ApiError } from '../api/client'
import { formatDateTime } from '../lib/format'
import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()
const toast = useToast()

const machines = ref<Machine[]>([])
const loading = ref(true)
const loadError = ref<string | null>(null)

async function load() {
  loading.value = true
  loadError.value = null
  try {
    machines.value = await machinesApi.list()
  } catch (err) {
    loadError.value = err instanceof ApiError ? err.message : 'Failed to load machines.'
  } finally {
    loading.value = false
  }
}

onMounted(load)

// --- create dialog -----------------------------------------------------

// Plain strings for every field, independent of `MachineInput`'s optional
// `string | null` shape — PrimeVue inputs bind to `string`, so the null/empty
// distinction is resolved once, at submit time, rather than on every field.
interface MachineFormState {
  name: string
  base_url: string
  api_key: string
  cpu: string
  ram: string
  gpu: string
  notes: string
}

const dialogOpen = ref(false)
const form = ref<MachineFormState>(emptyForm())
const formError = ref<string | null>(null)
const saving = ref(false)

function emptyForm(): MachineFormState {
  return { name: '', base_url: '', api_key: '', cpu: '', ram: '', gpu: '', notes: '' }
}

function openCreate() {
  form.value = emptyForm()
  formError.value = null
  dialogOpen.value = true
}

async function submitForm() {
  formError.value = null
  saving.value = true
  try {
    await machinesApi.create({
      name: form.value.name,
      base_url: form.value.base_url,
      api_key: form.value.api_key || null,
      cpu: form.value.cpu || null,
      ram: form.value.ram || null,
      gpu: form.value.gpu || null,
      notes: form.value.notes || null,
    })
    toast.add({ severity: 'success', summary: 'Machine created', life: 3000 })
    dialogOpen.value = false
    await load()
  } catch (err) {
    formError.value = err instanceof ApiError ? err.message : 'Failed to create the machine.'
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div class="page-heading">
        <h1>Machines</h1>
        <p class="subtitle">
          OpenAI-compatible endpoints that serve the models you are evaluating — Ollama, LM
          Studio or vLLM on your own hardware, or a hosted API.
        </p>
      </div>
      <Button v-if="auth.canAdminister" label="New machine" icon="pi pi-plus" @click="openCreate" />
    </div>

    <Message v-if="loadError" severity="error" :closable="false">{{ loadError }}</Message>

    <DataTable :value="machines" :loading="loading" data-key="id" class="table">
      <template #empty>No machines yet — add one with "New machine".</template>
      <Column field="name" header="Name">
        <template #body="{ data }: { data: Machine }">
          <RouterLink :to="`/machines/${data.id}`" class="name-link">{{ data.name }}</RouterLink>
        </template>
      </Column>
      <Column field="base_url" header="Base URL">
        <template #body="{ data }: { data: Machine }">
          <span class="mono">{{ data.base_url }}</span>
        </template>
      </Column>
      <Column field="gpu" header="GPU">
        <template #body="{ data }: { data: Machine }">{{ data.gpu ?? '—' }}</template>
      </Column>
      <Column header="Models">
        <template #body="{ data }: { data: Machine }">
          {{ data.loaded_model_count }}/{{ data.model_count }} loaded
        </template>
      </Column>
      <Column header="Created">
        <template #body="{ data }: { data: Machine }">{{ formatDateTime(data.created_at) }}</template>
      </Column>
    </DataTable>

    <Dialog v-model:visible="dialogOpen" modal header="New machine" class="form-dialog">
      <form class="dialog-form" @submit.prevent="submitForm">
        <div class="field-row">
          <div class="field">
            <label for="machine-name">Name *</label>
            <InputText id="machine-name" v-model="form.name" required placeholder="vllm-box" autofocus />
          </div>
          <div class="field">
            <label for="machine-base-url">Base URL *</label>
            <InputText
              id="machine-base-url"
              v-model="form.base_url"
              required
              placeholder="http://vllm:8000/v1"
            />
          </div>
        </div>
        <div class="field">
          <label for="machine-api-key">API key</label>
          <Password
            id="machine-api-key"
            v-model="form.api_key"
            :feedback="false"
            toggle-mask
            placeholder="optional"
            input-class="w-full"
          />
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
        <Message v-if="formError" severity="error" :closable="false">{{ formError }}</Message>
        <div class="dialog-actions">
          <Button type="button" label="Cancel" text @click="dialogOpen = false" />
          <Button type="submit" label="Create machine" :loading="saving" />
        </div>
      </form>
    </Dialog>
  </div>
</template>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  gap: 1.5rem;
}

.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 1rem;
}

.page-heading h1 {
  font-size: 1.5rem;
  font-weight: 600;
  margin: 0 0 0.375rem;
}

.subtitle {
  max-width: 48rem;
  color: var(--p-text-muted-color);
  font-size: 0.875rem;
  margin: 0;
}

.name-link {
  font-weight: 500;
  color: var(--p-text-color);
  text-decoration: none;
}

.name-link:hover {
  text-decoration: underline;
}

.mono {
  font-family: var(--p-font-family-mono, ui-monospace, monospace);
  font-size: 0.8125rem;
  color: var(--p-text-muted-color);
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

.field label {
  font-size: 0.8125rem;
  font-weight: 500;
  color: var(--p-text-muted-color);
}

.w-full {
  width: 100%;
}

.dialog-actions {
  display: flex;
  justify-content: flex-end;
  gap: 0.5rem;
}
</style>
