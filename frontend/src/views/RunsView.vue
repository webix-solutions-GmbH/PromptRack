<script setup lang="ts">
// Runs list — each run executed the test cases of one or more groups against
// one endpoint and model. Port of `git show master:src/app/runs/page.tsx`,
// trimmed to what `GET /api/runs` actually returns: the backend's `RunView`
// (`backend/app/api/runs.py`) carries no per-run result/rating aggregates
// (the old `listRunSummaries` computed those with SQL joins that have no
// equivalent here yet), so the "ok/err/pending" and rating-tally columns are
// dropped rather than approximated with N+1 requests — see this task's
// report for a note on adding an aggregate endpoint later.
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import Button from 'primevue/button'
import Column from 'primevue/column'
import DataTable from 'primevue/datatable'
import Message from 'primevue/message'
import Select from 'primevue/select'
import Tag from 'primevue/tag'
import { useConfirm } from 'primevue/useconfirm'
import { useToast } from 'primevue/usetoast'
import { runsApi, type ArchivedFilter, type RunStatus, type RunView } from '../api/runs'
import { ApiError } from '../api/client'
import { formatDateTime } from '../lib/format'
import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()
const router = useRouter()
const confirm = useConfirm()
const toast = useToast()

const runs = ref<RunView[]>([])
const loading = ref(true)
const loadError = ref<string | null>(null)
const archivedFilter = ref<ArchivedFilter>('exclude')

async function load() {
  loading.value = true
  loadError.value = null
  try {
    runs.value = await runsApi.list({ archived: archivedFilter.value })
  } catch (err) {
    loadError.value = err instanceof ApiError ? err.message : 'Failed to load runs.'
  } finally {
    loading.value = false
  }
}

onMounted(load)
watch(archivedFilter, load)

const archivedOptions: { label: string; value: ArchivedFilter }[] = [
  { label: 'Hidden', value: 'exclude' },
  { label: 'Only archived', value: 'only' },
  { label: 'Include archived', value: 'all' },
]

const statusSeverity: Record<RunStatus, 'secondary' | 'info' | 'success' | 'danger'> = {
  pending: 'secondary',
  running: 'info',
  completed: 'success',
  failed: 'danger',
}

function endpointName(row: RunView): string {
  return row.endpoint_snapshot?.name ?? '(deleted endpoint)'
}

function excerpt(value: string | null, max = 60): string {
  if (!value) return '—'
  const flat = value.replace(/\s+/g, ' ').trim()
  return flat.length > max ? `${flat.slice(0, max)}…` : flat
}

// --- archive / unarchive / delete -----------------------------------------

const busyRunId = ref<number | null>(null)

async function toggleArchive(row: RunView) {
  busyRunId.value = row.id
  try {
    if (row.archived_at !== null) {
      await runsApi.unarchive(row.id)
    } else {
      await runsApi.archive(row.id)
    }
    await load()
  } catch (err) {
    toast.add({
      severity: 'error',
      summary: row.archived_at !== null ? 'Failed to unarchive' : 'Failed to archive',
      detail: err instanceof ApiError ? err.message : undefined,
      life: 5000,
    })
  } finally {
    busyRunId.value = null
  }
}

function confirmDelete(row: RunView) {
  confirm.require({
    header: 'Delete run',
    message: `Delete run #${row.id} and all its results? This cannot be undone.`,
    acceptProps: { label: 'Delete', severity: 'danger' },
    rejectProps: { label: 'Cancel', text: true },
    accept: () => void removeRun(row),
  })
}

async function removeRun(row: RunView) {
  busyRunId.value = row.id
  try {
    await runsApi.remove(row.id)
    await load()
  } catch (err) {
    toast.add({
      severity: 'error',
      summary: 'Failed to delete run',
      detail: err instanceof ApiError ? err.message : undefined,
      life: 5000,
    })
  } finally {
    busyRunId.value = null
  }
}

const hasRuns = computed(() => runs.value.length > 0)
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div class="page-heading">
        <h1>Runs</h1>
        <p class="subtitle">
          Each run executes the test cases of one or more groups against a single endpoint and
          model, snapshotting everything so later edits never change its history.
        </p>
      </div>
      <Button
        v-if="auth.canWrite"
        label="New run"
        icon="pi pi-plus"
        @click="router.push('/runs/new')"
      />
    </div>

    <div class="filter-row">
      <label for="archived-filter">Archived</label>
      <Select
        id="archived-filter"
        v-model="archivedFilter"
        :options="archivedOptions"
        option-label="label"
        option-value="value"
      />
    </div>

    <Message v-if="loadError" severity="error" :closable="false">{{ loadError }}</Message>

    <DataTable :value="runs" :loading="loading" data-key="id" class="table">
      <template #empty>
        {{ hasRuns ? 'No runs match this filter.' : 'No runs yet — start one with "New run".' }}
      </template>
      <Column header="Run">
        <template #body="{ data }: { data: RunView }">
          <RouterLink :to="`/runs/${data.id}`" class="name-link">#{{ data.id }}</RouterLink>
        </template>
      </Column>
      <Column header="Created">
        <template #body="{ data }: { data: RunView }">{{ formatDateTime(data.created_at) }}</template>
      </Column>
      <Column header="Endpoint">
        <template #body="{ data }: { data: RunView }">{{ endpointName(data) }}</template>
      </Column>
      <Column header="Model">
        <template #body="{ data }: { data: RunView }">
          <span class="mono">{{ data.model_id }}</span>
        </template>
      </Column>
      <Column header="Groups">
        <template #body="{ data }: { data: RunView }">{{ data.group_names.join(', ') || '—' }}</template>
      </Column>
      <Column header="Status">
        <template #body="{ data }: { data: RunView }">
          <div class="status-cell">
            <Tag :severity="statusSeverity[data.status]" :value="data.status" />
            <Tag v-if="data.archived_at !== null" severity="warn" value="archived" />
          </div>
        </template>
      </Column>
      <Column header="Comment">
        <template #body="{ data }: { data: RunView }">{{ excerpt(data.comment) }}</template>
      </Column>
      <Column header="" class="actions-column">
        <template #body="{ data }: { data: RunView }">
          <div v-if="auth.canWrite" class="row-actions">
            <Button
              :icon="data.archived_at !== null ? 'pi pi-history' : 'pi pi-inbox'"
              text
              size="small"
              :loading="busyRunId === data.id"
              :aria-label="data.archived_at !== null ? 'Unarchive run' : 'Archive run'"
              @click="toggleArchive(data)"
            />
            <Button
              icon="pi pi-trash"
              text
              size="small"
              severity="danger"
              :loading="busyRunId === data.id"
              aria-label="Delete run"
              @click="confirmDelete(data)"
            />
          </div>
        </template>
      </Column>
    </DataTable>
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

.filter-row {
  display: flex;
  align-items: center;
  gap: 0.625rem;
}

.filter-row label {
  font-size: 0.8125rem;
  font-weight: 500;
  color: var(--p-text-muted-color);
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
}

.status-cell {
  display: flex;
  align-items: center;
  gap: 0.375rem;
}

.actions-column {
  width: 1%;
  white-space: nowrap;
}

.row-actions {
  display: flex;
  align-items: center;
  gap: 0.125rem;
}
</style>
