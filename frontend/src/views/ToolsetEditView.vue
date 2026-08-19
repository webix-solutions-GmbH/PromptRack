<script setup lang="ts">
// Toolset detail/edit. `canAdminister` gates the toolset itself (its
// `mcp_url` and headers are credentials); `canWrite` gates the tools inside
// it, which are content — the same split as the toolsets list. A documents
// toolset's corpus sits on the `canWrite` side of that line for the same
// reason: markdown a customer wrote is content, and the toolset holds no
// credential at all.
//
// The three tool rows of a documents toolset are synthesized, not authored, so
// this page shows them like any other tool but offers no way to add a fourth or
// re-word one of them (both are 400s server-side). Disabling one *is* offered:
// turning `search_documents` off to see whether a model can navigate a corpus by
// list-and-read alone is one of the measurements the feature exists for.
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
  TOOLSET_KIND_OPTIONS,
  toolsetKindLabel,
  toolsetKindSeverity,
  type Document,
  type DocumentUploadResult,
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
/** Metadata only — the detail response carries no markdown, and one document's
 * `content` is fetched on demand when its editor opens. */
const documents = ref<Document[]>([])
const loading = ref(true)
const loadError = ref<string | null>(null)

/** The corpus is content, so a member may change it — but a global toolset
 * borrowed from Base is still read-only here, exactly as its tools are. Reading
 * a borrowed document is allowed and useful: the corpus is what a retrieval
 * measurement is measured against. */
const documentsWritable = computed(() => auth.canWrite && toolset.value?.editable === true)

interface ToolsetFormState {
  name: string
  description: string
  kind: ToolsetKind
  mcp_url: string
  /** Always starts blank: the stored headers are never sent to the client, and
   * a blank field means "leave them alone", not "clear them". */
  mcp_headers: string
  is_global: boolean
}

const form = ref<ToolsetFormState>({
  name: '',
  description: '',
  kind: 'manual',
  mcp_url: '',
  mcp_headers: '',
  is_global: false,
})

/** Clearing stored headers has to be deliberate — see `buildInput`. */
const clearMcpHeaders = ref(false)

function applyToolset(row: ToolsetDetail) {
  toolset.value = row
  tools.value = row.tools
  documents.value = row.documents
  form.value = {
    name: row.name,
    description: row.description ?? '',
    kind: row.kind,
    mcp_url: row.mcp_url ?? '',
    mcp_headers: '',
    is_global: row.is_global,
  }
  clearMcpHeaders.value = false
}

async function load() {
  loading.value = true
  loadError.value = null
  try {
    // The tools come embedded in the toolset's own detail response — there is
    // no `/toolsets/{id}/tools` route to ask separately. The documents come
    // embedded too, but without their markdown, and they do have their own list
    // route, which is what a corpus action refreshes with.
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
  documents.value = detail.documents
}

/** The corpus alone, after a document action — the one child with its own list
 * route, so a five-file upload does not re-read the whole toolset. */
async function refreshDocuments() {
  documents.value = await toolsetsApi.listDocuments(toolsetId.value)
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
    const updated = await toolsetsApi.update(toolsetId.value, buildInput())
    applyToolset(updated)
    toast.add({ severity: 'success', summary: 'Toolset saved', life: 5000 })
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
        life: 5000,
      })
      await refreshTools()
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

// --- sync document tools (documents only) --------------------------------

const syncingTools = ref(false)

/** The documents counterpart of Discover: puts the three synthesized tool rows
 * back if one was disabled-and-deleted or predates a change to their schemas.
 * It reaches no server, so unlike Discover it has no expected failure to report
 * in the body — anything thrown here is a real request error.
 */
async function syncDocumentTools() {
  syncingTools.value = true
  try {
    const result = await toolsetsApi.syncDocumentTools(toolsetId.value)
    toast.add({
      severity: 'success',
      summary: `${result.tools.length} document tool${result.tools.length === 1 ? '' : 's'} in place`,
      detail: result.created > 0 ? `Restored ${result.created} that was missing` : undefined,
      life: 5000,
    })
    await refreshTools()
  } catch (err) {
    toast.add({
      severity: 'error',
      summary: 'Failed to sync the document tools',
      detail: err instanceof ApiError ? err.message : 'Request failed unexpectedly.',
      life: 5000,
    })
  } finally {
    syncingTools.value = false
  }
}

// --- delete toolset --------------------------------------------------

const deleting = ref(false)

function confirmDeleteToolset() {
  if (!toolset.value) return
  // A run freezes the tool *definitions* it was offered, so deleting a toolset
  // never changes how a past run reads. A corpus is not frozen — a document tool
  // reads live — so for a documents toolset the warning says so rather than
  // implying the markdown survives somewhere.
  const held =
    toolset.value.kind === 'documents'
      ? `its ${tools.value.length} tool(s) and ${documents.value.length} document(s)? Past runs keep the frozen tool definitions; the markdown is not frozen anywhere and is gone for good.`
      : `its ${tools.value.length} tool(s)? Past runs keep their own frozen copies.`
  confirm.require({
    header: 'Delete toolset',
    message: `Delete toolset "${toolset.value.name}" and ${held}`,
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
      toast.add({ severity: 'success', summary: 'Tool saved', life: 5000 })
    } else {
      await toolsetsApi.createTool(toolsetId.value, input)
      toast.add({ severity: 'success', summary: 'Tool added', life: 5000 })
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
    message: `Delete tool "${tool.name}"? This cannot be undone.`,
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

// --- document create/edit dialog -----------------------------------------

interface DocumentFormState {
  path: string
  title: string
  content: string
}

function emptyDocumentForm(): DocumentFormState {
  return { path: '', title: '', content: '' }
}

const documentDialogOpen = ref(false)
const editingDocument = ref<Document | null>(null)
const documentForm = ref<DocumentFormState>(emptyDocumentForm())
const documentFormError = ref<string | null>(null)
const loadingDocument = ref(false)
const savingDocument = ref(false)

function openNewDocument() {
  editingDocument.value = null
  documentForm.value = emptyDocumentForm()
  documentFormError.value = null
  documentDialogOpen.value = true
}

/** The list carries no markdown, so opening a document is a fetch. The dialog
 * opens first and fills in: a corpus document can be tens of kilobytes, and
 * waiting on the request before showing anything reads as a dead button.
 */
async function openDocument(document: Document) {
  editingDocument.value = document
  documentForm.value = { path: document.path, title: document.title, content: '' }
  documentFormError.value = null
  documentDialogOpen.value = true
  loadingDocument.value = true
  try {
    const detail = await toolsetsApi.getDocument(toolsetId.value, document.id)
    // A second click while the first read was in flight wins — don't overwrite
    // whatever is open now with a stale body.
    if (editingDocument.value?.id === detail.id) {
      documentForm.value = { path: detail.path, title: detail.title, content: detail.content }
    }
  } catch (err) {
    documentFormError.value =
      err instanceof ApiError ? err.message : 'Failed to load the document.'
  } finally {
    loadingDocument.value = false
  }
}

async function submitDocument() {
  documentFormError.value = null
  savingDocument.value = true
  try {
    // `title` blank means "derive it" server-side — from the markdown's first
    // heading, then from the path's file stem — so it is sent as null rather
    // than as an empty string.
    const input = {
      path: documentForm.value.path,
      title: documentForm.value.title || null,
      content: documentForm.value.content,
    }
    if (editingDocument.value) {
      await toolsetsApi.updateDocument(toolsetId.value, editingDocument.value.id, input)
      toast.add({ severity: 'success', summary: 'Document saved', life: 5000 })
    } else {
      await toolsetsApi.createDocument(toolsetId.value, input)
      toast.add({ severity: 'success', summary: 'Document added', life: 5000 })
    }
    documentDialogOpen.value = false
    await refreshDocuments()
  } catch (err) {
    documentFormError.value = err instanceof ApiError ? err.message : 'Failed to save the document.'
  } finally {
    savingDocument.value = false
  }
}

// --- delete document -----------------------------------------------------

const busyDocumentId = ref<number | null>(null)

function confirmDeleteDocument(document: Document) {
  confirm.require({
    header: 'Delete document',
    message: `Delete "${document.path}" from this corpus? A model will stop finding it on the next run.`,
    acceptProps: { label: 'Delete', severity: 'danger' },
    rejectProps: { label: 'Cancel', text: true },
    accept: () => void removeDocument(document),
  })
}

async function removeDocument(document: Document) {
  busyDocumentId.value = document.id
  try {
    await toolsetsApi.removeDocument(toolsetId.value, document.id)
    await refreshDocuments()
  } catch (err) {
    toast.add({
      severity: 'error',
      summary: 'Failed to delete the document',
      detail: err instanceof ApiError ? err.message : undefined,
      life: 5000,
    })
  } finally {
    busyDocumentId.value = null
  }
}

// --- markdown upload -----------------------------------------------------

const fileInput = ref<HTMLInputElement | null>(null)
const uploading = ref(false)
/** Per-file rejections survive on the page rather than in a toast: "re-save as
 * UTF-8" is a task, and the response reports it per file precisely so the other
 * seven files of the same drop are not thrown away with it. */
const uploadFailures = ref<DocumentUploadResult[]>([])

function pickFiles() {
  uploadFailures.value = []
  fileInput.value?.click()
}

async function onFilesPicked(event: Event) {
  const input = event.target as HTMLInputElement
  const picked = Array.from(input.files ?? [])
  // Clearing the input is what lets the same file be picked again after a fix —
  // otherwise the `change` event never fires a second time.
  input.value = ''
  if (picked.length === 0) return

  uploading.value = true
  try {
    // `webkitRelativePath` is what a folder picker fills in, and it is the whole
    // reason `guides/refunds.md` can stay a path instead of collapsing to its
    // basename: the filename sent with the part *is* the corpus key.
    const result = await toolsetsApi.uploadDocuments(
      toolsetId.value,
      picked.map((file) => ({ file, path: file.webkitRelativePath || file.name })),
    )
    uploadFailures.value = result.results.filter((row) => !row.ok)
    documents.value = result.documents
    const accepted = result.created + result.replaced
    toast.add({
      severity: result.failed > 0 ? 'warn' : 'success',
      summary: `${accepted} document${accepted === 1 ? '' : 's'} uploaded`,
      detail: [
        result.replaced > 0 ? `${result.replaced} replaced an existing path` : null,
        result.failed > 0 ? `${result.failed} rejected` : null,
      ]
        .filter(Boolean)
        .join(' · ') || undefined,
      life: 5000,
    })
  } catch (err) {
    toast.add({
      severity: 'error',
      summary: 'Upload failed',
      detail: err instanceof ApiError ? err.message : 'Request failed unexpectedly.',
      life: 5000,
    })
  } finally {
    uploading.value = false
  }
}

/** A document's size in the same unit `read_document` windows in, so the table
 * and the model measure a document the same way. */
function formatChars(chars: number): string {
  return `${chars.toLocaleString()} chars`
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
            <Tag :value="toolsetKindLabel(toolset.kind)" :severity="toolsetKindSeverity(toolset.kind)" />
            <Tag v-if="toolset.is_global" value="Global" severity="info" />
          </h1>
          <p v-if="toolset.description" class="subtitle">{{ toolset.description }}</p>
          <Message v-if="!toolset.editable" severity="info" :closable="false">
            Shared from the Base workspace. Switch to Base to change it.
          </Message>
        </div>
        <Button
          v-if="auth.canAdminister && toolset.kind === 'mcp' && toolset.editable"
          label="Discover tools"
          severity="secondary"
          outlined
          :loading="discovering"
          @click="discoverTools"
        />
        <Button
          v-else-if="documentsWritable && toolset.kind === 'documents'"
          label="Sync document tools"
          severity="secondary"
          outlined
          :loading="syncingTools"
          @click="syncDocumentTools"
        />
      </div>

      <section v-if="auth.canAdminister && toolset.editable" class="panel">
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
            <SelectButton v-model="form.kind" :options="TOOLSET_KIND_OPTIONS" option-label="label" option-value="value" />
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
          <p v-else-if="form.kind === 'documents'" class="hint">
            A documents toolset has no server and no canned responses: it offers
            <span class="mono">list_documents</span>, <span class="mono">search_documents</span> and
            <span class="mono">read_document</span> over the corpus below, and every call is answered
            from it live. Switching a toolset to this kind adds the three tools and clears any stored
            MCP URL and headers.
          </p>
          <label v-if="auth.isBaseWorkspace" class="checkbox-option" for="toolset-is-global">
            <Checkbox v-model="form.is_global" binary input-id="toolset-is-global" />
            Global — share this toolset with every workspace
          </label>

          <p class="hint">Updated {{ formatDateTime(toolset.updated_at) }}</p>

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
          <!-- Hidden rather than disabled on a documents toolset: its tools are
               synthesized from one constant, and a fourth hand-authored tool
               beside them is refused server-side. -->
          <Button
            v-if="auth.canWrite && toolset.kind !== 'documents'"
            label="New tool"
            icon="pi pi-plus"
            size="small"
            outlined
            @click="openNewTool"
          />
        </div>

        <DataTable :value="tools" :loading="loading" removable-sort data-key="id" class="table list-table">
          <template #empty>
            {{
              toolset.kind === 'mcp'
                ? 'No tools yet — run Discover to import them from the server.'
                : toolset.kind === 'documents'
                  ? 'No tools — run "Sync document tools" to put the three retrieval tools back.'
                  : 'No tools yet — add one with "New tool".'
            }}
          </template>
          <Column field="name" header="Name" sortable>
            <template #body="{ data }: { data: Tool }">
              <div class="name-cell">
                <span class="mono">{{ data.name }}</span>
                <Tag v-if="!data.enabled" value="disabled" severity="secondary" />
                <Tag v-if="data.source === 'mcp'" value="discovered" severity="info" />
                <Tag v-if="data.source === 'documents'" value="synthesized" severity="success" />
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
                  v-if="auth.canWrite && data.source !== 'documents'"
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

      <section v-if="toolset.kind === 'documents'" class="panel">
        <div class="panel-header">
          <h2>Corpus</h2>
          <div v-if="documentsWritable" class="row-actions">
            <Button
              label="Upload markdown"
              icon="pi pi-upload"
              size="small"
              outlined
              :loading="uploading"
              @click="pickFiles"
            />
            <Button
              label="New document"
              icon="pi pi-plus"
              size="small"
              outlined
              @click="openNewDocument"
            />
          </div>
        </div>

        <p class="hint">
          What <span class="mono">search_documents</span> and <span class="mono">read_document</span>
          answer from. A file's name is its path, so upload a folder's worth to keep
          <span class="mono">guides/refunds.md</span> as one key. Search is full-text over title and
          body in Postgres' language-neutral configuration, so a German corpus retrieves as well as
          an English one.
        </p>

        <!-- The picker is driven by the button above so the panel keeps the app's
             own control vocabulary; `accept` filters the dialog, and the route
             re-checks the extension per file regardless. -->
        <input
          ref="fileInput"
          class="file-input"
          type="file"
          multiple
          accept=".md,.markdown,text/markdown"
          @change="onFilesPicked"
        />

        <Message
          v-if="uploadFailures.length > 0"
          severity="warn"
          :closable="true"
          @close="uploadFailures = []"
        >
          <p>These files were not added:</p>
          <ul class="upload-failures">
            <li v-for="failure in uploadFailures" :key="failure.filename">
              <span class="mono">{{ failure.filename }}</span> — {{ failure.error }}
            </li>
          </ul>
        </Message>

        <DataTable
          :value="documents"
          :loading="loading"
          removable-sort
          data-key="id"
          class="table list-table"
        >
          <template #empty>
            No documents yet — upload markdown, or write one with "New document". The three tools
            already answer; they just answer that this corpus is empty.
          </template>
          <Column field="path" header="Path" sortable>
            <template #body="{ data }: { data: Document }">
              <div class="name-cell">
                <span class="mono">{{ data.path }}</span>
              </div>
              <span class="description">{{ data.title }}</span>
            </template>
          </Column>
          <Column field="chars" header="Size" sortable class="fit-column">
            <template #body="{ data }: { data: Document }">{{ formatChars(data.chars) }}</template>
          </Column>
          <Column field="updated_at" header="Updated" sortable class="fit-column">
            <template #body="{ data }: { data: Document }">
              {{ formatDateTime(data.updated_at) }}
            </template>
          </Column>
          <Column header="" class="actions-column">
            <template #body="{ data }: { data: Document }">
              <div class="row-actions">
                <Button
                  :label="documentsWritable ? 'Edit' : 'View'"
                  text
                  size="small"
                  @click="openDocument(data)"
                />
                <Button
                  v-if="documentsWritable"
                  label="Delete"
                  text
                  size="small"
                  severity="danger"
                  :loading="busyDocumentId === data.id"
                  @click="confirmDeleteDocument(data)"
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
          <Button type="submit" :label="editingTool ? 'Save tool' : 'Create tool'" :loading="savingTool" />
        </div>
      </form>
    </Dialog>

    <Dialog
      v-model:visible="documentDialogOpen"
      modal
      :header="editingDocument ? editingDocument.path : 'New document'"
      class="form-dialog"
    >
      <form class="dialog-form" @submit.prevent="submitDocument">
        <div class="field">
          <label for="document-path">Path *</label>
          <InputText
            id="document-path"
            v-model="documentForm.path"
            required
            :readonly="!documentsWritable"
            placeholder="guides/refunds.md"
            class="mono-field"
          />
          <p class="hint">The key <span class="mono">read_document</span> is called with.</p>
        </div>
        <div class="field">
          <label for="document-title">Title</label>
          <InputText
            id="document-title"
            v-model="documentForm.title"
            :readonly="!documentsWritable"
            placeholder="taken from the first heading, or the file name"
          />
        </div>
        <div class="field">
          <label for="document-content">Markdown *</label>
          <Textarea
            id="document-content"
            v-model="documentForm.content"
            rows="18"
            :readonly="!documentsWritable"
            :placeholder="loadingDocument ? 'loading…' : '# Refunds\n\n…'"
            class="mono-input"
          />
        </div>
        <Message v-if="documentFormError" severity="error" :closable="false">
          {{ documentFormError }}
        </Message>
        <div class="dialog-actions">
          <Button
            type="button"
            :label="documentsWritable ? 'Cancel' : 'Close'"
            text
            @click="documentDialogOpen = false"
          />
          <Button
            v-if="documentsWritable"
            type="submit"
            :label="editingDocument ? 'Save document' : 'Create document'"
            :loading="savingDocument"
            :disabled="loadingDocument"
          />
        </div>
      </form>
    </Dialog>
  </div>
</template>

<style scoped>
.page {
  max-width: 56rem;
}

/* The heading carries a Tag beside the name, so it lays its children out on
 * one line instead of taking the global's plain block flow. */
.page-heading h1 {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.mono-input :deep(textarea) {
  font-family: var(--p-font-family-mono, ui-monospace, monospace);
}

/* A document's path is read character by character (a trailing `.markdown`, a
 * missing folder), so its field is monospaced too. Unlike the textareas above,
 * PrimeVue's InputText *is* the input element, so the class lands on it. */
.mono-field {
  font-family: var(--p-font-family-mono, ui-monospace, monospace);
}

/* The file picker is opened by the panel's own Button, so the input itself is
 * never seen — `display: none` still accepts a programmatic `click()`. */
.file-input {
  display: none;
}

.upload-failures {
  margin: 0.375rem 0 0;
  padding-left: 1.25rem;
}

.dialog-actions.start {
  justify-content: flex-start;
}

.description {
  display: block;
  font-size: 0.8125rem;
  color: var(--p-text-muted-color);
  margin-top: 0.125rem;
}
</style>
