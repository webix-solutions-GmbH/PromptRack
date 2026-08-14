<script setup lang="ts">
// Prompts list — the versioned assets ("git for your customers' prompts").
// Creating/editing content is member-writable (unlike machines/toolsets,
// which hold credentials and stay admin-only): every writer can start a new
// prompt or edit a draft.
import { computed, onMounted, ref } from 'vue'
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
import {
  promptsApi,
  describeVersionStatus,
  PROMPT_KINDS,
  type Prompt,
  type PromptKind,
} from '../api/prompts'
import { ApiError } from '../api/client'
import { formatDateTime } from '../lib/format'
import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()
const toast = useToast()

const prompts = ref<Prompt[]>([])
const loading = ref(true)
const loadError = ref<string | null>(null)

async function load() {
  loading.value = true
  loadError.value = null
  try {
    prompts.value = await promptsApi.list()
  } catch (err) {
    loadError.value = err instanceof ApiError ? err.message : 'Failed to load prompts.'
  } finally {
    loading.value = false
  }
}

onMounted(load)

// --- kind filter --------------------------------------------------------

// The standard suite alone puts ~20 rows on this page, so "show me only the
// task prompts" is the difference between a list and a haystack. `null` is
// "all kinds" — a filter, not a selection, so it never hides a row silently.
const kindFilter = ref<PromptKind | null>(null)

const kindFilterOptions = [
  { label: 'All', value: null },
  ...PROMPT_KINDS.map((kind) => ({ label: kind.label, value: kind.value })),
]

const visiblePrompts = computed(() =>
  kindFilter.value === null
    ? prompts.value
    : prompts.value.filter((prompt) => prompt.kind === kindFilter.value),
)

function kindLabel(kind: PromptKind): string {
  return PROMPT_KINDS.find((option) => option.value === kind)?.label ?? kind
}

// --- create dialog -----------------------------------------------------

interface PromptFormState {
  name: string
  content: string
  kind: PromptKind
}

function emptyForm(): PromptFormState {
  // `system` is the server's own default and the channel everything authored
  // before the prompt-kinds pivot was sent on.
  return { name: '', content: '', kind: 'system' }
}

const dialogOpen = ref(false)
const form = ref<PromptFormState>(emptyForm())
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
    await promptsApi.create({
      name: form.value.name,
      content: form.value.content,
      kind: form.value.kind,
    })
    toast.add({ severity: 'success', summary: 'Prompt created', life: 3000 })
    dialogOpen.value = false
    await load()
  } catch (err) {
    formError.value = err instanceof ApiError ? err.message : 'Failed to create the prompt.'
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div class="page-heading">
        <h1>Prompts</h1>
        <p class="subtitle">
          The versioned business logic behind a customer's agent — a mutable draft, an explicit
          commit history, and one deployed pointer naming what is live at the customer.
        </p>
      </div>
      <Button v-if="auth.canWrite" label="New prompt" icon="pi pi-plus" @click="openCreate" />
    </div>

    <Message v-if="loadError" severity="error" :closable="false">{{ loadError }}</Message>

    <div class="filter-row">
      <span class="filter-label">Kind</span>
      <SelectButton
        v-model="kindFilter"
        :options="kindFilterOptions"
        option-label="label"
        option-value="value"
        :allow-empty="false"
      />
    </div>

    <DataTable :value="visiblePrompts" :loading="loading" data-key="id" class="table">
      <template #empty>No prompts yet — add one with "New prompt".</template>
      <Column field="name" header="Name">
        <template #body="{ data }: { data: Prompt }">
          <div class="name-cell">
            <RouterLink :to="`/prompts/${data.id}`" class="name-link">{{ data.name }}</RouterLink>
            <Tag v-if="data.dirty" value="uncommitted" severity="warn" />
          </div>
        </template>
      </Column>
      <Column header="Kind">
        <template #body="{ data }: { data: Prompt }">
          <Tag
            :value="kindLabel(data.kind)"
            :severity="data.kind === 'system' ? 'info' : 'secondary'"
          />
        </template>
      </Column>
      <Column header="Used by">
        <template #body="{ data }: { data: Prompt }">
          <!-- Flat and forced: a prompt's cases span groups, so landing on
               the grouped view would scatter the answer across panels the
               reader then has to expand. Zero stays unlinked — an empty
               filtered table is a worse answer than a number that does not
               invite a click. -->
          <RouterLink
            v-if="data.used_by_test_case_count > 0"
            :to="`/test-cases?view=flat&prompt=${data.id}`"
          >
            {{ data.used_by_test_case_count }} test case{{
              data.used_by_test_case_count === 1 ? '' : 's'
            }}
          </RouterLink>
          <span v-else class="unused">
            {{ data.used_by_test_case_count }} test case{{
              data.used_by_test_case_count === 1 ? '' : 's'
            }}
          </span>
        </template>
      </Column>
      <Column header="Version status">
        <template #body="{ data }: { data: Prompt }">{{ describeVersionStatus(data) }}</template>
      </Column>
      <Column header="Updated">
        <template #body="{ data }: { data: Prompt }">{{ formatDateTime(data.updated_at) }}</template>
      </Column>
    </DataTable>

    <Dialog v-model:visible="dialogOpen" modal header="New prompt" class="form-dialog">
      <form class="dialog-form" @submit.prevent="submitForm">
        <div class="field">
          <label for="prompt-name">Name *</label>
          <InputText id="prompt-name" v-model="form.name" required placeholder="Order support agent" autofocus />
        </div>
        <div class="field">
          <span class="label">Kind</span>
          <SelectButton
            v-model="form.kind"
            :options="PROMPT_KINDS"
            option-label="label"
            option-value="value"
            :allow-empty="false"
          />
          <p class="hint">
            {{ PROMPT_KINDS.find((kind) => kind.value === form.kind)?.hint }}
          </p>
        </div>
        <div class="field">
          <label for="prompt-content">Draft content</label>
          <Textarea
            id="prompt-content"
            v-model="form.content"
            rows="6"
            auto-resize
            placeholder="You are a support agent for…"
            class="mono-input"
          />
        </div>
        <Message v-if="formError" severity="error" :closable="false">{{ formError }}</Message>
        <div class="dialog-actions">
          <Button type="button" label="Cancel" text @click="dialogOpen = false" />
          <Button type="submit" label="Create prompt" :loading="saving" />
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

.field label,
.field .label {
  font-size: 0.8125rem;
  font-weight: 500;
  color: var(--p-text-muted-color);
}

.filter-row {
  display: flex;
  align-items: center;
  gap: 0.75rem;
}

.filter-label {
  font-size: 0.8125rem;
  font-weight: 500;
  color: var(--p-text-muted-color);
}

.unused {
  color: var(--p-text-muted-color);
}

.hint {
  font-size: 0.75rem;
  color: var(--p-text-muted-color);
  margin: 0;
}

.mono-input :deep(textarea) {
  font-family: var(--p-font-family-mono, ui-monospace, monospace);
  font-size: 0.8125rem;
}

.dialog-actions {
  display: flex;
  justify-content: flex-end;
  gap: 0.5rem;
}
</style>
