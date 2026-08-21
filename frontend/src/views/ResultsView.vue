<script setup lang="ts">
// Results — the comparison matrix, both pivots, one view: the backend does
// all the pivoting server-side (`GET /api/results/matrix`) and hands back a
// single payload with the pickers, the matrix and its tallies already
// agreeing with each other.
//
// `?mode=` wins; without it a URL carrying `?runs=` stays in run mode (so a
// baseline-comparison deep link `?mode=runs&runs=a,b` keeps its pivot),
// everything else defaults to models. Every picker interaction re-requests
// the matrix and then rewrites the URL from the server's *actual* selection
// (foreign/archived/over-the-cap ids get silently dropped there), so the
// address bar can never claim a selection the table does not show.
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import Button from 'primevue/button'
import Checkbox from 'primevue/checkbox'
import Column from 'primevue/column'
import DataTable from 'primevue/datatable'
import type { DataTableRowClickEvent } from 'primevue/datatable'
import Message from 'primevue/message'
import SelectButton from 'primevue/selectbutton'
import {
  resultsApi,
  type ColumnTally,
  type CompareCellView,
  type CompareMode,
  type CompareRowView,
  type CompareRunView,
  type MatrixResponse,
  type ModelColumnView,
} from '../api/results'
import { ApiError } from '../api/client'
import { formatDateTime, formatDuration, formatRate } from '../lib/format'
import { countRatings, type Rating } from '../lib/rating'
import { applyColumnTallyDelta, applyTallyDelta } from '../lib/ratingTally'
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

// --- the meh/bad filter ----------------------------------------------------

// Reviewing a matrix is mostly a hunt for what is not good enough, and both
// pivots routinely put dozens of rows on screen. `attention` keeps the rows
// where *any* cell was rated meh or bad — the whole line, never the single
// cell, because the comparison is the point: a row is worth reading precisely
// against the columns that did better on it.
//
// Client-side, over the matrix already loaded, unlike every picker above:
// those change the *selection* and so must be reconciled against the server,
// whereas this only narrows what is rendered — and re-requesting would
// re-render the table under a reviewer halfway through a rating pass,
// collapsing every open peek.
type RowFilter = 'all' | 'attention'

const rowFilter = ref<RowFilter>('all')
const rowFilterOptions: { label: string; value: RowFilter }[] = [
  { label: 'All', value: 'all' },
  { label: 'meh + bad', value: 'attention' },
]

function needsAttention(row: CompareRowView): boolean {
  return row.cells.some((cell) => cell?.rating === 'meh' || cell?.rating === 'bad')
}

const attentionRowCount = computed(() => rows.value.filter(needsAttention).length)
const filtering = computed(() => rowFilter.value === 'attention')
const visibleRows = computed(() =>
  filtering.value ? rows.value.filter(needsAttention) : rows.value,
)

/** One model column's totals over a given set of rows — the client-side twin
 * of `_tallies` in `backend/app/api/results.py`, down to reporting `null`
 * rather than 0 for "nothing measured". */
function tallyColumn(rows: CompareRowView[], column: number): ColumnTally {
  const cells = rows
    .map((row) => row.cells[column])
    .filter((cell): cell is CompareCellView => cell != null)
  const rates = cells
    .map((cell) => cell.tokens_per_sec)
    .filter((rate): rate is number => rate !== null)
  const durations = cells
    .map((cell) => cell.duration_ms)
    .filter((duration): duration is number => duration !== null)
  return {
    answered: cells.length,
    ...countRatings(cells.map((cell) => cell.rating)),
    avg_rate: rates.length > 0 ? rates.reduce((sum, rate) => sum + rate, 0) / rates.length : null,
    total_duration_ms:
      durations.length > 0 ? durations.reduce((sum, duration) => sum + duration, 0) : null,
  }
}

// A model-mode column tally counts the cells **on screen** and its header
// reads "n/<rows> answered", so hiding rows here has to re-derive it — left
// alone it would report a column the table no longer shows. Run mode needs
// none of this: its header numbers are the run's own totals, which never
// claimed to describe what is on screen.
const visibleTallies = computed(() => {
  const tallies = matrix.value?.column_tallies ?? []
  if (!filtering.value) return tallies
  return tallies.map((_, index) => tallyColumn(visibleRows.value, index))
})

// Rating the last meh/bad cell away takes the control off screen; a table
// still filtered by it would then be stranded empty with nothing to undo it.
watch(attentionRowCount, (count) => {
  if (count === 0) rowFilter.value = 'all'
})

// --- fullscreen ------------------------------------------------------------

// Not the browser's Fullscreen API, deliberately: that one takes over the
// whole screen, hides the tab strip and the address bar, and can only be left
// through a browser-drawn affordance — too heavy for "give the table the
// window". This is the app's own maximise instead: `.matrix-section` becomes a
// fixed overlay over the shell (see the style block), the matrix keeps every
// behaviour it has in place — sticky headers, peeks, rating — and Esc leaves.
//
// It stays out of the URL: the pickers are reconciled against the server on
// every load and `syncUrl` rewrites the query from that, whereas this is a
// transient way of looking at the page rather than part of the selection a
// link is supposed to carry.
const expanded = ref(false)

/** A peek pinned open over the matrix — see the Escape handling below. */
const peekPinned = ref(false)

function toggleExpanded() {
  expanded.value = !expanded.value
}

function onKeydown(event: KeyboardEvent) {
  if (event.key !== 'Escape' || !expanded.value) return
  // One keypress, one layer: a pinned rubric is dismissed and the matrix
  // stays maximised (dismissing a rubric is not leaving the view), and the
  // next Escape leaves.
  //
  // Which is why this listens in the *capture* phase — see the registration
  // below. PrimeVue's dialog closes on the same Escape without stopping the
  // event, and a bubble-phase listener reads `peekPinned` after Vue has
  // already flushed it back to false: the reactivity queue is drained at the
  // microtask checkpoint between two listener callbacks, so "check a flag the
  // earlier handler just invalidated" cannot work in that order. Capture runs
  // before any of it.
  if (peekPinned.value) return
  expanded.value = false
}

// The overlay covers the shell but the content column underneath still
// scrolls, so a wheel event that reaches past the table would move a page
// nobody can see. One class on <body>, cleaned up by the same watcher on the
// way out and by unmount, so navigating away mid-fullscreen cannot strand it.
watch(expanded, (on) => {
  document.body.classList.toggle('results-fullscreen', on)
})

// Capture, for the ordering `onKeydown` depends on — and nothing else inside
// the matrix handles Escape, so running first costs no other handler its key.
onMounted(() => window.addEventListener('keydown', onKeydown, true))
onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKeydown, true)
  document.body.classList.remove('results-fullscreen')
})

// Nothing to maximise is nothing to stay maximised over: emptying the
// selection while expanded unmounts the section the exit button lives in, so
// the state has to follow the matrix rather than outlive it.
watch(
  () => belowMinimum.value || rows.value.length === 0,
  (empty) => {
    if (empty) expanded.value = false
  },
)

// --- rating write-through ------------------------------------------------

// `RatingButtons` has already sent the PATCH by the time this fires; this
// patches the cell in place and, when the patch actually carries a rating,
// adjusts the one column tally (model mode) or run column + `available_runs`
// entry (run mode) that cell feeds by delta — same immutable assignment,
// no refetch of the whole comparison.
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

  // The column a cell belongs to is just its index within `row.cells` — the
  // same index that indexes `run_columns`/`column_tallies`/`model_columns`.
  let columnIndex = -1
  for (const row of matrix.value.rows) {
    const index = row.cells.findIndex((cell) => cell !== null && cell.id === target.id)
    if (index !== -1) {
      columnIndex = index
      break
    }
  }

  const rows = matrix.value.rows.map((row) => ({
    ...row,
    cells: row.cells.map((cell) => (cell !== null && cell.id === target.id ? updated : cell)),
  }))

  // Only a rating change moves a tally — a note-only patch leaves every
  // tally untouched.
  if (!('rating' in patch) || columnIndex === -1) {
    matrix.value = { ...matrix.value, rows }
    return
  }
  const oldRating = target.rating
  const newRating = patch.rating ?? null

  if (matrix.value.mode === 'models') {
    matrix.value = {
      ...matrix.value,
      rows,
      column_tallies: matrix.value.column_tallies.map((tally, index) =>
        index === columnIndex ? applyColumnTallyDelta(tally, oldRating, newRating) : tally,
      ),
      // `model_columns` mirrors `column_tallies` index-for-index (the
      // selected subset); `available_models` holds every model column, so it
      // has to be matched by key rather than index.
      model_columns: matrix.value.model_columns.map((column, index) =>
        index === columnIndex ? applyTallyDelta(column, oldRating, newRating) : column,
      ),
      available_models: matrix.value.available_models.map((model) =>
        model.key === target.column_key ? applyTallyDelta(model, oldRating, newRating) : model,
      ),
    }
    return
  }

  const runColumns = matrix.value.run_columns.map((column, index) =>
    index === columnIndex ? applyTallyDelta(column, oldRating, newRating) : column,
  )
  const changedRunId = runColumns[columnIndex]?.id
  matrix.value = {
    ...matrix.value,
    rows,
    run_columns: runColumns,
    available_runs: matrix.value.available_runs.map((run) =>
      run.id === changedRunId ? applyTallyDelta(run, oldRating, newRating) : run,
    ),
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
      <p v-if="!isModels && matrix.hidden_archived_runs > 0" class="hint hint-text">
        {{ matrix.hidden_archived_runs }} archived run{{ matrix.hidden_archived_runs === 1 ? ' is' : 's are' }}
        not listed below.
      </p>

      <section v-if="!isModels" class="picker">
        <p v-if="matrix.available_runs.length === 0" class="empty-state">
          No comparable runs yet — finish a run first.
        </p>
        <!-- Paginated, and deliberately at a small page size: this picker sits
             *above* the matrix, so every row it renders is height the reader
             scrolls past on the way to the results they came for. The list
             grows without bound as runs accumulate, while the reason to look
             at it — pick the two or three runs to compare — never needs more
             than a screenful. `always-show-paginator` off so a workspace with
             four runs doesn't grow a pager bar under them. -->
        <DataTable
          v-else
          :value="matrix.available_runs"
          :loading="loading"
          data-key="id"
          class="table list-table row-nav"
          removable-sort
          paginator
          :rows="5"
          :always-show-paginator="false"
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
          <!-- The note, as a bubble rather than a column of text: a comment is
               free text that would otherwise be excerpted to a few words in a
               picker this dense, and an excerpt of "temperature 0.2 vs 0.7,
               same quant" is worse than an icon that shows the whole thing on
               hover. The icon renders only when there *is* a note, so the
               column reads as "which of these runs was annotated". -->
          <Column class="comment-column">
            <template #header>
              <span title="The note left when the run was started">Note</span>
            </template>
            <template #body="{ data }: { data: CompareRunView }">
              <span
                v-if="data.comment"
                v-tooltip.top="{ value: data.comment, class: 'comment-tooltip', showDelay: 120 }"
                class="comment-bubble"
                :aria-label="data.comment"
              >
                <i class="pi pi-comment" aria-hidden="true" />
              </span>
              <span v-else class="comment-none" aria-hidden="true">—</span>
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
          <!-- Same reasoning as the run picker's paginator above, at a
               larger page size: a model row is one line where a run row
               carries a note and a params line, and the model list is the
               shorter of the two to begin with. -->
          <DataTable
            :value="matrix.available_models"
            :loading="loading"
            data-key="key"
            class="table list-table row-nav"
            removable-sort
            paginator
            :rows="10"
            :always-show-paginator="false"
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
            : 'Select a run above to see its results — or several to compare them.'
        }}
      </div>
      <div v-else-if="rows.length === 0" class="empty-state">
        {{
          isModels
            ? `None of the test cases in scope has a result from the selected ${columnCount === 1 ? 'model' : 'models'} yet.`
            : 'The selected runs have no results to compare.'
        }}
      </div>
      <!-- Fullscreen is this same section promoted to a fixed overlay, not a
           second copy of it behind a `v-if`: the heading is already the strip
           above the table, so expanded it simply becomes the overlay's
           toolbar, and the matrix below it is the one that was on the page a
           moment ago — same component, same scroll position, same open
           peeks. -->
      <section v-else class="matrix-section" :class="{ 'matrix-section-expanded': expanded }">
        <div class="matrix-heading">
          <h2>
            <template v-if="filtering">{{ visibleRows.length }} of {{ rows.length }}</template>
            <template v-else>{{ rows.length }}</template>
            <!-- Plural follows the total, which is the number the noun sits
                 next to once the filter narrows it: "1 of 5 test cases". -->
            test case{{ rows.length === 1 ? '' : 's' }} × {{ columnCount }}
            {{ isModels ? 'model' : 'run' }}{{ columnCount === 1 ? '' : 's' }}
          </h2>
          <div class="matrix-heading-actions">
            <!-- In the matrix's own heading rather than a `.filter-row` above
                 the section, for the same reason the fullscreen button is:
                 expanded, this strip *is* the toolbar, and a control left on
                 the page underneath would be unreachable exactly where a long
                 matrix most needs narrowing. Only once something is rated
                 meh or bad — a filter for a state the matrix is not in is a
                 control that can do nothing. -->
            <div v-if="attentionRowCount > 0" class="matrix-filter">
              <span class="filter-label">Show</span>
              <SelectButton
                v-model="rowFilter"
                :options="rowFilterOptions"
                option-label="label"
                option-value="value"
                :allow-empty="false"
                size="small"
              />
            </div>
            <span v-if="isModels && matrix.uncovered_test_cases > 0" class="uncovered-note">
              {{ matrix.uncovered_test_cases }} test case{{ matrix.uncovered_test_cases === 1 ? '' : 's' }} in scope
              not answered by any selected model
            </span>
            <span v-if="expanded" class="esc-hint"><kbd>Esc</kbd> to exit</span>
            <Button
              :icon="expanded ? 'pi pi-window-minimize' : 'pi pi-window-maximize'"
              :label="expanded ? 'Exit fullscreen' : 'Fullscreen'"
              text
              size="small"
              severity="secondary"
              class="fullscreen-button"
              :title="expanded ? 'Back to the page (Esc)' : 'Give the matrix the whole window'"
              @click="toggleExpanded"
            />
          </div>
        </div>

        <MatrixTable
          :mode="mode"
          :rows="visibleRows"
          :run-columns="matrix.run_columns"
          :model-columns="matrix.model_columns"
          :column-tallies="visibleTallies"
          :can-write="auth.canWrite"
          :fill="expanded"
          @rating-change="handleRatingChange"
          @peek-pinned="peekPinned = $event"
        />
      </section>
    </template>
  </div>
</template>

<style scoped>
.view-toggle {
  margin-top: 0.75rem;
}

/* Pulls the archived-runs note up against the header above; font/color come
 * from the shared .hint class. */
.hint-text {
  margin: -0.5rem 0 0;
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

/* Same shrink-to-fit as the checkbox column: the bubble takes its own width
 * and the tallies keep the rest. */
.picker :deep(.comment-column) {
  width: 1%;
  text-align: center;
}

.comment-bubble {
  color: var(--p-text-muted-color);
  cursor: help;
}

.comment-bubble:hover {
  color: var(--p-text-color);
}

.comment-none {
  color: var(--p-text-muted-color);
  opacity: 0.5;
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

.matrix-heading-actions {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 0.75rem;
}

.uncovered-note {
  font-size: 0.75rem;
  color: var(--p-text-muted-color);
}

/* The `.filter-row` pairing every other page uses, inline in the heading
 * strip — same label, same gap, no row of its own. */
.matrix-filter {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

/* Quiet until wanted: it sits above every screenful of the matrix, so it
 * reads as a caption with a label rather than a button competing with the
 * column headers below it. */
.fullscreen-button {
  color: var(--p-text-muted-color);
  font-size: 0.75rem;
}

.fullscreen-button:hover {
  color: var(--p-text-color);
}

/* --- fullscreen -----------------------------------------------------------
 *
 * `position: fixed` against the viewport, not the content column: `.app-content`
 * sets `overflow: auto`, which does not make a containing block for a fixed
 * descendant (only a transform/filter/contain would), so the overlay clears
 * the topbar and the side nav for free.
 *
 * z-index 15 sits above the shell's sticky topbar (2) and far below PrimeVue's
 * overlay layer (~1100+), which is what keeps the peek popover and the pinned
 * dialog usable on top of the maximised matrix — the whole reason for
 * maximising it is reading answers against the rubric. */
.matrix-section-expanded {
  position: fixed;
  inset: 0;
  z-index: 15;
  padding: 0.875rem 1.25rem 1.25rem;
  gap: 0.625rem;
  background: var(--p-content-background);
  animation: matrix-expand 120ms ease-out;
}

/* From the table's own edge outwards, so the expansion reads as the matrix
 * growing into the window rather than a panel appearing over it. */
@keyframes matrix-expand {
  from {
    opacity: 0;
    transform: scale(0.99);
  }
}

@media (prefers-reduced-motion: reduce) {
  .matrix-section-expanded {
    animation: none;
  }
}

.esc-hint {
  font-size: 0.75rem;
  color: var(--p-text-muted-color);
}

.esc-hint kbd {
  font-family: inherit;
  font-size: 0.6875rem;
  padding: 0.0625rem 0.3125rem;
  border: 1px solid var(--p-content-border-color);
  border-radius: var(--p-border-radius-sm, 4px);
  background: var(--p-content-hover-background);
}
</style>

<style>
/* The overlay hides the content column but does not stop it scrolling, so a
 * wheel event past the end of the table would move a page nobody can see.
 * Unscoped because the element is <body>, which no scoped selector reaches. */
body.results-fullscreen {
  overflow: hidden;
}

/* The tooltip is appended to <body>, which no scoped selector reaches — hence
 * the `class` option on the directive and this rule. `pre-line` because a run
 * comment is a textarea's worth of free text: the newlines whoever wrote it
 * put in are part of what it says, and PrimeVue's default collapses them. */
.p-tooltip.comment-tooltip {
  max-width: 32rem;
}

.p-tooltip.comment-tooltip .p-tooltip-text {
  white-space: pre-line;
  font-size: 0.8125rem;
  max-width: 32rem;
}
</style>
