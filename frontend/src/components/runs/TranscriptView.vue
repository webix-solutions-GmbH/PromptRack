<script setup lang="ts">
// The conversation a tool run actually had: assistant turns, the calls they
// asked for, what each tool returned, and per-turn metrics. Port of
// `git show legacy-nextjs:src/components/runs/tool-transcript.tsx`.
//
// Rendered from the stored/streamed transcript rather than re-derived, so it
// shows what happened even after the toolset behind it has been edited or
// deleted. `tool_calls` entries are the backend's flat `{id, name,
// arguments}` (`TranscriptMessage.to_json()` in
// `backend/app/services/tool_loop.py`) rather than the old app's OpenAI-wire
// `{id, function: {name, arguments}}` nesting.
import { computed } from 'vue'
import { formatDuration, formatRate, computeTokensPerSec, formatTokenLabel } from '../../lib/format'
import { splitThinking } from '../../lib/thinking'
import type { StoppedReason, TranscriptMessage, TurnMetrics } from '../../api/runs'

const props = defineProps<{
  transcript: TranscriptMessage[]
  turns: TurnMetrics[]
  stoppedReason: StoppedReason | null
}>()

const STOPPED_LABELS: Record<StoppedReason, string> = {
  stop: 'the model answered',
  definitions_only: 'definitions only — nothing was executed',
  max_turns: 'turn budget exhausted, still asking for tools',
}

/** Attempts to pretty-print a tool call's JSON arguments; falls back to the
 * raw string when they are not valid JSON (a model can send malformed
 * arguments — that is itself part of what a run measures). */
function formatArguments(raw: string): string {
  try {
    return JSON.stringify(JSON.parse(raw), null, 2)
  } catch {
    return raw
  }
}

interface Row {
  message: TranscriptMessage
  turn: number
  showHeader: boolean
}

// The system and user messages are already shown in the result card's
// prompt block, so only assistant/tool messages belong in the conversation.
// Turn headers are decided in one pass up front — a "have I already shown
// this turn?" counter cannot be threaded through the template itself.
const rows = computed<Row[]>(() => {
  const conversation = props.transcript.filter(
    (message) => message.role === 'assistant' || message.role === 'tool',
  )
  let lastHeaderTurn = -1
  return conversation.map((message) => {
    const turn = message.turn ?? 0
    const showHeader = message.role === 'assistant' && lastHeaderTurn !== turn
    if (showHeader) lastHeaderTurn = turn
    return { message, turn, showHeader }
  })
})

const turnByIndex = computed(() => new Map(props.turns.map((turn) => [turn.index, turn])))

function assistantParts(message: TranscriptMessage) {
  return splitThinking(message.content)
}
</script>

<template>
  <div v-if="rows.length === 0" class="transcript-empty">—</div>
  <div v-else class="transcript">
    <div v-for="(row, index) in rows" :key="index" class="transcript-row">
      <div v-if="row.showHeader" class="turn-header">
        <span class="turn-label">Turn {{ row.turn + 1 }}</span>
        <template v-if="turnByIndex.get(row.turn)">
          <span class="chip">ttft <b>{{ formatDuration(turnByIndex.get(row.turn)!.ttft_ms) }}</b></span>
          <span class="chip">duration <b>{{ formatDuration(turnByIndex.get(row.turn)!.duration_ms) }}</b></span>
          <span class="chip">
            tokens
            <b>{{
              formatTokenLabel(
                turnByIndex.get(row.turn)!.prompt_tokens,
                turnByIndex.get(row.turn)!.completion_tokens,
                turnByIndex.get(row.turn)!.tokens_estimated,
              )
            }}</b>
          </span>
          <span class="chip"
            >speed
            <b>{{
              formatRate(
                computeTokensPerSec(
                  turnByIndex.get(row.turn)!.completion_tokens,
                  turnByIndex.get(row.turn)!.duration_ms,
                  turnByIndex.get(row.turn)!.ttft_ms,
                ),
              )
            }}</b></span
          >
          <span v-if="turnByIndex.get(row.turn)!.finish_reason" class="chip"
            >finish <b>{{ turnByIndex.get(row.turn)!.finish_reason }}</b></span
          >
        </template>
      </div>

      <template v-if="row.message.role === 'assistant'">
        <details v-if="assistantParts(row.message).thinking !== null">
          <summary class="think-summary">
            Thinking{{ assistantParts(row.message).thinkingClosed ? '' : '…' }}
          </summary>
          <pre class="pre italic">{{ assistantParts(row.message).thinking }}</pre>
        </details>
        <div v-if="assistantParts(row.message).answer.length > 0" class="answer-block">
          <p class="answer-text">{{ assistantParts(row.message).answer }}</p>
        </div>
        <div
          v-for="call in row.message.tool_calls ?? []"
          :key="call.id"
          class="tool-call-block"
        >
          <span class="tool-call-label">→ calls <span class="mono">{{ call.name }}</span></span>
          <pre class="pre">{{ formatArguments(call.arguments) }}</pre>
        </div>
        <pre
          v-if="row.message.content.length === 0 && (row.message.tool_calls ?? []).length === 0"
          class="pre"
          >(empty turn)</pre
        >
      </template>

      <details v-else class="tool-result-block" :class="{ failed: row.message.tool_is_error === true }">
        <summary class="tool-result-summary">
          ← <span class="mono">{{ row.message.name ?? 'tool' }}</span> returned{{
            row.message.tool_is_error === true ? ' an error' : ''
          }}{{
            typeof row.message.tool_duration_ms === 'number'
              ? ` · ${formatDuration(row.message.tool_duration_ms)}`
              : ''
          }}
        </summary>
        <pre class="pre">{{ row.message.content.length > 0 ? row.message.content : '(empty)' }}</pre>
      </details>
    </div>

    <p v-if="stoppedReason !== null" class="stopped-note">
      Stopped: {{ STOPPED_LABELS[stoppedReason] }}
    </p>
  </div>
</template>

<style scoped>
.transcript-empty {
  font-size: 0.8125rem;
  color: var(--p-text-muted-color);
}

.transcript {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.transcript-row {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.turn-header {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.5rem;
  padding-top: 0.25rem;
}

.turn-label {
  font-size: 0.75rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--p-text-muted-color);
}

.think-summary {
  cursor: pointer;
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
  margin: 0.25rem 0 0;
}

.pre.italic {
  font-style: italic;
}

.answer-block {
  max-height: 24rem;
  overflow: auto;
  border: 1px solid var(--p-content-border-color);
  border-radius: var(--p-content-border-radius);
  padding: 0.625rem;
  font-size: 0.8125rem;
}

.answer-text {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
}

.tool-call-block {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  border: 1px solid var(--p-primary-200, var(--p-content-border-color));
  border-radius: var(--p-content-border-radius);
  background: var(--p-highlight-background);
  padding: 0.625rem;
}

.tool-call-label {
  font-size: 0.75rem;
  font-weight: 500;
}

.tool-result-block {
  border: 1px solid var(--p-green-200, var(--p-content-border-color));
  border-radius: var(--p-content-border-radius);
  background: var(--p-content-background);
  padding: 0.625rem;
}

.tool-result-block.failed {
  border-color: var(--p-red-300, var(--p-red-500));
}

.tool-result-summary {
  cursor: pointer;
  font-size: 0.75rem;
  font-weight: 500;
}

.stopped-note {
  font-size: 0.75rem;
  color: var(--p-text-muted-color);
  margin: 0;
}
</style>
