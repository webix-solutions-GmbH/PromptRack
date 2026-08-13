<script setup lang="ts">
// One matrix cell, expanded: full response, tool-call summary, drift notes
// and the same rating widget the run detail page uses — a matrix cell is a
// `run_results` row like any other, so rating it here writes through the
// identical `PATCH /api/results/{id}` endpoint. Port of the old app's
// `compare-row.tsx` `Cell`, moved from an inline expand/collapse into a
// dialog per this task's contract ("cell dialog with full response +
// transcript + drift notes") — the matrix itself only carries `tool_call_names`
// (`app/services/compare.py` never freights the full transcript into a cell),
// so "transcript" here is the same turn/tool-call summary the old compare
// page showed, not a message-by-message replay.
import { computed } from 'vue'
import { RouterLink } from 'vue-router'
import Dialog from 'primevue/dialog'
import Tag from 'primevue/tag'
import RatingButtons from '../runs/RatingButtons.vue'
import { formatDateTime, formatDuration, formatRate } from '../../lib/format'
import { splitThinking } from '../../lib/thinking'
import type { Rating } from '../../lib/rating'
import type { CompareCellView } from '../../api/results'

const props = defineProps<{
  visible: boolean
  cell: CompareCellView | null
  rowGroup: string
  rowTitle: string
  /** Conditions that differ across the row this cell belongs to. */
  drift: string[]
  /** "model_id @ machine" — the column this cell came from. */
  columnLabel: string
  canWrite: boolean
}>()

const emit = defineEmits<{
  'update:visible': [value: boolean]
  ratingChange: [patch: { rating?: Rating | null; ratingNote?: string | null }]
}>()

const statusSeverity: Record<string, 'secondary' | 'info' | 'success' | 'danger'> = {
  pending: 'secondary',
  running: 'info',
  ok: 'success',
  error: 'danger',
}

const response = computed(() => splitThinking(props.cell?.response_text ?? ''))
const isToolCell = computed(() => props.cell !== null && props.cell.tool_mode !== 'none')
const tokenLabel = computed(() => {
  const cell = props.cell
  if (!cell || cell.completion_tokens === null) return null
  return `${cell.tokens_estimated ? '~' : ''}${cell.completion_tokens} tok`
})
const readonly = computed(
  () =>
    !props.canWrite ||
    props.cell === null ||
    props.cell.status === 'pending' ||
    props.cell.status === 'running',
)
</script>

<template>
  <Dialog
    :visible="visible"
    modal
    :header="rowTitle"
    class="cell-dialog"
    @update:visible="(value) => emit('update:visible', value)"
  >
    <div v-if="cell" class="cell-body">
      <div class="cell-meta">
        <span class="mono">{{ columnLabel }}</span>
        <Tag :severity="statusSeverity[cell.status] ?? 'secondary'" :value="cell.status" />
        <span class="group-name">{{ rowGroup }}</span>
      </div>

      <p v-if="drift.length > 0" class="drift-note">
        {{ drift.length === 1 && drift[0] === 'test case edited since' ? drift[0] : `differs across cells: ${drift.join(', ')}` }}
      </p>

      <RatingButtons
        :result-id="cell.id"
        :rating="cell.rating"
        :rating-note="cell.rating_note"
        :readonly="readonly"
        @change="(patch) => emit('ratingChange', patch)"
      />

      <details class="prompt-details">
        <summary>Test case &amp; effective prompt</summary>
        <div class="prompt-body">
          <div class="field">
            <span class="field-label">User message</span>
            <pre class="pre">{{ cell.test_case_text }}</pre>
          </div>
          <div class="field">
            <span class="field-label">Effective system prompt</span>
            <pre class="pre">{{ cell.effective_prompt_text ?? '(no system message)' }}</pre>
          </div>
        </div>
      </details>

      <div v-if="cell.error" class="error-block">{{ cell.error }}</div>
      <div v-else class="field">
        <span class="field-label">Response</span>
        <details v-if="response.thinking !== null">
          <summary class="think-summary">
            Thinking{{ response.thinkingClosed ? '' : '…' }}
          </summary>
          <pre class="pre italic">{{ response.thinking }}</pre>
        </details>
        <p v-if="response.answer" class="answer-text">{{ response.answer }}</p>
        <pre v-else class="pre">{{ response.thinking !== null ? '(empty answer)' : '—' }}</pre>
      </div>

      <div v-if="isToolCell && cell.turn_count !== null" class="tool-block">
        <span class="tool-summary">
          {{ cell.turn_count }} turn{{ cell.turn_count === 1 ? '' : 's' }} ·
          {{ cell.tool_call_count ?? 0 }} tool call{{ cell.tool_call_count === 1 ? '' : 's' }}
        </span>
        <span v-if="cell.tool_call_names.length > 0" class="mono tool-calls">
          {{ cell.tool_call_names.join(' → ') }}
        </span>
      </div>

      <div class="chip-row">
        <span class="chip">speed <b>{{ formatRate(cell.tokens_per_sec) }}</b></span>
        <span class="chip">duration <b>{{ formatDuration(cell.duration_ms) }}</b></span>
        <span class="chip">ttft <b>{{ formatDuration(cell.ttft_ms) }}</b></span>
        <span v-if="tokenLabel" class="chip">tokens <b>{{ tokenLabel }}</b></span>
      </div>

      <div class="provenance">
        <RouterLink :to="`/runs/${cell.run_id}`">run #{{ cell.run_id }}</RouterLink>
        <span>·</span>
        <span>{{ formatDateTime(cell.run_created_at) }}</span>
        <span v-if="cell.superseded" class="superseded">
          newer attempt ({{ cell.superseded.status }}) in run #{{ cell.superseded.run_id }} skipped
        </span>
      </div>
    </div>
  </Dialog>
</template>

<style scoped>
.cell-dialog {
  width: 40rem;
  max-width: 92vw;
}

.cell-body {
  display: flex;
  flex-direction: column;
  gap: 0.875rem;
}

.cell-meta {
  display: flex;
  align-items: center;
  gap: 0.625rem;
  flex-wrap: wrap;
}

.mono {
  font-family: var(--p-font-family-mono, ui-monospace, monospace);
  font-size: 0.8125rem;
}

.group-name {
  font-size: 0.75rem;
  color: var(--p-text-muted-color);
}

.drift-note {
  margin: 0;
  padding: 0.5rem 0.625rem;
  border: 1px solid var(--p-yellow-300, var(--p-yellow-500));
  border-radius: var(--p-content-border-radius);
  background: var(--p-yellow-50, transparent);
  color: var(--p-yellow-700, var(--p-yellow-600));
  font-size: 0.75rem;
}

.prompt-details summary {
  cursor: pointer;
  font-size: 0.75rem;
  font-weight: 500;
  color: var(--p-text-muted-color);
}

.prompt-body {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
  margin-top: 0.5rem;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  min-width: 0;
}

.field-label {
  font-size: 0.75rem;
  font-weight: 500;
  color: var(--p-text-muted-color);
}

.pre {
  max-height: 16rem;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-word;
  border: 1px solid var(--p-content-border-color);
  border-radius: var(--p-content-border-radius);
  background: var(--p-content-background);
  padding: 0.625rem;
  font-family: var(--p-font-family-mono, ui-monospace, monospace);
  font-size: 0.75rem;
  margin: 0;
}

.pre.italic {
  font-style: italic;
}

.think-summary {
  cursor: pointer;
  font-size: 0.75rem;
  font-weight: 500;
  color: var(--p-text-muted-color);
}

.answer-text {
  max-height: 20rem;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-word;
  border: 1px solid var(--p-content-border-color);
  border-radius: var(--p-content-border-radius);
  padding: 0.625rem;
  font-size: 0.8125rem;
  margin: 0;
}

.error-block {
  border: 1px solid var(--p-red-300, var(--p-red-500));
  border-radius: var(--p-content-border-radius);
  background: var(--p-red-50, transparent);
  padding: 0.625rem;
  font-size: 0.8125rem;
  color: var(--p-red-600, var(--p-red-500));
}

.tool-block {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  border: 1px solid var(--p-primary-200, var(--p-primary-color));
  border-radius: var(--p-content-border-radius);
  background: var(--p-highlight-background);
  padding: 0.5rem 0.625rem;
}

.tool-summary {
  font-size: 0.75rem;
  font-weight: 500;
}

.tool-calls {
  font-size: 0.75rem;
  word-break: break-word;
}

.chip-row {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
}

.chip {
  display: inline-flex;
  align-items: center;
  gap: 0.25rem;
  border: 1px solid var(--p-content-border-color);
  border-radius: var(--p-content-border-radius);
  padding: 0.1rem 0.45rem;
  font-size: 0.75rem;
  color: var(--p-text-muted-color);
}

.chip b {
  font-family: var(--p-font-family-mono, ui-monospace, monospace);
  font-weight: 500;
  color: var(--p-text-color);
}

.provenance {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.375rem;
  font-size: 0.75rem;
  color: var(--p-text-muted-color);
  border-top: 1px solid var(--p-content-border-color);
  padding-top: 0.625rem;
}

.superseded {
  color: var(--p-orange-600, var(--p-orange-500));
}
</style>
