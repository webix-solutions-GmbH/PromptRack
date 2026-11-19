<script setup lang="ts">
// Toolsets list. Every signed-in user reads it to pick toolsets for a test
// case. A member may create a *manual* toolset — canned responses, no
// credential anywhere in it — so the create dialog offers a member name and
// description only. The kind choice (an MCP toolset's URL and headers are
// credentials) and sharing are admin-only, and a member's request leaves those
// keys out entirely: the route refuses a member's body that names one.
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
import SelectButton from 'primevue/selectbutton'
import Tag from 'primevue/tag'
import Textarea from 'primevue/textarea'
import { useToast } from 'primevue/usetoast'
import {
  toolsetsApi,
  TOOLSET_KIND_OPTIONS,
  toolsetKindLabel,
  toolsetKindSeverity,
  type Toolset,
  type ToolsetKind,
} from '../api/toolsets'
import { ApiError } from '../api/client'
import { formatDateTime } from '../lib/format'
import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()
const toast = useToast()
const router = useRouter()

// Same row-navigation contract as the other list views: the row opens the
// toolset, anchors/buttons inside a cell keep their own actions.
function onRowClick(event: DataTableRowClickEvent) {
  const target = event.originalEvent.target as HTMLElement | null
  if (target?.closest('a, button')) return
  void router.push(`/toolsets/${(event.data as Toolset).id}`)
}

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

interface ToolsetFormState {
  name: string
  description: string
  kind: ToolsetKind
  mcp_url: string
  mcp_headers: string
  is_global: boolean
}

function emptyForm(): ToolsetFormState {
  return {
    name: '',
    description: '',
    kind: 'manual',
    mcp_url: '',
    mcp_headers: '',
    is_global: false,
  }
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
    await toolsetsApi.create(
      auth.canAdminister
        ? {
            name: form.value.name,
            description: form.value.description || null,
            kind: form.value.kind,
            mcp_url: form.value.kind === 'mcp' ? form.value.mcp_url || null : null,
            mcp_headers: form.value.kind === 'mcp' ? form.value.mcp_headers || null : null,
            is_global: form.value.is_global,
          }
        : { name: form.value.name, description: form.value.description || null, kind: 'manual' },
    )
    toast.add({ severity: 'success', summary: 'Toolset created', life: 5000 })
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
          its tools from a server over HTTP and really executes them; a documents toolset holds a
          markdown corpus and offers three retrieval tools over it, so "answer from the customer's
          documentation" becomes a measurable workload.
        </p>
      </div>
      <Button v-if="auth.canWrite" label="New toolset" icon="pi pi-plus" @click="openCreate" />
    </div>

    <Message v-if="loadError" severity="error" :closable="false">{{ loadError }}</Message>

    <DataTable
      :value="toolsets"
      :loading="loading"
      removable-sort
      data-key="id"
      class="table list-table row-nav"
      @row-click="onRowClick"
    >
      <template #empty>No toolsets yet — add one with "New toolset".</template>
      <Column field="name" header="Name" sortable>
        <template #body="{ data }: { data: Toolset }">
          <div class="name-cell">
            <RouterLink :to="`/toolsets/${data.id}`" class="name-link">{{ data.name }}</RouterLink>
            <Tag :value="toolsetKindLabel(data.kind)" :severity="toolsetKindSeverity(data.kind)" />
            <Tag v-if="data.is_global" value="Global" severity="info" />
          </div>
          <span v-if="data.description" class="description">{{ data.description }}</span>
        </template>
      </Column>
      <Column header="Tools" class="fit-column">
        <template #body="{ data }: { data: Toolset }">
          {{ data.enabled_tool_count }}/{{ data.tool_count }} enabled
          <span v-if="data.kind === 'documents'" class="description">
            {{ data.document_count }} document{{ data.document_count === 1 ? '' : 's' }}
          </span>
        </template>
      </Column>
      <Column field="updated_at" header="Updated" sortable class="fit-column">
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
        <template v-if="auth.canAdminister">
          <div class="field">
            <label>Kind</label>
            <SelectButton v-model="form.kind" :options="TOOLSET_KIND_OPTIONS" option-label="label" option-value="value" />
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
          <p v-else-if="form.kind === 'documents'" class="hint">
            Creating this adds its three retrieval tools — <span class="mono">list_documents</span>,
            <span class="mono">search_documents</span> and <span class="mono">read_document</span> —
            straight away. Upload or write the markdown on the toolset's own page afterwards.
          </p>
          <label v-if="auth.isBaseWorkspace" class="checkbox-option" for="toolset-is-global">
            <Checkbox v-model="form.is_global" binary input-id="toolset-is-global" />
            Global — share this toolset with every workspace
          </label>
        </template>
        <!-- Stated rather than chosen, the way the test-case editor states a
             prompt's kind: a member's toolset is always manual. -->
        <p v-else class="hint">
          A manual toolset: you add its tools and their canned responses on the toolset's page.
          MCP and documents toolsets are created by an administrator.
        </p>
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
.description {
  display: block;
  font-size: 0.8125rem;
  color: var(--p-text-muted-color);
  margin-top: 0.125rem;
}
</style>
