<script setup lang="ts">
// Editor for a follow-up test case's earlier conversation: the user/assistant
// turns sent between the system message and the case's own user message.
//
// The shape is fixed by what the server accepts (`validate_history`): starts
// with `user`, alternates strictly, ends with `assistant` — because the case's
// own user message is the next turn. So the unit of editing is a **pair**, not
// a turn: adding appends a user turn and the assistant's reply together, and
// removing drops both. Roles are positional and never chosen, which makes an
// ill-formed history impossible to build here rather than refused on save.
//
// Empty is the common case and renders as one quiet button. Self-contained:
// `modelValue` in, a fresh array out on every edit.
import { computed } from 'vue'
import Button from 'primevue/button'
import Textarea from 'primevue/textarea'
import { MAX_HISTORY_TURNS, type HistoryTurn } from '../api/testCases'
import { formatChars } from '../lib/format'

const props = withDefaults(
  defineProps<{
    modelValue: HistoryTurn[]
    /** Prefix for the textareas' ids, so the labels point at their own fields. */
    idPrefix?: string
    readonly?: boolean
  }>(),
  { idPrefix: 'history-turn', readonly: false },
)

const emit = defineEmits<{ 'update:modelValue': [HistoryTurn[]] }>()

/** Room for one more pair under the server's turn ceiling. */
const canAdd = computed(() => !props.readonly && props.modelValue.length + 2 <= MAX_HISTORY_TURNS)

function roleAt(index: number): HistoryTurn['role'] {
  return index % 2 === 0 ? 'user' : 'assistant'
}

/** Turns grouped two by two, each pair starting at an even index. A stored
 * odd-length list (which the server would never hand out) still renders —
 * its last pair simply has no assistant row until one is typed. */
const pairs = computed(() => {
  const grouped: { start: number; turns: { index: number; turn: HistoryTurn }[] }[] = []
  props.modelValue.forEach((turn, index) => {
    if (index % 2 === 0) grouped.push({ start: index, turns: [] })
    grouped[grouped.length - 1].turns.push({ index, turn })
  })
  return grouped
})

function fieldId(index: number): string {
  return `${props.idPrefix}-${index}`
}

/** Roles are re-derived from position on every emit, so whatever arrived, what
 * leaves is always well-formed alternation. */
function normalized(turns: HistoryTurn[]): HistoryTurn[] {
  return turns.map((turn, index) => ({ role: roleAt(index), content: turn.content }))
}

function setContent(index: number, value: string | undefined) {
  const next = props.modelValue.map((turn) => ({ ...turn }))
  next[index] = { role: roleAt(index), content: value ?? '' }
  emit('update:modelValue', normalized(next))
}

function addPair() {
  emit(
    'update:modelValue',
    normalized([
      ...props.modelValue,
      { role: 'user', content: '' },
      { role: 'assistant', content: '' },
    ]),
  )
}

function removePair(start: number) {
  emit(
    'update:modelValue',
    normalized(props.modelValue.filter((_, index) => index !== start && index !== start + 1)),
  )
}
</script>

<template>
  <div class="conversation">
    <template v-if="modelValue.length === 0">
      <div v-if="!readonly">
        <Button
          type="button"
          label="Add earlier conversation"
          icon="pi pi-comments"
          size="small"
          outlined
          severity="secondary"
          @click="addPair"
        />
      </div>
      <p v-else class="hint">(no earlier conversation)</p>
    </template>

    <template v-else>
      <div v-for="(pair, pairIndex) in pairs" :key="pair.start" class="pair">
        <div class="pair-header">
          <span class="pair-label">Exchange {{ pairIndex + 1 }}</span>
          <Button
            v-if="!readonly"
            type="button"
            icon="pi pi-times"
            text
            size="small"
            severity="danger"
            :aria-label="`Remove exchange ${pairIndex + 1}`"
            @click="removePair(pair.start)"
          />
        </div>
        <div
          v-for="{ index, turn } in pair.turns"
          :key="index"
          class="turn field"
          :class="`turn-${roleAt(index)}`"
        >
          <label :for="fieldId(index)">{{ roleAt(index) === 'user' ? 'User' : 'Assistant' }}</label>
          <!-- Fixed height, no auto-resize: an earlier answer can be long, and
               a field that grows to fit would push the content field below it
               off screen. It scrolls; the user can still drag it taller. -->
          <Textarea
            :id="fieldId(index)"
            :model-value="turn.content"
            rows="4"
            :readonly="readonly"
            class="turn-input"
            @update:model-value="(value: string | undefined) => setContent(index, value)"
          />
          <p class="hint">{{ formatChars(turn.content.length) }}</p>
        </div>
      </div>

      <div v-if="canAdd">
        <Button
          type="button"
          label="Add an exchange"
          icon="pi pi-plus"
          size="small"
          outlined
          @click="addPair"
        />
      </div>
      <p v-else-if="!readonly" class="hint">
        {{ MAX_HISTORY_TURNS }} turns is the most a conversation can hold.
      </p>
    </template>
  </div>
</template>

<style scoped>
.conversation {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.pair {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  padding: 0.5rem 0.625rem;
  border: 1px dashed var(--p-content-border-color);
  border-radius: var(--p-content-border-radius);
}

.pair-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-height: 1.5rem;
}

.pair-label {
  font-size: 0.6875rem;
  text-transform: uppercase;
  letter-spacing: 0.03em;
  color: var(--p-text-muted-color);
}

/* The assistant's side sits a step in, so the two voices read apart at a
   glance without a colour of their own — the part colours are reserved for
   the system/task/content channels. */
.turn-assistant {
  padding-left: 0.75rem;
  border-left: 2px solid var(--p-content-border-color);
}

/* PrimeVue's Textarea *is* the <textarea>, so the class lands on it directly. */
.turn-input {
  font-family: var(--p-font-family-mono, ui-monospace, monospace);
  resize: vertical;
}
</style>
