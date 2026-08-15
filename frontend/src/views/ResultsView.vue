<script setup lang="ts">
// Results — the comparison matrix, both pivots. Port of
// `git show master:src/app/results/page.tsx` + its `compare-row.tsx` /
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
import Button from 'primevue/button'
import Checkbox from 'primevue/checkbox'
import Message from 'primevue/message'
import { resultsApi, type CompareCellView, type CompareMode, type MatrixResponse } from '../api/results'
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

function switchMode(next: CompareMode) {
  if (mode.value === next) return
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

function toggleGroup(id: number | null) {
  if (id === null) {
    selectedGroupIds.value = []
  } else if (selectedGroupIds.value.includes(id)) {
    selectedGroupIds.value = selectedGroupIds.value.filter((existing) => existing !== id)
  } else {
    selectedGroupIds.value = [...selectedGroupIds.value, id]
  }
  void load()
}

// --- derived view state ----------------------------------------------------

const isModels = computed(() => mode.value === 'models')
const columnCount = computed(() =>
  isModels.value ? selectedModelKeys.value.length : selectedRunIds.value.length,
)
const rows = computed(() => matrix.value?.rows ?? [])
const belowMinimum = computed(() => matrix.value !== null && columnCount.value < matrix.value.min_columns)

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
      <h1>Results</h1>
      <div class="mode-tabs">
        <Button
          label="By model"
          :severity="isModels ? undefined : 'secondary'"
          :outlined="!isModels"
          size="small"
          @click="switchMode('models')"
        />
        <Button
          label="By run"
          :severity="!isModels ? undefined : 'secondary'"
          :outlined="isModels"
          size="small"
          @click="switchMode('runs')"
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
        <p v-if="matrix.available_runs.length === 0" class="empty-text">
          No comparable runs yet — finish a run first.
        </p>
        <table v-else class="picker-table">
          <thead>
            <tr>
              <th></th>
              <th>Run</th>
              <th>Model</th>
              <th>Endpoint</th>
              <th>Created</th>
              <th title="good / meh / bad">Rating</th>
              <th>Avg speed</th>
              <th title="Sum of every result's generation time; tool waiting excluded">
                Total time
              </th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="run in matrix.available_runs"
              :key="run.id"
              class="picker-row"
              :class="{ selected: selectedRunIds.includes(run.id) }"
              @click="toggleRun(run.id)"
            >
              <td>
                <Checkbox
                  :model-value="selectedRunIds.includes(run.id)"
                  binary
                  :disabled="!selectedRunIds.includes(run.id) && selectedRunIds.length >= MAX_COMPARE_RUNS"
                  @click.stop="toggleRun(run.id)"
                />
              </td>
              <td>#{{ run.id }}</td>
              <td class="mono">{{ run.model_id }}</td>
              <td>{{ run.endpoint_name }}</td>
              <td>{{ formatDateTime(run.created_at) }}</td>
              <td>{{ run.good }}/{{ run.meh }}/{{ run.bad }}</td>
              <td>{{ formatRate(run.avg_rate) }}</td>
              <td>{{ formatDuration(run.total_duration_ms) }}</td>
            </tr>
          </tbody>
        </table>
      </section>

      <section v-else class="picker">
        <p v-if="matrix.available_models.length === 0" class="empty-text">
          No results yet — finish a run first, then come back.
        </p>
        <template v-else>
          <table class="picker-table">
            <thead>
              <tr>
                <th></th>
                <th>Model</th>
                <th>Endpoint</th>
                <th title="Distinct test cases with a usable result">Test cases</th>
                <th>Runs</th>
                <th>Latest run</th>
                <th title="good / meh / bad">Rating</th>
                <th>Avg speed</th>
                <th title="Sum of every result's generation time; tool waiting excluded">
                  Total time
                </th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="column in matrix.available_models"
                :key="column.key"
                class="picker-row"
                :class="{ selected: selectedModelKeys.includes(column.key) }"
                @click="toggleModel(column.key)"
              >
                <td>
                  <Checkbox
                    :model-value="selectedModelKeys.includes(column.key)"
                    binary
                    :disabled="
                      !selectedModelKeys.includes(column.key) &&
                      selectedModelKeys.length >= MAX_COMPARE_MODELS
                    "
                    @click.stop="toggleModel(column.key)"
                  />
                </td>
                <td class="mono">{{ column.model_id }}</td>
                <td>{{ column.endpoint_name }}</td>
                <td>{{ column.test_case_count }}</td>
                <td>{{ column.run_count }}</td>
                <td>{{ formatDateTime(column.latest_run_at) }}</td>
                <td>{{ column.good }}/{{ column.meh }}/{{ column.bad }}</td>
                <td>{{ formatRate(column.avg_rate) }}</td>
                <td>{{ formatDuration(column.total_duration_ms) }}</td>
              </tr>
            </tbody>
          </table>

          <div v-if="matrix.groups.length > 0" class="group-chips">
            <button
              type="button"
              class="chip-toggle"
              :class="{ active: selectedGroupIds.length === 0 }"
              @click="toggleGroup(null)"
            >
              All
            </button>
            <button
              v-for="group in matrix.groups"
              :key="group.id"
              type="button"
              class="chip-toggle"
              :class="{ active: selectedGroupIds.includes(group.id) }"
              @click="toggleGroup(group.id)"
            >
              {{ group.name }} ({{ group.test_case_count }})
            </button>
          </div>
        </template>
      </section>

      <div v-if="belowMinimum" class="placeholder">
        {{
          isModels
            ? 'Select a model above to see its results — or several to compare them.'
            : `Select at least ${matrix.min_columns} runs above to build the comparison matrix.`
        }}
      </div>
      <div v-else-if="rows.length === 0" class="placeholder">
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
.page {
  display: flex;
  flex-direction: column;
  gap: 1.25rem;
}

.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
}

.page-header h1 {
  font-size: 1.5rem;
  font-weight: 600;
  margin: 0;
}

.mode-tabs {
  display: flex;
  gap: 0.375rem;
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

.empty-text {
  margin: 0;
  padding: 1.5rem;
  text-align: center;
  border: 1px dashed var(--p-content-border-color);
  border-radius: var(--p-content-border-radius);
  font-size: 0.875rem;
  color: var(--p-text-muted-color);
}

/* `border-collapse: collapse` makes browsers drop `border-radius` entirely,
 * which left this table square-cornered next to the .list-table DataTables.
 * Separate borders with zero spacing render identically while letting the
 * radius clip; the head cells round their own top corners since the thead
 * background would otherwise paint over them. */
.picker-table {
  width: 100%;
  border-collapse: separate;
  border-spacing: 0;
  border: 1px solid var(--p-content-border-color);
  border-radius: var(--p-content-border-radius);
  font-size: 0.8125rem;
}

.picker-table thead th:first-child {
  border-top-left-radius: calc(var(--p-content-border-radius) - 1px);
}

.picker-table thead th:last-child {
  border-top-right-radius: calc(var(--p-content-border-radius) - 1px);
}

.picker-table tbody tr:last-child td {
  border-bottom: none;
}

.picker-table thead {
  background: var(--p-content-hover-background);
  text-transform: uppercase;
  letter-spacing: 0.03em;
  font-size: 0.6875rem;
  color: var(--p-text-muted-color);
}

.picker-table th,
.picker-table td {
  padding: 0.5rem 0.75rem;
  text-align: left;
  border-bottom: 1px solid var(--p-content-border-color);
}

.picker-row {
  cursor: pointer;
}

.picker-row:hover {
  background: var(--p-content-hover-background);
}

.picker-row.selected {
  background: var(--p-highlight-background);
}

.mono {
  font-family: var(--p-font-family-mono, ui-monospace, monospace);
  font-size: 0.75rem;
}

.group-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 0.375rem;
}

.chip-toggle {
  border: 1px solid var(--p-content-border-color);
  border-radius: 999px;
  background: transparent;
  padding: 0.25rem 0.75rem;
  font-size: 0.75rem;
  color: var(--p-text-color);
  cursor: pointer;
}

.chip-toggle.active {
  border-color: var(--p-primary-color);
  background: var(--p-highlight-background);
}

.placeholder {
  padding: 2.5rem;
  text-align: center;
  border: 1px dashed var(--p-content-border-color);
  border-radius: var(--p-content-border-radius);
  font-size: 0.875rem;
  color: var(--p-text-muted-color);
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
