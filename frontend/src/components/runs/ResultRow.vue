<script setup lang="ts">
// One test case's result inside a run: its frozen inputs, its outcome, its
// manual verdict. The run's **three** frozen texts show apart — the system
// prompt, the task prompt and the case's own content — each prompt with its
// own attribution badge (spec §"Attribution surfaced"). Reading the three
// columns and never the transcript is deliberate: `transcript_json` records the
// *assembled* messages, which cannot say which half came from where.
import { computed } from 'vue'
import Tag from 'primevue/tag'
import { formatDuration, formatRate, formatTokenLabel } from '../../lib/format'
import { RESULT_STATUS_SEVERITY } from '../../lib/runStatus'
import { resolveThinking } from '../../lib/thinking'
import type { Rating } from '../../lib/rating'
import type { RunResultView } from '../../api/runs'
import RatingButtons from './RatingButtons.vue'
import TranscriptView from './TranscriptView.vue'

const props = defineProps<{
  result: RunResultView
  index: number
  /** A viewer still sees the verdict, just not the buttons to change it. */
  canWrite: boolean
  /** `"v4"` when that slot's draft matched a committed version at run
   * creation, `"uncommitted"` when it did not, `null` when the slot was empty. One
   * per slot: the two prompts are versioned independently. */
  systemVersionLabel: string | null
  taskVersionLabel: string | null
}>()

const emit = defineEmits<{
  ratingChange: [patch: { rating?: Rating | null; ratingNote?: string | null }]
}>()

const isToolRun = computed(() => props.result.tool_mode !== 'none')
const hasTranscript = computed(
  () => isToolRun.value && (props.result.transcript?.length ?? 0) > 0,
)
const hasMetrics = computed(
  () =>
    props.result.duration_ms !== null ||
    props.result.ttft_ms !== null ||
    props.result.completion_tokens !== null ||
    props.result.tokens_per_sec !== null,
)
const tokenLabel = computed(() =>
  formatTokenLabel(props.result.prompt_tokens, props.result.completion_tokens, props.result.tokens_estimated),
)

const ratingResponse = computed(() =>
  resolveThinking(props.result.reasoning_text, props.result.response_text),
)

/** Shown only when the model thought: for everything else it repeats `ttft_ms`. */
const contentTtft = computed(() =>
  props.result.ttft_content_ms !== null && props.result.ttft_content_ms !== props.result.ttft_ms
    ? props.result.ttft_content_ms
    : null,
)
</script>

<template>
  <article class="result-card">
    <header class="result-header">
      <div class="result-heading">
        <span class="result-index">{{ index }}. {{ result.group_name }}</span>
        <h3 class="result-title">{{ result.test_case_title }}</h3>
      </div>
      <div class="result-badges">
        <Tag v-if="isToolRun" severity="info" :value="`tools: ${result.tool_mode}`" />
        <Tag v-if="systemVersionLabel" severity="secondary" :value="`system ${systemVersionLabel}`" />
        <Tag v-if="taskVersionLabel" severity="secondary" :value="`task ${taskVersionLabel}`" />
        <Tag :severity="RESULT_STATUS_SEVERITY[result.status] ?? 'secondary'" :value="result.status" />
      </div>
    </header>

    <RatingButtons
      :result-id="result.id"
      :rating="result.rating"
      :rating-note="result.rating_note"
      :rated-via="result.rated_via"
      :readonly="!canWrite || result.status === 'pending' || result.status === 'running'"
      @change="emit('ratingChange', $event)"
    />

    <details class="prompt-details">
      <summary>Prompts &amp; test case{{ isToolRun ? ' & tools' : '' }}</summary>
      <div class="prompt-body">
        <div class="prompt-field">
          <span class="field-label">
            System prompt
            <Tag v-if="systemVersionLabel" severity="secondary" :value="systemVersionLabel" />
          </span>
          <pre class="pre">{{ result.system_prompt_text ?? '(no system message)' }}</pre>
        </div>
        <div class="prompt-field">
          <span class="field-label">
            Task prompt
            <Tag v-if="taskVersionLabel" severity="secondary" :value="taskVersionLabel" />
          </span>
          <pre class="pre">{{ result.task_prompt_text ?? '(none)' }}</pre>
        </div>
        <div class="prompt-field">
          <span class="field-label">Content</span>
          <pre class="pre">{{ result.test_case_text ?? '(none)' }}</pre>
        </div>
        <div v-if="isToolRun" class="prompt-field">
          <span class="field-label">
            Tools offered ({{ result.tools_snapshot?.length ?? 0 }}) · tool_choice
            {{ result.tool_choice ?? 'server default' }} · max {{ result.max_turns }} turns
          </span>
          <pre class="pre">{{
            !result.tools_snapshot || result.tools_snapshot.length === 0
              ? '(none)'
              : JSON.stringify(
                  result.tools_snapshot.map((entry) => entry.definition),
                  null,
                  2,
                )
          }}</pre>
        </div>
      </div>
    </details>

    <div v-if="result.error" class="error-block">{{ result.error }}</div>

    <!--
      A tool run's answer only makes sense as a conversation, so the
      transcript replaces the single response block and expected output moves
      underneath it rather than beside it.
    -->
    <div v-if="hasTranscript" class="conversation-block">
      <div class="prompt-field">
        <span class="field-label">Conversation</span>
        <TranscriptView
          :transcript="result.transcript ?? []"
          :turns="result.turns ?? []"
          :stopped-reason="result.stopped_reason"
        />
      </div>
      <div v-if="result.expected_output" class="prompt-field">
        <span class="field-label">Expected output</span>
        <pre class="pre">{{ result.expected_output }}</pre>
      </div>
    </div>
    <div v-else-if="result.expected_output" class="response-grid">
      <div class="prompt-field">
        <span class="field-label">Response</span>
        <template v-if="result.response_text !== null">
          <details v-if="ratingResponse.thinking !== null">
            <summary class="think-summary">
              Thinking{{ ratingResponse.thinkingClosed ? '' : '…' }}
            </summary>
            <pre class="pre italic">{{ ratingResponse.thinking }}</pre>
          </details>
          <p v-if="ratingResponse.answer" class="answer-text">{{ ratingResponse.answer }}</p>
          <pre v-else class="pre">{{
            result.status === 'running' ? '…' : ratingResponse.thinking !== null ? '(empty answer)' : '—'
          }}</pre>
        </template>
        <pre v-else class="pre">{{ result.status === 'running' ? '…' : '—' }}</pre>
      </div>
      <div class="prompt-field">
        <span class="field-label">Expected output</span>
        <pre class="pre">{{ result.expected_output }}</pre>
      </div>
    </div>
    <div
      v-else-if="result.response_text || result.reasoning_text || result.status === 'running'"
      class="prompt-field"
    >
      <span class="field-label">Response</span>
      <details v-if="ratingResponse.thinking !== null">
        <summary class="think-summary">
          Thinking{{ ratingResponse.thinkingClosed ? '' : '…' }}
        </summary>
        <pre class="pre italic">{{ ratingResponse.thinking }}</pre>
      </details>
      <p v-if="ratingResponse.answer" class="answer-text">{{ ratingResponse.answer }}</p>
      <pre v-else class="pre">{{
        result.status === 'running' ? '…' : ratingResponse.thinking !== null ? '(empty answer)' : '—'
      }}</pre>
    </div>

    <div v-if="hasMetrics" class="metrics-row">
      <span class="chip">duration <b>{{ formatDuration(result.duration_ms) }}</b></span>
      <span class="chip">ttft <b>{{ formatDuration(result.ttft_ms) }}</b></span>
      <span v-if="contentTtft !== null" class="chip"
        >first answer token <b>{{ formatDuration(contentTtft) }}</b></span
      >
      <span v-if="tokenLabel" class="chip">tokens <b>{{ tokenLabel }}</b></span>
      <span v-if="result.reasoning_tokens !== null" class="chip"
        >thinking <b>{{ result.reasoning_tokens }} tok</b></span
      >
      <span class="chip">speed <b>{{ formatRate(result.tokens_per_sec) }}</b></span>
      <span v-if="result.turn_count !== null" class="chip">turns <b>{{ result.turn_count }}</b></span>
      <span v-if="result.tool_call_count !== null" class="chip"
        >tool calls <b>{{ result.tool_call_count }}</b></span
      >
    </div>
  </article>
</template>

<style scoped>
.result-card {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
  padding: 1.25rem;
  border: 1px solid var(--p-content-border-color);
  border-radius: var(--p-content-border-radius);
}

.result-header {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
}

.result-heading {
  display: flex;
  flex-direction: column;
  gap: 0.125rem;
  min-width: 0;
}

.result-index {
  font-size: 0.75rem;
  text-transform: uppercase;
  letter-spacing: 0.03em;
  color: var(--p-text-muted-color);
}

.result-title {
  margin: 0;
  font-size: 0.9375rem;
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.result-badges {
  display: flex;
  align-items: center;
  gap: 0.375rem;
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

.prompt-field {
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
  max-height: 24rem;
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
  max-height: 24rem;
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
  font-size: 0.75rem;
  color: var(--p-red-600, var(--p-red-500));
}

.conversation-block {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.response-grid {
  display: grid;
  grid-template-columns: 1fr;
  gap: 0.75rem;
}

@media (min-width: 64rem) {
  .response-grid {
    grid-template-columns: 1fr 1fr;
  }
}

.metrics-row {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
}
</style>
