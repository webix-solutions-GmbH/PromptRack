<script setup lang="ts">
// Results — the comparison matrix, both pivots. Port of
// `git show legacy-nextjs:src/app/results/page.tsx` + its `compare-row.tsx` /
// `model-picker.tsx` / `run-picker.tsx` / `group-filter.tsx`, collapsed into
// one view since the backend now does all the pivoting server-side
// (`GET /api/results/matrix`, Task 5.1) and hands back a single payload with
// the pickers, the matrix and its tallies already agreeing with each other.
//
// `?mode=` wins; without it a URL carrying `?runs=` stays in run mode (so a
// baseline-comparison deep link `?mode=runs&runs=a,b` keeps its pivot),
// everything else defaults to models. Every picker interaction re-requests
// the matrix and then rewrites the URL from the server's *actual* selection
// (foreign/archived/over-the-cap ids get silently dropped there), so the
// address bar can never claim a selection the table does not show.
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import Checkbox from 'primevue/checkbox'
import Column from 'primevue/column'
import DataTable from 'primevue/datatable'
import type { DataTableRowClickEvent } from 'primevue/datatable'
import Message from 'primevue/message'
import SelectButton from 'primevue/selectbutton'
import {
  resultsApi,
  type CompareCellView,
  type CompareMode,
  type CompareRunView,
  type MatrixResponse,
  type ModelColumnView,
} from '../api/results'
import { ApiError } from '../api/client'
import { formatDateTime, formatDuration, formatRate } from '../lib/format'
import type { Rating } from '../lib/rating'
import MatrixTable from '../components/results/MatrixTable.vue'
import { useAuthStore } from '../stores/auth'

// Mirrors `MAX_COMPARE_RUNS`/`MAX_COMPARE_MODELS` in
// `backend/app/services/compare.py` — only used here to stop offering more
// checkboxes once a selection is full; the backend enforces the real cap.
const MAX_COMPARE_RUNS = 4
const MAX_COMPARE_MODELS = 6

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

function queryStrings(raw: unknown): string[] {
  if (raw === undefined || raw === null) return []
  return Array.isArray(raw)
    ? raw.filter((value): value is string => typeof value === 'string')
    : [String(raw)]
}

function parseIdsFromQuery(raw: unknown): number[] {
  return queryStrings(raw)
    .join(',')
    .split(',')
    .map((part) => Number(part.trim()))
    .filter((n) => Number.isInteger(n) && n > 0)
}

function initialMode(): CompareMode {
  const raw = route.query.mode
  if (raw === 'runs' || raw === 'models') return raw
  return route.query.runs ? 'runs' : 'models'
}

const mode = ref<CompareMode>(initialMode())
const selectedRunIds = ref<number[]>(parseIdsFromQuery(route.query.runs))
const selectedModelKeys = ref<string[]>(queryStrings(route.query.models))
const selectedGroupIds = ref<number[]>(parseIdsFromQuery(route.query.group))

const matrix = ref<MatrixResponse | null>(null)
const loading = ref(true)
const loadError = ref<string | null>(null)

function syncUrl() {
  const query: Record<string, string | string[]> = { mode: mode.value }
  if (mode.value === 'runs' && selectedRunIds.value.length > 0) {
    query.runs = selectedRunIds.value.join(',')
  }
  if (mode.value === 'models') {
    if (selectedModelKeys.value.length > 0) query.models = [...selectedModelKeys.value]
    if (selectedGroupIds.value.length > 0) query.group = selectedGroupIds.value.join(',')
  }
  void router.replace({ query })
}

async function load() {
  loading.value = true
  loadError.value = null
  try {
    const response = await resultsApi.matrix({
      mode: mode.value,
      runs: mode.value === 'runs' ? selectedRunIds.value : undefined,
      models: mode.value === 'models' ? selectedModelKeys.value : undefined,
      group: mode.value === 'models' ? selectedGroupIds.value : undefined,
    })
    matrix.value = response
    // The server is the source of truth for what selection actually applied
    // (foreign ids dropped, archived-unless-already-selected, over the cap
    // truncated) — reconcile local picker state to match rather than
    // trusting what was asked for.
    mode.value = response.mode
    selectedRunIds.value = response.selected_run_ids
    selectedModelKeys.value = response.selected_model_keys
    selectedGroupIds.value = response.selected_group_ids
    syncUrl()
  } catch (err) {
    loadError.value = err instanceof ApiError ? err.message : 'Failed to load the comparison.'
  } finally {
    loading.value = false
  }
}

onMounted(load)

const modeOptions: { label: string; value: CompareMode }[] = [
  { label: 'By model', value: 'models' },
  { label: 'By run', value: 'runs' },
]

function switchMode(next: CompareMode | null) {
  if (next === null || mode.value === next) return
  mode.value = next
  void load()
}

function toggleRun(id: number) {
  if (selectedRunIds.value.includes(id)) {
    selectedRunIds.value = selectedRunIds.value.filter((existing) => existing !== id)
  } else if (selectedRunIds.value.length < MAX_COMPARE_RUNS) {
    selectedRunIds.value = [...selectedRunIds.value, id]
  } else {
    return
  }
  void load()
}

function toggleModel(key: string) {
  if (selectedModelKeys.value.includes(key)) {
    selectedModelKeys.value = selectedModelKeys.value.filter((existing) => existing !== key)
  } else if (selectedModelKeys.value.length < MAX_COMPARE_MODELS) {
    selectedModelKeys.value = [...selectedModelKeys.value, key]
  } else {
    return
  }
  void load()
}

// No group selected means every group, which is what an empty selection
// already encodes — so the multi-select needs no "All" option of its own.
function setGroupFilter(ids: number[] | null) {
  selectedGroupIds.value = ids ?? []
  void load()
}

// --- picker rows -----------------------------------------------------------

// The row is the one click target; the checkbox in it is display-only (see the
// template comment), so both tables toggle from the row-click event.
function onRunRowClick(event: DataTableRowClickEvent) {
  const target = event.originalEvent.target as HTMLElement | null
  if (target?.closest('a, button')) return
  toggleRun((event.data as CompareRunView).id)
}

function onModelRowClick(event: DataTableRowClickEvent) {
  const target = event.originalEvent.target as HTMLElement | null
  if (target?.closest('a, button')) return
  toggleModel((event.data as ModelColumnView).key)
}

function runRowClass(run: CompareRunView) {
  return selectedRunIds.value.includes(run.id) ? 'selected' : ''
}

function modelRowClass(column: ModelColumnView) {
  return selectedModelKeys.value.includes(column.key) ? 'selected' : ''
}

// --- derived view state ----------------------------------------------------

const isModels = computed(() => mode.value === 'models')
const columnCount = computed(() =>
  isModels.value ? selectedModelKeys.value.length : selectedRunIds.value.length,
)
const rows = computed(() => matrix.value?.rows ?? [])
const belowMinimum = computed(() => matrix.value !== null && columnCount.value < matrix.value.min_columns)
const groupOptions = computed(() =>
  (matrix.value?.groups ?? []).map((group) => ({
    label: `${group.name} (${group.test_case_count})`,
    value: group.id,
  })),
)

// --- rating write-through ------------------------------------------------

// `RatingButtons` has already sent the PATCH by the time this fires; patching
// the cell in place keeps it and the tallies it feeds honest without
// re-requesting the whole comparison.
function handleRatingChange(payload: {
  cell: CompareCellView
  patch: { rating?: Rating | null; ratingNote?: string | null }
}) {
  if (!matrix.value) return
  const { cell: target, patch } = payload
  const updated: CompareCellView = {
    ...target,
    ...('rating' in patch ? { rating: patch.rating ?? null } : {}),
    ...('ratingNote' in patch ? { rating_note: patch.ratingNote ?? null } : {}),
  }
  matrix.value = {
    ...matrix.value,
    rows: matrix.value.rows.map((row) => ({
      ...row,
      cells: row.cells.map((cell) => (cell !== null && cell.id === target.id ? updated : cell)),
    })),
  }
}
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div class="page-heading">
        <h1>Results</h1>
        <!-- On the heading's side of the header row: the pivot changes how the
             page reads, it does not add anything. -->
        <SelectButton
          class="view-toggle"
          :model-value="mode"
          :options="modeOptions"
          option-label="label"
          option-value="value"
          :allow-empty="false"
          size="small"
          @update:model-value="switchMode"
        />
      </div>
    </div>

    <Message v-if="loadError" severity="error" :closable="false">{{ loadError }}</Message>

    <template v-if="matrix">
      <p v-if="!isModels && matrix.hidden_archived_runs > 0" class="hint-text">
        {{ matrix.hidden_archived_runs }} archived run{{ matrix.hidden_archived_runs === 1 ? ' is' : 's are' }}
        not listed below.
      </p>

      <section v-if="!isModels" class="picker">
        <p v-if="matrix.available_runs.length === 0" class="empty-state">
          No comparable runs yet — finish a run first.
        </p>
        <DataTable
          v-else
          :value="matrix.available_runs"
          :loading="loading"
          data-key="id"
          class="table list-table row-nav"
          removable-sort
          :row-class="runRowClass"
          @row-click="onRunRowClick"
        >
          <Column class="check-column">
            <template #body="{ data }: { data: CompareRunView }">
              <!-- Display-only: the row is the one click target. A click on
                   the checkbox itself used to fire twice (the input's click
                   plus the label-forwarded one), toggling on and straight
                   back off — pointer-events: none routes it to the row. -->
              <Checkbox
                :model-value="selectedRunIds.includes(data.id)"
                binary
                :disabled="!selectedRunIds.includes(data.id) && selectedRunIds.length >= MAX_COMPARE_RUNS"
              />
            </template>
          </Column>
          <Column field="id" header="Run" sortable>
            <template #body="{ data }: { data: CompareRunView }">#{{ data.id }}</template>
          </Column>
          <Column field="model_id" header="Model" sortable>
            <template #body="{ data }: { data: CompareRunView }">
              <span class="mono">{{ data.model_id }}</span>
            </template>
          </Column>
          <Column field="endpoint_name" header="Endpoint" sortable />
          <Column field="created_at" header="Created" sortable>
            <template #body="{ data }: { data: CompareRunView }">
              {{ formatDateTime(data.created_at) }}
            </template>
          </Column>
          <Column>
            <template #header><span title="good / meh / bad">Rating</span></template>
            <template #body="{ data }: { data: CompareRunView }">
              <span class="good-count">{{ data.good }}</span
              >/<span class="meh-count">{{ data.meh }}</span
              >/<span class="bad-count">{{ data.bad }}</span>
            </template>
          </Column>
          <Column field="avg_rate" header="Avg speed" sortable>
            <template #body="{ data }: { data: CompareRunView }">
              {{ formatRate(data.avg_rate) }}
            </template>
          </Column>
          <Column field="total_duration_ms" sortable>
            <template #header>
              <span title="Sum of every result's generation time; tool waiting excluded">
                Total time
              </span>
            </template>
            <template #body="{ data }: { data: CompareRunView }">
              {{ formatDuration(data.total_duration_ms) }}
            </template>
          </Column>
        </DataTable>
      </section>

      <section v-else class="picker">
        <p v-if="matrix.available_models.length === 0" class="empty-state">
          No results yet — finish a run first, then come back.
        </p>
        <template v-else>
          <DataTable
            :value="matrix.available_models"
            :loading="loading"
            data-key="key"
            class="table list-table row-nav"
            removable-sort
            :row-class="modelRowClass"
            @row-click="onModelRowClick"
          >
            <Column class="check-column">
              <template #body="{ data }: { data: ModelColumnView }">
                <!-- Display-only, same as the run picker's. -->
                <Checkbox
                  :model-value="selectedModelKeys.includes(data.key)"
                  binary
                  :disabled="
                    !selectedModelKeys.includes(data.key) &&
                    selectedModelKeys.length >= MAX_COMPARE_MODELS
                  "
                />
              </template>
            </Column>
            <Column field="model_id" header="Model" sortable>
              <template #body="{ data }: { data: ModelColumnView }">
                <span class="mono">{{ data.model_id }}</span>
              </template>
            </Column>
            <Column field="endpoint_name" header="Endpoint" sortable />
            <Column field="test_case_count" sortable>
              <template #header>
                <span title="Distinct test cases with a usable result">Test cases</span>
              </template>
            </Column>
            <Column field="run_count" header="Runs" sortable />
            <Column field="latest_run_at" header="Latest run" sortable>
              <template #body="{ data }: { data: ModelColumnView }">
                {{ formatDateTime(data.latest_run_at) }}
              </template>
            </Column>
            <Column>
              <template #header><span title="good / meh / bad">Rating</span></template>
              <template #body="{ data }: { data: ModelColumnView }">
                <span class="good-count">{{ data.good }}</span
                >/<span class="meh-count">{{ data.meh }}</span
                >/<span class="bad-count">{{ data.bad }}</span>
              </template>
            </Column>
            <Column field="avg_rate" header="Avg speed" sortable>
              <template #body="{ data }: { data: ModelColumnView }">
                {{ formatRate(data.avg_rate) }}
              </template>
            </Column>
            <Column field="total_duration_ms" sortable>
              <template #header>
                <span title="Sum of every result's generation time; tool waiting excluded">
                  Total time
                </span>
              </template>
              <template #body="{ data }: { data: ModelColumnView }">
                {{ formatDuration(data.total_duration_ms) }}
              </template>
            </Column>
          </DataTable>

          <div v-if="matrix.groups.length > 0" class="filter-row">
            <span class="filter-label">Groups</span>
            <SelectButton
              :model-value="selectedGroupIds"
              :options="groupOptions"
              option-label="label"
              option-value="value"
              multiple
              size="small"
              @update:model-value="setGroupFilter"
            />
          </div>
        </template>
      </section>

      <div v-if="belowMinimum" class="empty-state">
        {{
          isModels
            ? 'Select a model above to see its results — or several to compare them.'
            : `Select at least ${matrix.min_columns} runs above to build the comparison matrix.`
        }}
      </div>
      <div v-else-if="rows.length === 0" class="empty-state">
        {{
          isModels
            ? `None of the test cases in scope has a result from the selected ${columnCount === 1 ? 'model' : 'models'} yet.`
            : 'The selected runs have no results to compare.'
        }}
      </div>
      <section v-else class="matrix-section">
        <div class="matrix-heading">
          <h2>
            {{ rows.length }} test case{{ rows.length === 1 ? '' : 's' }} × {{ columnCount }}
            {{ isModels ? 'model' : 'run' }}{{ columnCount === 1 ? '' : 's' }}
          </h2>
          <span v-if="isModels && matrix.uncovered_test_cases > 0" class="uncovered-note">
            {{ matrix.uncovered_test_cases }} test case{{ matrix.uncovered_test_cases === 1 ? '' : 's' }} in scope
            not answered by any selected model
          </span>
        </div>

        <MatrixTable
          :mode="mode"
          :rows="rows"
          :run-columns="matrix.run_columns"
          :model-columns="matrix.model_columns"
          :column-tallies="matrix.column_tallies"
          :can-write="auth.canWrite"
          @rating-change="handleRatingChange"
        />
      </section>
    </template>
  </div>
</template>

<style scoped>
.view-toggle {
  margin-top: 0.75rem;
}

.hint-text {
  margin: -0.5rem 0 0;
  font-size: 0.8125rem;
  color: var(--p-text-muted-color);
}

.picker {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

/* Shrink-to-fit leading column: the checkbox takes its own width and the
 * columns after it keep the rest. */
.picker :deep(.check-column) {
  width: 1%;
}

/* See the template comment: the checkbox is display-only, the row toggles. */
.picker :deep(.p-checkbox) {
  pointer-events: none;
}

.picker :deep(.p-datatable-tbody > tr.selected) {
  background: var(--p-highlight-background);
}

.good-count {
  color: var(--p-green-600, var(--p-green-500));
}

.meh-count {
  color: var(--p-yellow-600, var(--p-yellow-500));
}

.bad-count {
  color: var(--p-red-600, var(--p-red-500));
}

.matrix-section {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.matrix-heading {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  justify-content: space-between;
  gap: 0.5rem;
}

.matrix-heading h2 {
  margin: 0;
  font-size: 0.875rem;
  font-weight: 500;
  color: var(--p-text-color);
}

.uncovered-note {
  font-size: 0.75rem;
  color: var(--p-text-muted-color);
}
</style>
