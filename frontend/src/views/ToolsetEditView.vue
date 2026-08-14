<script setup lang="ts">
// Toolset detail/edit. `canAdminister` gates the toolset itself (its
// `mcp_url` and headers are credentials); `canWrite` gates the tools inside
// it, which are content — the same split as the toolsets list.
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import Button from 'primevue/button'
import Checkbox from 'primevue/checkbox'
import Column from 'primevue/column'
import DataTable from 'primevue/datatable'
import Dialog from 'primevue/dialog'
import InputText from 'primevue/inputtext'
import Message from 'primevue/message'
import SelectButton from 'primevue/selectbutton'
import Tag from 'primevue/tag'
import Textarea from 'primevue/textarea'
import { useConfirm } from 'primevue/useconfirm'
import { useToast } from 'primevue/usetoast'
import {
  toolsetsApi,
  type Tool,
  type ToolsetDetail,
  type ToolsetInput,
  type ToolsetKind,
} from '../api/toolsets'
import { ApiError } from '../api/client'
import { formatDateTime } from '../lib/format'
import { useAuthStore } from '../stores/auth'

const props = defineProps<{ id: string }>()
const toolsetId = computed(() => Number(props.id))

const auth = useAuthStore()
const router = useRouter()
const confirm = useConfirm()
const toast = useToast()

const toolset = ref<ToolsetDetail | null>(null)
const tools = ref<Tool[]>([])
const loading = ref(true)
const loadError = ref<string | null>(null)

const kindOptions: { label: string; value: ToolsetKind }[] = [
  { label: 'Manual', value: 'manual' },
  { label: 'MCP', value: 'mcp' },
]

interface ToolsetFormState {
  name: string
  description: string
  kind: ToolsetKind
  mcp_url: string
  /** Always starts blank: the stored headers are never sent to the client, and
   * a blank field means "leave them alone", not "clear them". */
  mcp_headers: string
}

const form = ref<ToolsetFormState>({
  name: '',
  description: '',
  kind: 'manual',
  mcp_url: '',
  mcp_headers: '',
})

/** Clearing stored headers has to be deliberate — see `buildInput`. */
const clearMcpHeaders = ref(false)

function applyToolset(row: ToolsetDetail) {
  toolset.value = row
  tools.value = row.tools
  form.value = {
    name: row.name,
    description: row.description ?? '',
    kind: row.kind,
    mcp_url: row.mcp_url ?? '',
    mcp_headers: '',
  }
  clearMcpHeaders.value = false
}

async function load() {
  loading.value = true
  loadError.value = null
  try {
    // The tools come embedded in the toolset's own detail response — there is
    // no `/toolsets/{id}/tools` route to ask separately.
    applyToolset(await toolsetsApi.get(toolsetId.value))
  } catch (err) {
    loadError.value = err instanceof ApiError ? err.message : 'Failed to load the toolset.'
  } finally {
    loading.value = false
  }
}

onMounted(load)
watch(toolsetId, load)

/** Re-reads the toolset after a tool action, since the tool list only exists
 * inside the detail response. Deliberately not `applyToolset`: a tool action
 * must not throw away unsaved edits in the Details form.
 */
async function refreshTools() {
  const detail = await toolsetsApi.get(toolsetId.value)
  toolset.value = detail
  tools.value = detail.tools
}

// --- save toolset --------------------------------------------------------

const saving = ref(false)
const saveError = ref<string | null>(null)

/** `mcp_headers` is the one field this form does not replace wholesale: the
 * route treats it patch-like, so it is present in the body only when the admin
 * typed new headers or explicitly asked for the stored ones to be removed.
 * Anything else — including a save that never touched the field — leaves them
 * intact. (Switching to `manual` clears them server-side either way.)
 */
function buildInput(): ToolsetInput {
  const input: ToolsetInput = {
    name: form.value.name,
    description: form.value.description || null,
    kind: form.value.kind,
    mcp_url: form.value.kind === 'mcp' ? form.value.mcp_url || null : null,
  }
  if (clearMcpHeaders.value) {
    input.mcp_headers = null
  } else if (form.value.mcp_headers.length > 0) {
    input.mcp_headers = form.value.mcp_headers
  }
  return input
}

async function save() {
  saveError.value = null
  saving.value = true
  try {
    const updated = await toolsetsApi.update(toolsetId.value, buildInput())
    applyToolset(updated)
    toast.add({ severity: 'success', summary: 'Toolset saved', life: 3000 })
  } catch (err) {
    saveError.value = err instanceof ApiError ? err.message : 'Failed to save the toolset.'
  } finally {
    saving.value = false
  }
}

// --- discover (mcp only) --------------------------------------------------

const discovering = ref(false)

async function discoverTools() {
  discovering.value = true
  try {
    const result = await toolsetsApi.discover(toolsetId.value)
    if (result.ok) {
      toast.add({
        severity: 'success',
        summary: `Found ${result.discovered} tool${result.discovered === 1 ? '' : 's'}`,
        detail: result.retired > 0 ? `Disabled ${result.retired} that vanished` : undefined,
        life: 4000,
      })
      await refreshTools()
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

// --- delete toolset --------------------------------------------------

const deleting = ref(false)

function confirmDeleteToolset() {
  if (!toolset.value) return
  confirm.require({
    header: 'Delete toolset',
    message: `Delete toolset "${toolset.value.name}" and its ${tools.value.length} tool(s)? Past runs keep their frozen copies.`,
    acceptProps: { label: 'Delete', severity: 'danger' },
    rejectProps: { label: 'Cancel', text: true },
    accept: () => void removeToolset(),
  })
}

async function removeToolset() {
  deleting.value = true
  try {
    await toolsetsApi.remove(toolsetId.value)
    await router.push('/toolsets')
  } catch (err) {
    toast.add({
      severity: 'error',
      summary: 'Failed to delete toolset',
      detail: err instanceof ApiError ? err.message : undefined,
      life: 5000,
    })
  } finally {
    deleting.value = false
  }
}

// --- tool create/edit dialog --------------------------------------------

interface ToolFormState {
  name: string
  description: string
  parameters_json: string
  mock_response: string
}

function emptyToolForm(): ToolFormState {
  return { name: '', description: '', parameters_json: '', mock_response: '' }
}

const toolDialogOpen = ref(false)
const editingTool = ref<Tool | null>(null)
const toolForm = ref<ToolFormState>(emptyToolForm())
const toolFormError = ref<string | null>(null)
const savingTool = ref(false)

const toolNameEditable = computed(() => editingTool.value === null || editingTool.value.source === 'manual')

function openNewTool() {
  editingTool.value = null
  toolForm.value = emptyToolForm()
  toolFormError.value = null
  toolDialogOpen.value = true
}

function openEditTool(tool: Tool) {
  editingTool.value = tool
  toolForm.value = {
    name: tool.name,
    description: tool.description ?? '',
    parameters_json: tool.parameters_json,
    mock_response: tool.mock_response ?? '',
  }
  toolFormError.value = null
  toolDialogOpen.value = true
}

async function submitTool() {
  toolFormError.value = null
  savingTool.value = true
  try {
    const input = {
      name: toolForm.value.name,
      description: toolForm.value.description || null,
      parameters_json: toolForm.value.parameters_json || '{}',
      mock_response: toolForm.value.mock_response || null,
    }
    if (editingTool.value) {
      await toolsetsApi.updateTool(toolsetId.value, editingTool.value.id, input)
      toast.add({ severity: 'success', summary: 'Tool saved', life: 3000 })
    } else {
      await toolsetsApi.createTool(toolsetId.value, input)
      toast.add({ severity: 'success', summary: 'Tool added', life: 3000 })
    }
    toolDialogOpen.value = false
    await refreshTools()
  } catch (err) {
    toolFormError.value = err instanceof ApiError ? err.message : 'Failed to save the tool.'
  } finally {
    savingTool.value = false
  }
}

// --- enable / delete tool --------------------------------------------

const busyToolId = ref<number | null>(null)

async function toggleToolEnabled(tool: Tool) {
  busyToolId.value = tool.id
  try {
    await toolsetsApi.setToolEnabled(toolsetId.value, tool.id, !tool.enabled)
    await refreshTools()
  } catch (err) {
    toast.add({
      severity: 'error',
      summary: 'Failed to change the tool',
      detail: err instanceof ApiError ? err.message : undefined,
      life: 5000,
    })
  } finally {
    busyToolId.value = null
  }
}

function confirmDeleteTool(tool: Tool) {
  confirm.require({
    header: 'Delete tool',
    message: `Delete tool "${tool.name}"?`,
    acceptProps: { label: 'Delete', severity: 'danger' },
    rejectProps: { label: 'Cancel', text: true },
    accept: () => void removeTool(tool),
  })
}

async function removeTool(tool: Tool) {
  busyToolId.value = tool.id
  try {
    await toolsetsApi.removeTool(toolsetId.value, tool.id)
    await refreshTools()
  } catch (err) {
    toast.add({
      severity: 'error',
      summary: 'Failed to delete the tool',
      detail: err instanceof ApiError ? err.message : undefined,
      life: 5000,
    })
  } finally {
    busyToolId.value = null
  }
}
</script>

<template>
  <div class="page">
    <Message v-if="loadError" severity="error" :closable="false">{{ loadError }}</Message>

    <template v-if="!loading && toolset">
      <div class="page-header">
        <div class="page-heading">
          <h1>
            {{ toolset.name }}
            <Tag :value="toolset.kind === 'mcp' ? 'MCP' : 'manual'" :severity="toolset.kind === 'mcp' ? 'info' : 'secondary'" />
          </h1>
          <p v-if="toolset.description" class="subtitle">{{ toolset.description }}</p>
        </div>
        <Button
          v-if="auth.canAdminister && toolset.kind === 'mcp'"
          label="Discover tools"
          severity="secondary"
          outlined
          :loading="discovering"
          @click="discoverTools"
        />
      </div>

      <section v-if="auth.canAdminister" class="panel">
        <h2>Details</h2>
        <form class="dialog-form" @submit.prevent="save">
          <div class="field">
            <label for="toolset-name">Name *</label>
            <InputText id="toolset-name" v-model="form.name" required />
          </div>
          <div class="field">
            <label for="toolset-description">Description</label>
            <InputText id="toolset-description" v-model="form.description" />
          </div>
          <div class="field">
            <label>Kind</label>
            <SelectButton v-model="form.kind" :options="kindOptions" option-label="label" option-value="value" />
          </div>
          <template v-if="form.kind === 'mcp'">
            <div class="field">
              <label for="toolset-mcp-url">MCP URL</label>
              <InputText id="toolset-mcp-url" v-model="form.mcp_url" />
            </div>
            <div class="field">
              <label for="toolset-mcp-headers">Headers (JSON)</label>
              <Textarea
                id="toolset-mcp-headers"
                v-model="form.mcp_headers"
                rows="3"
                auto-resize
                :disabled="clearMcpHeaders"
                :placeholder="
                  toolset.has_mcp_headers
                    ? 'leave blank to keep the stored headers'
                    : '{&quot;Authorization&quot;: &quot;Bearer …&quot;}'
                "
              />
              <p v-if="toolset.has_mcp_headers" class="hint">
                Authentication headers are stored — leave this blank to keep them, or paste a new
                JSON object to replace them.
              </p>
              <label
                v-if="toolset.has_mcp_headers"
                class="checkbox-option"
                for="toolset-clear-mcp-headers"
              >
                <Checkbox
                  v-model="clearMcpHeaders"
                  binary
                  input-id="toolset-clear-mcp-headers"
                />
                Remove the stored headers on save
              </label>
            </div>
          </template>

          <p class="meta">Updated {{ formatDateTime(toolset.updated_at) }}</p>

          <Message v-if="saveError" severity="error" :closable="false">{{ saveError }}</Message>
          <div class="dialog-actions start">
            <Button type="submit" label="Save changes" :loading="saving" />
          </div>
        </form>

        <div class="danger-zone">
          <Button
            label="Delete toolset"
            severity="danger"
            outlined
            :loading="deleting"
            @click="confirmDeleteToolset"
          />
        </div>
      </section>

      <section class="panel">
        <div class="panel-header">
          <h2>Tools</h2>
          <Button v-if="auth.canWrite" label="Add tool" size="small" outlined @click="openNewTool" />
        </div>

        <DataTable :value="tools" data-key="id" class="table">
          <template #empty>
            {{
              toolset.kind === 'mcp'
                ? 'No tools yet — run Discover to import them from the server.'
                : 'No tools yet — add one with "Add tool".'
            }}
          </template>
          <Column field="name" header="Name">
            <template #body="{ data }: { data: Tool }">
              <div class="name-cell">
                <span class="mono">{{ data.name }}</span>
                <Tag v-if="!data.enabled" value="disabled" severity="secondary" />
                <Tag v-if="data.source === 'mcp'" value="discovered" severity="info" />
              </div>
              <span v-if="data.description" class="description">{{ data.description }}</span>
            </template>
          </Column>
          <Column header="" class="actions-column">
            <template #body="{ data }: { data: Tool }">
              <div class="row-actions">
                <Button
                  v-if="auth.canWrite"
                  :label="data.enabled ? 'Disable' : 'Enable'"
                  text
                  size="small"
                  :loading="busyToolId === data.id"
                  @click="toggleToolEnabled(data)"
                />
                <Button
                  v-if="auth.canWrite"
                  label="Edit"
                  text
                  size="small"
                  @click="openEditTool(data)"
                />
                <Button
                  v-if="auth.canWrite"
                  label="Delete"
                  text
                  size="small"
                  severity="danger"
                  :loading="busyToolId === data.id"
                  @click="confirmDeleteTool(data)"
                />
              </div>
            </template>
          </Column>
        </DataTable>
      </section>
    </template>

    <Dialog
      v-model:visible="toolDialogOpen"
      modal
      :header="editingTool ? `Edit ${editingTool.name}` : 'New tool'"
      class="form-dialog"
    >
      <form class="dialog-form" @submit.prevent="submitTool">
        <div class="field">
          <label for="tool-name">Name *</label>
          <InputText id="tool-name" v-model="toolForm.name" required :disabled="!toolNameEditable" />
        </div>
        <div class="field">
          <label for="tool-description">Description</label>
          <InputText id="tool-description" v-model="toolForm.description" />
        </div>
        <div class="field">
          <label for="tool-parameters">Parameters (JSON Schema)</label>
          <Textarea id="tool-parameters" v-model="toolForm.parameters_json" rows="4" auto-resize class="mono-input" />
        </div>
        <div class="field">
          <label for="tool-mock-response">Mock response</label>
          <Textarea id="tool-mock-response" v-model="toolForm.mock_response" rows="4" auto-resize class="mono-input" />
        </div>
        <Message v-if="toolFormError" severity="error" :closable="false">{{ toolFormError }}</Message>
        <div class="dialog-actions">
          <Button type="button" label="Cancel" text @click="toolDialogOpen = false" />
          <Button type="submit" :label="editingTool ? 'Save tool' : 'Add tool'" :loading="savingTool" />
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
  max-width: 56rem;
}

.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 1rem;
}

.page-heading h1 {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 1.5rem;
  font-weight: 600;
  margin: 0 0 0.25rem;
}

.subtitle {
  max-width: 44rem;
  color: var(--p-text-muted-color);
  font-size: 0.875rem;
  margin: 0;
}

.panel {
  display: flex;
  flex-direction: column;
  gap: 1rem;
  padding: 1.5rem;
  border: 1px solid var(--p-content-border-color);
  border-radius: var(--p-content-border-radius);
}

.panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
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

.mono-input :deep(textarea) {
  font-family: var(--p-font-family-mono, ui-monospace, monospace);
  font-size: 0.8125rem;
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
  justify-content: flex-end;
  gap: 0.5rem;
}

.dialog-actions.start {
  justify-content: flex-start;
}

.danger-zone {
  border-top: 1px solid var(--p-content-border-color);
  padding-top: 1rem;
}

.name-cell {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.mono {
  font-family: var(--p-font-family-mono, ui-monospace, monospace);
  font-size: 0.875rem;
}

.description {
  display: block;
  font-size: 0.8125rem;
  color: var(--p-text-muted-color);
  margin-top: 0.125rem;
}

.actions-column {
  width: 1%;
  white-space: nowrap;
}

.row-actions {
  display: flex;
  gap: 0.25rem;
  justify-content: flex-end;
}
</style>
