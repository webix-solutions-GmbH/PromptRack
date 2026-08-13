<script setup lang="ts">
// The comparison matrix itself: test cases as rows, one column per selected
// run (run mode) or model (model mode). Port of the old app's
// `compare-row.tsx` table body, with the per-cell expand/collapse replaced by
// a click-to-open `CellDetail` dialog (this task's contract) — a cell here is
// only ever a compact preview.
//
// Column headers differ by pivot (run mode names the run and its own
// good/meh/bad; model mode uses `columnTallies`, computed over the cells
// actually on screen rather than the whole run — see
// `backend/app/api/results.py`'s `_tallies`), so this component takes both
// column shapes and picks per `mode` rather than making the caller normalize
// them into one.
import { computed } from 'vue'
import { RouterLink } from 'vue-router'
import Tag from 'primevue/tag'
import { formatDateTime, formatRate } from '../../lib/format'
import { splitThinking } from '../../lib/thinking'
import { RATING_META } from '../../lib/rating'
import type {
  ColumnTally,
  CompareCellView,
  CompareMode,
  CompareRowView,
  CompareRunView,
  ModelColumnView,
} from '../../api/results'

const props = defineProps<{
  mode: CompareMode
  rows: CompareRowView[]
  runColumns: CompareRunView[]
  modelColumns: ModelColumnView[]
  columnTallies: ColumnTally[]
}>()

const emit = defineEmits<{
  cellClick: [payload: { cell: CompareCellView; row: CompareRowView }]
}>()

/** Characters of a response shown before a cell's preview is clamped. */
const CLAMP = 180

interface ColumnHeader {
  key: string
  modelId: string
  machineName: string
  subtitle: string
  good: number
  meh: number
  bad: number
  avgRate: number | null
  runId: number | null
}

const columnHeaders = computed<ColumnHeader[]>(() => {
  if (props.mode === 'runs') {
    return props.runColumns.map((run) => ({
      key: String(run.id),
      modelId: run.model_id,
      machineName: run.machine_name,
      subtitle: formatDateTime(run.created_at),
      good: run.good,
      meh: run.meh,
      bad: run.bad,
      avgRate: run.avg_rate,
      runId: run.id,
    }))
  }
  return props.modelColumns.map((column, index) => {
    const tally = props.columnTallies[index]
    return {
      key: column.key,
      modelId: column.model_id,
      machineName: column.machine_name,
      subtitle: `${tally?.answered ?? 0}/${props.rows.length} answered · latest ${formatDateTime(column.latest_run_at)}`,
      good: tally?.good ?? 0,
      meh: tally?.meh ?? 0,
      bad: tally?.bad ?? 0,
      avgRate: tally?.avg_rate ?? null,
      runId: null,
    }
  })
})

function driftLabel(drift: string[]): string {
  if (drift.length === 1 && drift[0] === 'test case edited since') return drift[0]
  return `differs across cells: ${drift.join(', ')}`
}

function preview(cell: CompareCellView): { text: string; clamped: boolean } {
  const { answer } = splitThinking(cell.response_text ?? '')
  const clamped = answer.length > CLAMP
  return { text: clamped ? `${answer.slice(0, CLAMP)}…` : answer, clamped }
}
</script>

<template>
  <div class="matrix-wrap">
    <table class="matrix">
      <thead>
        <tr>
          <th class="row-header-cell">Test case</th>
          <th v-for="column in columnHeaders" :key="column.key" class="col-header-cell">
            <div class="col-header">
              <span class="mono model-id">{{ column.modelId }}</span>
              <span class="machine-name">@ {{ column.machineName }}</span>
              <span class="col-sub">
                <RouterLink v-if="column.runId !== null" :to="`/runs/${column.runId}`"
                  >run #{{ column.runId }}</RouterLink
                >
                <span v-else>{{ column.subtitle }}</span>
              </span>
              <span class="col-sub rating-tallies">
                <span class="good-count">{{ column.good }}</span
                >/<span class="meh-count">{{ column.meh }}</span
                >/<span class="bad-count">{{ column.bad }}</span>
                <span class="rate">{{ formatRate(column.avgRate) }}</span>
              </span>
            </div>
          </th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="row.key">
          <th scope="row" class="row-header-cell">
            <div class="row-header">
              <span class="row-group">
                {{ row.group_name }}<template v-if="row.test_case_id === null"> · deleted test case</template>
              </span>
              <span class="row-title">{{ row.test_case_title }}</span>
              <details class="row-details">
                <summary>Test case</summary>
                <p class="row-text">{{ row.test_case_text }}</p>
              </details>
              <p v-if="row.drift.length > 0" class="drift-note">{{ driftLabel(row.drift) }}</p>
            </div>
          </th>
          <td v-for="(cell, index) in row.cells" :key="index" class="cell">
            <span v-if="cell === null" class="empty-cell">—</span>
            <button
              v-else
              type="button"
              class="cell-button"
              @click="emit('cellClick', { cell, row })"
            >
              <span class="cell-top">
                <Tag v-if="cell.rating" :severity="RATING_META[cell.rating].severity">
                  {{ RATING_META[cell.rating].emoji }}
                </Tag>
                <span v-if="cell.status !== 'ok'" class="cell-status">{{ cell.status }}</span>
                <span v-if="cell.turn_count !== null" class="cell-status"
                  >{{ cell.turn_count }}t / {{ cell.tool_call_count ?? 0 }} calls</span
                >
              </span>
              <span v-if="cell.error" class="cell-error">{{ cell.error }}</span>
              <span v-else class="cell-text">{{ preview(cell).text || '(no response)' }}</span>
              <span class="cell-rate">{{ formatRate(cell.tokens_per_sec) }}</span>
            </button>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<style scoped>
.matrix-wrap {
  overflow-x: auto;
  border: 1px solid var(--p-content-border-color);
  border-radius: var(--p-content-border-radius);
}

.matrix {
  width: 100%;
  min-width: max-content;
  border-collapse: collapse;
  text-align: left;
  font-size: 0.8125rem;
}

thead {
  background: var(--p-content-hover-background);
}

.row-header-cell {
  position: sticky;
  left: 0;
  z-index: 1;
  width: 16rem;
  min-width: 16rem;
  max-width: 16rem;
  border-right: 1px solid var(--p-content-border-color);
  border-bottom: 1px solid var(--p-content-border-color);
  background: var(--p-content-background);
  padding: 0.75rem 1rem;
  vertical-align: top;
  font-weight: 400;
}

thead .row-header-cell {
  background: var(--p-content-hover-background);
  font-size: 0.6875rem;
  text-transform: uppercase;
  letter-spacing: 0.03em;
  color: var(--p-text-muted-color);
}

.col-header-cell {
  width: 22rem;
  min-width: 20rem;
  max-width: 26rem;
  border-left: 1px solid var(--p-content-border-color);
  border-bottom: 1px solid var(--p-content-border-color);
  padding: 0.75rem 1rem;
  vertical-align: top;
}

.col-header {
  display: flex;
  flex-direction: column;
  gap: 0.125rem;
}

.mono {
  font-family: var(--p-font-family-mono, ui-monospace, monospace);
}

.model-id {
  font-size: 0.75rem;
  font-weight: 600;
}

.machine-name {
  font-size: 0.75rem;
  color: var(--p-text-muted-color);
}

.col-sub {
  font-size: 0.6875rem;
  color: var(--p-text-muted-color);
}

.rating-tallies {
  display: flex;
  align-items: center;
  gap: 0.25rem;
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

.rate {
  margin-left: 0.25rem;
}

.row-header {
  display: flex;
  flex-direction: column;
  gap: 0.1875rem;
}

.row-group {
  font-size: 0.6875rem;
  text-transform: uppercase;
  letter-spacing: 0.03em;
  color: var(--p-text-muted-color);
}

.row-title {
  font-size: 0.8125rem;
  font-weight: 600;
  color: var(--p-text-color);
}

.row-details summary {
  cursor: pointer;
  font-size: 0.6875rem;
  color: var(--p-text-muted-color);
}

.row-text {
  margin: 0.25rem 0 0;
  white-space: pre-wrap;
  word-break: break-word;
  font-family: var(--p-font-family-mono, ui-monospace, monospace);
  font-size: 0.6875rem;
  color: var(--p-text-muted-color);
}

.drift-note {
  margin: 0;
  padding: 0.25rem 0.5rem;
  border: 1px solid var(--p-yellow-300, var(--p-yellow-500));
  border-radius: var(--p-content-border-radius);
  background: var(--p-yellow-50, transparent);
  color: var(--p-yellow-700, var(--p-yellow-600));
  font-size: 0.6875rem;
}

.cell {
  border-left: 1px solid var(--p-content-border-color);
  border-bottom: 1px solid var(--p-content-border-color);
  padding: 0;
  vertical-align: top;
}

.empty-cell {
  display: block;
  padding: 0.75rem 1rem;
  color: var(--p-text-muted-color);
}

.cell-button {
  display: flex;
  flex-direction: column;
  gap: 0.375rem;
  width: 100%;
  height: 100%;
  border: none;
  background: transparent;
  padding: 0.75rem 1rem;
  text-align: left;
  font: inherit;
  color: inherit;
  cursor: pointer;
}

.cell-button:hover {
  background: var(--p-content-hover-background);
}

.cell-top {
  display: flex;
  align-items: center;
  gap: 0.375rem;
  flex-wrap: wrap;
}

.cell-status {
  font-size: 0.6875rem;
  font-weight: 500;
  color: var(--p-text-muted-color);
}

.cell-text {
  white-space: pre-wrap;
  word-break: break-word;
  color: var(--p-text-color);
}

.cell-error {
  color: var(--p-red-600, var(--p-red-500));
  word-break: break-word;
}

.cell-rate {
  font-size: 0.6875rem;
  color: var(--p-text-muted-color);
}
</style>
