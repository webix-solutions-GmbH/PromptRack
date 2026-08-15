<script setup lang="ts">
// Endpoints list. An endpoint is any OpenAI-compatible base URL — a local box,
// a proxy, or a hosted API — plus an optional API key and free-text hardware
// notes; creating/editing one is admin-only, since it holds credentials;
// every signed-in user can still read the list to pick an endpoint for a run.
import { onMounted, ref } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import Button from 'primevue/button'
import Checkbox from 'primevue/checkbox'
import Column from 'primevue/column'
import DataTable from 'primevue/datatable'
import type { DataTableRowClickEvent } from 'primevue/datatable'
import Dialog from 'primevue/dialog'
import InputText from 'primevue/inputtext'
import Message from 'primevue/message'
import Password from 'primevue/password'
import Select from 'primevue/select'
import Tag from 'primevue/tag'
import Textarea from 'primevue/textarea'
import { useToast } from 'primevue/usetoast'
import { endpointsApi, type Endpoint, type EndpointPlatform } from '../api/endpoints'
import { ApiError } from '../api/client'
import { formatDateTime } from '../lib/format'
import { PARAM_CATALOG } from '../lib/paramCatalog'
import { useAuthStore } from '../stores/auth'

/** Options for the platform `Select` — value is the catalog key, label its
 * `PlatformCatalog.label`. Built from the catalog itself so a new platform
 * added there needs no matching change here. */
const platformOptions = (Object.keys(PARAM_CATALOG) as EndpointPlatform[]).map((key) => ({
  label: PARAM_CATALOG[key].label,
  value: key,
}))

const auth = useAuthStore()
const toast = useToast()
const router = useRouter()

// Same row-navigation contract as the other list views: the row opens the
// endpoint, anchors/buttons inside a cell keep their own actions.
function onRowClick(event: DataTableRowClickEvent) {
  const target = event.originalEvent.target as HTMLElement | null
  if (target?.closest('a, button')) return
  void router.push(`/endpoints/${(event.data as Endpoint).id}`)
}

const endpoints = ref<Endpoint[]>([])
const loading = ref(true)
const loadError = ref<string | null>(null)

async function load() {
  loading.value = true
  loadError.value = null
  try {
    endpoints.value = await endpointsApi.list()
  } catch (err) {
    loadError.value = err instanceof ApiError ? err.message : 'Failed to load endpoints.'
  } finally {
    loading.value = false
  }
}

onMounted(load)

// --- create dialog -----------------------------------------------------

// Plain strings for every field, independent of `EndpointInput`'s optional
// `string | null` shape — PrimeVue inputs bind to `string`, so the null/empty
// distinction is resolved once, at submit time, rather than on every field.
interface EndpointFormState {
  name: string
  base_url: string
  api_key: string
  cpu: string
  ram: string
  gpu: string
  notes: string
  platform: EndpointPlatform
  is_global: boolean
}

const dialogOpen = ref(false)
const form = ref<EndpointFormState>(emptyForm())
const formError = ref<string | null>(null)
const saving = ref(false)

function emptyForm(): EndpointFormState {
  return {
    name: '',
    base_url: '',
    api_key: '',
    cpu: '',
    ram: '',
    gpu: '',
    notes: '',
    platform: 'generic',
    is_global: false,
  }
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
    await endpointsApi.create({
      name: form.value.name,
      base_url: form.value.base_url,
      api_key: form.value.api_key || null,
      cpu: form.value.cpu || null,
      ram: form.value.ram || null,
      gpu: form.value.gpu || null,
      notes: form.value.notes || null,
      platform: form.value.platform,
      is_global: form.value.is_global,
    })
    toast.add({ severity: 'success', summary: 'Endpoint created', life: 5000 })
    dialogOpen.value = false
    await load()
  } catch (err) {
    formError.value = err instanceof ApiError ? err.message : 'Failed to create the endpoint.'
  } finally {
    saving.value = false
  }
}

// --- test connection, before the endpoint row exists ----------------------

const testingConnection = ref(false)

async function testConnection() {
  testingConnection.value = true
  try {
    const result = await endpointsApi.testConnection(form.value.base_url, form.value.api_key || null)
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
    testingConnection.value = false
  }
}
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div class="page-heading">
        <h1>Endpoints</h1>
        <p class="subtitle">
          OpenAI-compatible endpoints that serve the models you are evaluating — Ollama, LM
          Studio or vLLM on your own hardware, a proxy, or a hosted API.
        </p>
      </div>
      <Button v-if="auth.canAdminister" label="New endpoint" icon="pi pi-plus" @click="openCreate" />
    </div>

    <Message v-if="loadError" severity="error" :closable="false">{{ loadError }}</Message>

    <DataTable
      :value="endpoints"
      :loading="loading"
      data-key="id"
      class="table list-table row-nav"
      removable-sort
      @row-click="onRowClick"
    >
      <template #empty>No endpoints yet — add one with "New endpoint".</template>
      <Column field="name" header="Name" sortable>
        <template #body="{ data }: { data: Endpoint }">
          <div class="name-cell">
            <RouterLink :to="`/endpoints/${data.id}`" class="name-link">{{ data.name }}</RouterLink>
            <Tag v-if="data.is_global" value="Global" severity="info" />
            <Tag v-if="data.platform !== 'generic'" :value="data.platform" severity="secondary" />
          </div>
        </template>
      </Column>
      <Column field="base_url" header="Base URL" sortable>
        <template #body="{ data }: { data: Endpoint }">
          <span class="mono">{{ data.base_url }}</span>
        </template>
      </Column>
      <Column field="gpu" header="GPU" sortable>
        <template #body="{ data }: { data: Endpoint }">{{ data.gpu ?? '—' }}</template>
      </Column>
      <Column header="Models">
        <template #body="{ data }: { data: Endpoint }">
          {{ data.loaded_model_count }}/{{ data.model_count }} loaded
        </template>
      </Column>
      <Column header="Created">
        <template #body="{ data }: { data: Endpoint }">{{ formatDateTime(data.created_at) }}</template>
      </Column>
    </DataTable>

    <Dialog v-model:visible="dialogOpen" modal header="New endpoint" class="form-dialog">
      <form class="dialog-form" @submit.prevent="submitForm">
        <div class="field-row">
          <div class="field">
            <label for="endpoint-name">Name *</label>
            <InputText id="endpoint-name" v-model="form.name" required placeholder="vllm-box" autofocus />
          </div>
          <div class="field">
            <label for="endpoint-base-url">Base URL *</label>
            <InputText
              id="endpoint-base-url"
              v-model="form.base_url"
              required
              placeholder="http://vllm:8000/v1"
            />
          </div>
        </div>
        <div class="field">
          <label for="endpoint-api-key">API key</label>
          <Password
            id="endpoint-api-key"
            v-model="form.api_key"
            :feedback="false"
            toggle-mask
            placeholder="optional"
            input-class="w-full"
          />
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
          <p class="hint">Drives which parameter suggestions the endpoint's editor page offers.</p>
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

        <Message v-if="formError" severity="error" :closable="false">{{ formError }}</Message>
        <div class="dialog-actions" :class="{ split: auth.canAdminister }">
          <Button
            v-if="auth.canAdminister"
            type="button"
            label="Test connection"
            severity="secondary"
            outlined
            :disabled="!form.base_url.trim() || testingConnection"
            :loading="testingConnection"
            @click="testConnection"
          />
          <div class="dialog-actions-right">
            <Button type="button" label="Cancel" text @click="dialogOpen = false" />
            <Button type="submit" label="Create endpoint" :loading="saving" />
          </div>
        </div>
      </form>
    </Dialog>
  </div>
</template>

<style scoped>
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
   * dialog. 0 lets the 1fr tracks actually constrain it. */
  min-width: 0;
}

/* Only when the Test connection button actually renders (canAdminister) —
 * otherwise a lone .dialog-actions-right would land at flex-start instead of
 * the trailing edge under plain space-between. */
.dialog-actions.split {
  justify-content: space-between;
}

.dialog-actions-right {
  display: flex;
  gap: 0.5rem;
}
</style>
