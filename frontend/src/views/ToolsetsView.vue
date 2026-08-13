<script setup lang="ts">
// Toolsets list. Creating/editing a toolset is admin-only — it holds
// `mcp_url` and headers, which are credentials — but every member reads the
// list to pick toolsets for a test case.
import { onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import Button from 'primevue/button'
import Column from 'primevue/column'
import DataTable from 'primevue/datatable'
import Dialog from 'primevue/dialog'
import InputText from 'primevue/inputtext'
import Message from 'primevue/message'
import SelectButton from 'primevue/selectbutton'
import Tag from 'primevue/tag'
import Textarea from 'primevue/textarea'
import { useToast } from 'primevue/usetoast'
import { toolsetsApi, type Toolset, type ToolsetKind } from '../api/toolsets'
import { ApiError } from '../api/client'
import { formatDateTime } from '../lib/format'
import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()
const toast = useToast()

const toolsets = ref<Toolset[]>([])
const loading = ref(true)
const loadError = ref<string | null>(null)

async function load() {
  loading.value = true
  loadError.value = null
  try {
    toolsets.value = await toolsetsApi.list()
  } catch (err) {
    loadError.value = err instanceof ApiError ? err.message : 'Failed to load toolsets.'
  } finally {
    loading.value = false
  }
}

onMounted(load)

// --- create dialog -----------------------------------------------------

const kindOptions: { label: string; value: ToolsetKind }[] = [
  { label: 'Manual', value: 'manual' },
  { label: 'MCP', value: 'mcp' },
]

interface ToolsetFormState {
  name: string
  description: string
  kind: ToolsetKind
  mcp_url: string
  mcp_headers: string
}

function emptyForm(): ToolsetFormState {
  return { name: '', description: '', kind: 'manual', mcp_url: '', mcp_headers: '' }
}

const dialogOpen = ref(false)
const form = ref<ToolsetFormState>(emptyForm())
const formError = ref<string | null>(null)
const saving = ref(false)

function openCreate() {
  form.value = emptyForm()
  formError.value = null
  dialogOpen.value = true
}

async function submitForm() {
  formError.value = null
  saving.value = true
  try {
    await toolsetsApi.create({
      name: form.value.name,
      description: form.value.description || null,
      kind: form.value.kind,
      mcp_url: form.value.kind === 'mcp' ? form.value.mcp_url || null : null,
      mcp_headers: form.value.kind === 'mcp' ? form.value.mcp_headers || null : null,
    })
    toast.add({ severity: 'success', summary: 'Toolset created', life: 3000 })
    dialogOpen.value = false
    await load()
  } catch (err) {
    formError.value = err instanceof ApiError ? err.message : 'Failed to create the toolset.'
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div class="page-heading">
        <h1>Toolsets</h1>
        <p class="subtitle">
          Bundles of callable functions a test case can be run with. A manual toolset answers with
          canned responses, which keeps a multi-turn test deterministic; an MCP toolset discovers
          its tools from a server over HTTP and really executes them.
        </p>
      </div>
      <Button v-if="auth.canAdminister" label="New toolset" icon="pi pi-plus" @click="openCreate" />
    </div>

    <Message v-if="loadError" severity="error" :closable="false">{{ loadError }}</Message>

    <DataTable :value="toolsets" :loading="loading" data-key="id" class="table">
      <template #empty>No toolsets yet — add one with "New toolset".</template>
      <Column field="name" header="Name">
        <template #body="{ data }: { data: Toolset }">
          <div class="name-cell">
            <RouterLink :to="`/toolsets/${data.id}`" class="name-link">{{ data.name }}</RouterLink>
            <Tag :value="data.kind === 'mcp' ? 'MCP' : 'manual'" :severity="data.kind === 'mcp' ? 'info' : 'secondary'" />
          </div>
          <span v-if="data.description" class="description">{{ data.description }}</span>
        </template>
      </Column>
      <Column header="Tools">
        <template #body="{ data }: { data: Toolset }">
          {{ data.enabled_tool_count }}/{{ data.tool_count }} enabled
        </template>
      </Column>
      <Column header="Updated">
        <template #body="{ data }: { data: Toolset }">{{ formatDateTime(data.updated_at) }}</template>
      </Column>
    </DataTable>

    <Dialog v-model:visible="dialogOpen" modal header="New toolset" class="form-dialog">
      <form class="dialog-form" @submit.prevent="submitForm">
        <div class="field">
          <label for="toolset-name">Name *</label>
          <InputText id="toolset-name" v-model="form.name" required placeholder="Odoo ERP" autofocus />
        </div>
        <div class="field">
          <label for="toolset-description">Description</label>
          <InputText
            id="toolset-description"
            v-model="form.description"
            placeholder="Read-only product and partner lookups"
          />
        </div>
        <div class="field">
          <label>Kind</label>
          <SelectButton v-model="form.kind" :options="kindOptions" option-label="label" option-value="value" />
        </div>
        <template v-if="form.kind === 'mcp'">
          <div class="field">
            <label for="toolset-mcp-url">MCP URL *</label>
            <InputText id="toolset-mcp-url" v-model="form.mcp_url" placeholder="https://mcp.example.com" />
          </div>
          <div class="field">
            <label for="toolset-mcp-headers">Headers (JSON)</label>
            <Textarea
              id="toolset-mcp-headers"
              v-model="form.mcp_headers"
              rows="3"
              auto-resize
              placeholder='{"Authorization": "Bearer …"}'
            />
          </div>
        </template>
        <Message v-if="formError" severity="error" :closable="false">{{ formError }}</Message>
        <div class="dialog-actions">
          <Button type="button" label="Cancel" text @click="dialogOpen = false" />
          <Button type="submit" label="Create toolset" :loading="saving" />
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

.name-cell {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.name-link {
  font-weight: 500;
  color: var(--p-text-color);
  text-decoration: none;
}

.name-link:hover {
  text-decoration: underline;
}

.description {
  display: block;
  font-size: 0.8125rem;
  color: var(--p-text-muted-color);
  margin-top: 0.125rem;
}

.form-dialog {
  width: 32rem;
  max-width: 90vw;
}

.dialog-form {
  display: flex;
  flex-direction: column;
  gap: 1rem;
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

.dialog-actions {
  display: flex;
  justify-content: flex-end;
  gap: 0.5rem;
}
</style>
