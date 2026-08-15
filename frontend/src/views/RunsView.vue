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
import type { DataTableRowClickEvent } from 'primevue/datatable'
import Message from 'primevue/message'
import SelectButton from 'primevue/selectbutton'
import Tag from 'primevue/tag'
import { useConfirm } from 'primevue/useconfirm'
import { useToast } from 'primevue/usetoast'
import { runsApi, type ArchivedFilter, type RunView } from '../api/runs'
import { ApiError } from '../api/client'
import SearchField from '../components/SearchField.vue'
import { endpointLabel, excerpt, formatDateTime } from '../lib/format'
import { RUN_STATUS_SEVERITY } from '../lib/runStatus'
import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()
const router = useRouter()
const confirm = useConfirm()
const toast = useToast()

// Same row-navigation contract as the other list views: the row opens the
// run, anchors/buttons inside a cell keep their own actions.
function onRowClick(event: DataTableRowClickEvent) {
  const target = event.originalEvent.target as HTMLElement | null
  if (target?.closest('a, button')) return
  void router.push(`/runs/${(event.data as { id: number }).id}`)
}

const runs = ref<RunView[]>([])
const loading = ref(true)
const loadError = ref<string | null>(null)
const archivedFilter = ref<ArchivedFilter>('exclude')
const search = ref('')

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
  { label: 'Active', value: 'exclude' },
  { label: 'Archived', value: 'only' },
  { label: 'All', value: 'all' },
]

function endpointName(row: RunView): string {
  return endpointLabel(row.endpoint_snapshot?.name)
}

// Client-side, over what identifies a run at a glance: its number, the model
// it ran and the endpoint that served it. The archived filter stays a
// server-side query, since archived runs are not loaded at all by default.
const visibleRuns = computed(() => {
  const needle = search.value.trim().toLowerCase()
  if (needle === '') return runs.value
  return runs.value.filter((row) =>
    [`#${row.id}`, row.model_id, endpointName(row)].some((field) =>
      field.toLowerCase().includes(needle),
    ),
  )
})

const emptyMessage = computed(() => {
  if (search.value.trim() !== '') return 'No runs match this filter.'
  if (archivedFilter.value === 'only') return 'No archived runs.'
  return 'No runs yet — start one with "New run".'
})

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

    <Message v-if="loadError" severity="error" :closable="false">{{ loadError }}</Message>

    <div class="filter-row">
      <SearchField v-model="search" placeholder="Search runs" />
      <span class="filter-label">Show</span>
      <SelectButton
        v-model="archivedFilter"
        :options="archivedOptions"
        option-label="label"
        option-value="value"
        :allow-empty="false"
        size="small"
      />
    </div>

    <DataTable
      :value="visibleRuns"
      :loading="loading"
      data-key="id"
      class="table list-table row-nav"
      removable-sort
      @row-click="onRowClick"
    >
      <template #empty>{{ emptyMessage }}</template>
      <Column field="id" header="Run" sortable>
        <template #body="{ data }: { data: RunView }">
          <RouterLink :to="`/runs/${data.id}`" class="name-link">#{{ data.id }}</RouterLink>
        </template>
      </Column>
      <Column field="created_at" header="Created" sortable>
        <template #body="{ data }: { data: RunView }">{{ formatDateTime(data.created_at) }}</template>
      </Column>
      <Column header="Endpoint">
        <template #body="{ data }: { data: RunView }">{{ endpointName(data) }}</template>
      </Column>
      <Column field="model_id" header="Model" sortable>
        <template #body="{ data }: { data: RunView }">
          <span class="mono">{{ data.model_id }}</span>
        </template>
      </Column>
      <Column header="Groups">
        <template #body="{ data }: { data: RunView }">{{ data.group_names.join(', ') || '—' }}</template>
      </Column>
      <Column field="status" header="Status" sortable>
        <template #body="{ data }: { data: RunView }">
          <div class="status-cell">
            <Tag :severity="RUN_STATUS_SEVERITY[data.status]" :value="data.status" />
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
              :label="data.archived_at !== null ? 'Unarchive' : 'Archive'"
              text
              size="small"
              :loading="busyRunId === data.id"
              @click="toggleArchive(data)"
            />
            <Button
              label="Delete"
              text
              size="small"
              severity="danger"
              :loading="busyRunId === data.id"
              @click="confirmDelete(data)"
            />
          </div>
        </template>
      </Column>
    </DataTable>
  </div>
</template>

<style scoped>
.status-cell {
  display: flex;
  align-items: center;
  gap: 0.375rem;
}
</style>
