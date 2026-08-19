<script setup lang="ts">
// The body of one matrix cell: full response, tool-call summary, provenance and
// the same rating widget the run detail page uses — a matrix cell is a
// `run_results` row like any other, so rating it here writes through the
// identical `PATCH /api/results/{id}` endpoint. The matrix itself only carries
// `tool_call_names` (`app/services/compare.py` never freights the full
// transcript into a cell), so "transcript" here is a turn/tool-call summary,
// not a message-by-message replay.
//
// Rendered inline inside the `<td>` by `MatrixTable`, which mounts it only for
// a cell that exists — hence a non-nullable `cell` and no `visible` prop.
// Written for a dialog originally, it used to restate the column, the group,
// the drift note and the metrics; inline in a matrix those are the row header's
// and the column header's job, and across four columns the reader saw each of
// them four times. What is left is only what differs *per cell*.
import { computed } from 'vue'
import Tag from 'primevue/tag'
import RatingButtons from '../runs/RatingButtons.vue'
import { usePromptVersionLabels } from '../../lib/promptVersionLabels'
import { resolveThinking } from '../../lib/thinking'
import type { Rating } from '../../lib/rating'
import type { CompareCellView } from '../../api/results'

const props = defineProps<{
  cell: CompareCellView
  /** True when this slot's frozen text is *not* the same across the row, so
   * the row header cannot honestly show one copy of it and each cell carries
   * its own instead. Set per slot: a row can agree on the system prompt and
   * disagree on the task prompt. */
  showSystemPrompt: boolean
  showTaskPrompt: boolean
  canWrite: boolean
}>()

const emit = defineEmits<{
  ratingChange: [patch: { rating?: Rating | null; ratingNote?: string | null }]
}>()

const response = computed(() =>
  resolveThinking(props.cell.reasoning_text, props.cell.response_text),
)
const isToolCell = computed(() => props.cell.tool_mode !== 'none')
const readonly = computed(
  () => !props.canWrite || props.cell.status === 'pending' || props.cell.status === 'running',
)

// Only the ids of the slots actually rendered here — the shared ones are the
// row header's to resolve, and both go through the same module-level cache.
const { versionLabel } = usePromptVersionLabels(() => [
  props.showSystemPrompt ? props.cell.system_prompt_version_id : null,
  props.showTaskPrompt ? props.cell.task_prompt_version_id : null,
])

const systemVersionLabel = computed(() =>
  versionLabel(props.cell.system_prompt_text, props.cell.system_prompt_version_id),
)
const taskVersionLabel = computed(() =>
  versionLabel(props.cell.task_prompt_text, props.cell.task_prompt_version_id),
)
</script>

<template>
  <div class="cell-body">
    <RatingButtons
      :result-id="cell.id"
      :rating="cell.rating"
      :rating-note="cell.rating_note"
      :readonly="readonly"
      @change="(patch) => emit('ratingChange', patch)"
    />

    <!--
      The two prompt slots apart, never the assembled message: keeping them
      separate is what lets a drift note say *the task prompt changed* rather
      than *the user message changed*. A slot only appears down here when it
      differs across the row — the row header carries it otherwise, and the
      test case's own text is always the row's.
    -->
    <details v-if="showSystemPrompt" class="prompt-details prompt-details-system">
      <summary>
        System prompt
        <Tag v-if="systemVersionLabel" severity="secondary" :value="systemVersionLabel" />
      </summary>
      <pre class="pre">{{ cell.system_prompt_text ?? '(no system message)' }}</pre>
    </details>
    <details v-if="showTaskPrompt" class="prompt-details prompt-details-task">
      <summary>
        Task prompt
        <Tag v-if="taskVersionLabel" severity="secondary" :value="taskVersionLabel" />
      </summary>
      <pre class="pre">{{ cell.task_prompt_text ?? '(none)' }}</pre>
    </details>

    <div v-if="cell.error" class="error-block">{{ cell.error }}</div>
    <div v-else class="field">
      <span class="field-label">Response</span>
      <details v-if="response.thinking !== null">
        <summary class="think-summary">Thinking{{ response.thinkingClosed ? '' : '…' }}</summary>
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

  </div>
</template>

<style scoped>
/* Flex columns for stacking and gaps only: the cell is content-sized
   (`MatrixTable`, top-of-file comment), so nothing here distributes height
   and the answer below renders in full. */
.cell-body {
  display: flex;
  flex-direction: column;
  gap: 0.875rem;
}

.mono {
  font-family: var(--p-font-family-mono, ui-monospace, monospace);
  font-size: 0.8125rem;
}

/* No `display: flex` here, unlike the row header's summaries: that drops the
   native disclosure triangle in WebKit/Blink, and this one has no chevron of
   its own to put in its place. */
.prompt-details summary {
  cursor: pointer;
  font-size: 0.75rem;
  font-weight: 500;
  color: var(--p-text-muted-color);
}

.prompt-details .pre {
  margin-top: 0.5rem;
}

/* Same ink logic as the matrix peeks and the editor's assembled preview
   (tokens in `src/style.css`): the frozen text itself carries its channel's
   color, so a cell's own prompt copy reads the same as everywhere else. */
.prompt-details-system .pre {
  color: var(--pr-system-text);
}

.prompt-details-task .pre {
  color: var(--pr-task-text);
}

.field {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  min-width: 0;
}

.field-label {
  display: flex;
  align-items: center;
  gap: 0.375rem;
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

/* No scrollport, no height cap: the answer is the thing this matrix exists
   to show, so it renders whole and the row grows to fit it. The `.pre` cap
   above stays, deliberately — thinking traces and per-cell prompt copies are
   disclosures, opened to check, not the payload. */
.answer-text {
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

</style>
