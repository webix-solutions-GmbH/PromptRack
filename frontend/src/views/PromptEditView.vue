<script setup lang="ts">
// Prompt editor: the mutable draft plus its immutable commit history — the
// "git for your customers' prompts" workflow (spec §"Workflow & UI").
//
// Committing is deliberately two steps: "Save draft" persists
// `prompts.content` (what a run always tests, right now); "Commit" freezes
// *that saved content* as the next version. Commit stays disabled while the
// textarea has unsaved edits, so a commit can never silently include text
// nobody chose to save — the same reason git refuses to commit a file you
// never staged.
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import Button from 'primevue/button'
import Dialog from 'primevue/dialog'
import Message from 'primevue/message'
import Select from 'primevue/select'
import SelectButton from 'primevue/selectbutton'
import Tag from 'primevue/tag'
import Textarea from 'primevue/textarea'
import { useConfirm } from 'primevue/useconfirm'
import { useToast } from 'primevue/usetoast'
import {
  promptsApi,
  describeVersionStatus,
  PROMPT_KINDS,
  type DiffRef,
  type Prompt,
  type PromptKind,
  type PromptVersion,
} from '../api/prompts'
import { ApiError } from '../api/client'
import { formatDateTime } from '../lib/format'
import { useAuthStore } from '../stores/auth'
import CommitDialog from '../components/CommitDialog.vue'
import DiffViewer from '../components/DiffViewer.vue'
import VersionHistory from '../components/VersionHistory.vue'

const props = defineProps<{ id: string }>()
const promptId = computed(() => Number(props.id))

const auth = useAuthStore()
const router = useRouter()
const confirm = useConfirm()
const toast = useToast()

const prompt = ref<Prompt | null>(null)
const versions = ref<PromptVersion[]>([])
const loading = ref(true)
const loadError = ref<string | null>(null)

const draftContent = ref('')

async function load() {
  loading.value = true
  loadError.value = null
  try {
    const [promptRow, versionRows] = await Promise.all([
      promptsApi.get(promptId.value),
      promptsApi.listVersions(promptId.value),
    ])
    prompt.value = promptRow
    draftContent.value = promptRow.content
    versions.value = versionRows
  } catch (err) {
    loadError.value = err instanceof ApiError ? err.message : 'Failed to load the prompt.'
  } finally {
    loading.value = false
  }
}

onMounted(load)
watch(promptId, load)

// --- draft: unsaved edits vs. the persisted draft -------------------------

const unsavedChanges = computed(() => prompt.value !== null && draftContent.value !== prompt.value.content)

const savingDraft = ref(false)
const saveError = ref<string | null>(null)

async function saveDraft() {
  saveError.value = null
  savingDraft.value = true
  try {
    const updated = await promptsApi.updateDraft(promptId.value, { content: draftContent.value })
    prompt.value = updated
    draftContent.value = updated.content
    toast.add({ severity: 'success', summary: 'Draft saved', life: 2500 })
  } catch (err) {
    saveError.value = err instanceof ApiError ? err.message : 'Failed to save the draft.'
  } finally {
    savingDraft.value = false
  }
}

// --- kind ---------------------------------------------------------------

// The channel this prompt's text goes out on. Changing it is refused by the
// server (409) while any test case references the prompt, because relocating
// text between the system and the user message for every case that uses it is
// exactly the invisible wire-format change the pivot exists to eliminate — so
// the control is disabled with that reason rather than left to fail on click.
// It rides on the same PATCH as the draft, but nothing is written when the
// refusal fires, so an unsaved draft is never lost by trying.
const kindLocked = computed(() => (prompt.value?.used_by_test_case_count ?? 0) > 0)

const kindHint = computed(
  () => PROMPT_KINDS.find((option) => option.value === prompt.value?.kind)?.hint ?? '',
)

const savingKind = ref(false)
const kindError = ref<string | null>(null)

async function changeKind(kind: PromptKind) {
  if (prompt.value === null || kind === prompt.value.kind) return
  kindError.value = null
  savingKind.value = true
  try {
    prompt.value = await promptsApi.updateDraft(promptId.value, { kind })
    toast.add({ severity: 'success', summary: `Kind set to ${kind}`, life: 2500 })
  } catch (err) {
    kindError.value = err instanceof ApiError ? err.message : 'Failed to change the kind.'
  } finally {
    savingKind.value = false
  }
}

// --- commit -----------------------------------------------------------

// A commit only ever freezes the *saved* draft, so it stays unavailable
// while there is nothing new to freeze (not dirty) or while the textarea
// holds edits nobody has saved yet.
const canCommit = computed(() => (prompt.value?.dirty ?? false) && !unsavedChanges.value)

const commitDialogOpen = ref(false)
const committing = ref(false)
const commitError = ref<string | null>(null)

async function handleCommit(message: string) {
  committing.value = true
  commitError.value = null
  try {
    await promptsApi.commit(promptId.value, message)
    commitDialogOpen.value = false
    await load()
    toast.add({ severity: 'success', summary: 'Committed', life: 3000 })
  } catch (err) {
    commitError.value = err instanceof ApiError ? err.message : 'Failed to commit.'
  } finally {
    committing.value = false
  }
}

// --- deploy / restore ---------------------------------------------------

const busyVersionId = ref<number | null>(null)

async function handleDeploy(version: PromptVersion) {
  busyVersionId.value = version.id
  try {
    prompt.value = await promptsApi.deploy(promptId.value, version.id)
    toast.add({ severity: 'success', summary: `Deployed v${version.version}`, life: 3000 })
  } catch (err) {
    toast.add({
      severity: 'error',
      summary: 'Failed to deploy',
      detail: err instanceof ApiError ? err.message : undefined,
      life: 5000,
    })
  } finally {
    busyVersionId.value = null
  }
}

function confirmRestore(version: PromptVersion) {
  confirm.require({
    header: 'Restore to draft',
    message: unsavedChanges.value
      ? `Copy v${version.version}'s content into the draft? Your unsaved draft edits will be discarded.`
      : `Copy v${version.version}'s content into the draft? Review and commit it to record the rollback in history.`,
    acceptProps: { label: 'Restore', severity: 'danger' },
    rejectProps: { label: 'Cancel', text: true },
    accept: () => void handleRestore(version),
  })
}

async function handleRestore(version: PromptVersion) {
  busyVersionId.value = version.id
  try {
    const updated = await promptsApi.restore(promptId.value, version.id)
    prompt.value = updated
    draftContent.value = updated.content
    toast.add({
      severity: 'success',
      summary: `Draft restored to v${version.version}`,
      detail: 'Commit it to record the rollback in history.',
      life: 5000,
    })
  } catch (err) {
    toast.add({
      severity: 'error',
      summary: 'Failed to restore',
      detail: err instanceof ApiError ? err.message : undefined,
      life: 5000,
    })
  } finally {
    busyVersionId.value = null
  }
}

// --- view a version -----------------------------------------------------

const viewDialogOpen = ref(false)
const viewingVersion = ref<PromptVersion | null>(null)

function openView(version: PromptVersion) {
  viewingVersion.value = version
  viewDialogOpen.value = true
}

// --- diff a version -------------------------------------------------------

const diffDialogOpen = ref(false)
const diffTarget = ref<PromptVersion | null>(null)
const diffAgainst = ref<DiffRef>('draft')
const diffText = ref<string[]>([])
const diffLoading = ref(false)
const diffError = ref<string | null>(null)

const diffOptions = computed<{ label: string; value: DiffRef }[]>(() => {
  const options: { label: string; value: DiffRef }[] = [{ label: 'draft', value: 'draft' }]
  for (const version of versions.value) {
    if (diffTarget.value && version.id === diffTarget.value.id) continue
    options.push({ label: `v${version.version}`, value: version.id })
  }
  return options
})

function openDiff(version: PromptVersion) {
  diffTarget.value = version
  diffAgainst.value = 'draft'
  diffDialogOpen.value = true
}

async function loadDiff() {
  if (!diffTarget.value) return
  diffLoading.value = true
  diffError.value = null
  try {
    const result = await promptsApi.diff(promptId.value, diffAgainst.value, diffTarget.value.id)
    diffText.value = result.diff
  } catch (err) {
    diffError.value = err instanceof ApiError ? err.message : 'Failed to load the diff.'
  } finally {
    diffLoading.value = false
  }
}

watch([diffDialogOpen, diffAgainst], ([open]) => {
  if (open) void loadDiff()
})

// --- delete ----------------------------------------------------------

const deleting = ref(false)

function confirmDeletePrompt() {
  if (!prompt.value) return
  confirm.require({
    header: 'Delete prompt',
    message: `Delete prompt "${prompt.value.name}" and its ${versions.value.length} version(s)? Past runs keep their own frozen copies.`,
    acceptProps: { label: 'Delete', severity: 'danger' },
    rejectProps: { label: 'Cancel', text: true },
    accept: () => void removePrompt(),
  })
}

async function removePrompt() {
  deleting.value = true
  try {
    await promptsApi.remove(promptId.value)
    await router.push('/prompts')
  } catch (err) {
    toast.add({
      severity: 'error',
      summary: 'Failed to delete prompt',
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

    <template v-if="!loading && prompt">
      <div class="page-heading">
        <h1>
          {{ prompt.name }}
          <Tag :value="prompt.kind" :severity="prompt.kind === 'system' ? 'info' : 'secondary'" />
          <Tag v-if="prompt.dirty" value="uncommitted" severity="warn" />
        </h1>
        <p class="status-line">{{ describeVersionStatus(prompt) }}</p>
        <p v-if="prompt.deployed_at" class="meta">
          Deployed {{ formatDateTime(prompt.deployed_at) }}
          <template v-if="prompt.deployed_by_name">by {{ prompt.deployed_by_name }}</template>
        </p>
      </div>

      <section v-if="auth.canWrite" class="panel">
        <div class="panel-header">
          <h2>Kind</h2>
          <SelectButton
            :model-value="prompt.kind"
            :options="PROMPT_KINDS"
            option-label="label"
            option-value="value"
            :allow-empty="false"
            :disabled="kindLocked || savingKind"
            @update:model-value="changeKind"
          />
        </div>
        <p class="hint">{{ kindHint }}</p>
        <p v-if="kindLocked" class="hint">
          Locked: {{ prompt.used_by_test_case_count }} test case{{
            prompt.used_by_test_case_count === 1 ? '' : 's'
          }}
          reference this prompt. Changing the kind would move its text between the system and the
          user message for every one of them — detach it from those test cases first.
        </p>
        <Message v-if="kindError" severity="error" :closable="false">{{ kindError }}</Message>
      </section>

      <section class="panel">
        <div class="panel-header">
          <h2>Draft</h2>
          <div class="draft-actions">
            <Button
              v-if="auth.canWrite"
              label="Save draft"
              severity="secondary"
              outlined
              :disabled="!unsavedChanges"
              :loading="savingDraft"
              @click="saveDraft"
            />
            <Button
              v-if="auth.canWrite"
              label="Commit…"
              :disabled="!canCommit"
              @click="commitDialogOpen = true"
            />
          </div>
        </div>
        <Textarea
          v-model="draftContent"
          rows="12"
          auto-resize
          class="mono-input"
          :disabled="!auth.canWrite"
        />
        <p v-if="auth.canWrite && unsavedChanges" class="hint">
          Save your draft before committing.
        </p>
        <Message v-if="saveError" severity="error" :closable="false">{{ saveError }}</Message>
      </section>

      <section class="panel">
        <h2>History</h2>
        <VersionHistory
          :versions="versions"
          :deployed-version-id="prompt.deployed_version?.id ?? null"
          :can-write="auth.canWrite"
          :busy-version-id="busyVersionId"
          @view="openView"
          @diff="openDiff"
          @deploy="handleDeploy"
          @restore="confirmRestore"
        />
      </section>

      <section v-if="auth.canWrite" class="panel">
        <div class="danger-zone">
          <Button
            label="Delete prompt"
            severity="danger"
            outlined
            :loading="deleting"
            @click="confirmDeletePrompt"
          />
        </div>
      </section>
    </template>

    <CommitDialog
      v-model:visible="commitDialogOpen"
      :saving="committing"
      :error="commitError"
      @commit="handleCommit"
    />

    <Dialog
      v-model:visible="viewDialogOpen"
      modal
      :header="viewingVersion ? `v${viewingVersion.version} — ${viewingVersion.message}` : 'Version'"
      class="view-dialog"
    >
      <Textarea v-if="viewingVersion" :model-value="viewingVersion.content" rows="16" readonly class="mono-input view-textarea" />
    </Dialog>

    <Dialog
      v-model:visible="diffDialogOpen"
      modal
      :header="diffTarget ? `Diff v${diffTarget.version}` : 'Diff'"
      class="diff-dialog"
    >
      <div class="diff-controls">
        <label for="diff-against">Compare against</label>
        <Select
          id="diff-against"
          v-model="diffAgainst"
          :options="diffOptions"
          option-label="label"
          option-value="value"
        />
      </div>
      <div class="diff-dialog-body">
        <p v-if="diffLoading" class="hint">Loading diff…</p>
        <Message v-else-if="diffError" severity="error" :closable="false">{{ diffError }}</Message>
        <DiffViewer v-else :diff="diffText" />
      </div>
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

.page-heading h1 {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 1.5rem;
  font-weight: 600;
  margin: 0 0 0.25rem;
}

.status-line {
  font-size: 0.875rem;
  color: var(--p-text-muted-color);
  margin: 0;
}

.meta {
  font-size: 0.75rem;
  color: var(--p-text-muted-color);
  margin: 0.125rem 0 0;
}

.panel {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
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

.draft-actions {
  display: flex;
  gap: 0.5rem;
}

.mono-input :deep(textarea) {
  font-family: var(--p-font-family-mono, ui-monospace, monospace);
  font-size: 0.8125rem;
}

.hint {
  font-size: 0.75rem;
  color: var(--p-text-muted-color);
  margin: 0;
}

.danger-zone {
  display: flex;
}

.view-textarea {
  width: 100%;
}

.diff-controls {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  margin-bottom: 1rem;
}

.diff-controls label {
  font-size: 0.8125rem;
  font-weight: 500;
  color: var(--p-text-muted-color);
}
</style>

<style>
/* Both dialogs teleport to <body>, out of reach of the scoped block above —
   sizing lives here instead. The global `.diff-dialog` rule in style.css
   covers the outer dialog width; this block covers what that rule doesn't. */
.view-dialog {
  width: min(48rem, 90vw);
}

.diff-dialog-body {
  min-height: 24rem;
  max-height: 70vh;
  overflow-y: auto;
}
</style>
