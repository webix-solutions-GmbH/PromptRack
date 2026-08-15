<script setup lang="ts">
// A result's manual verdict — good/meh/bad plus an optional note. One
// component covers both read-only display and the interactive toggle
// buttons, since a viewer and a writer both need to see the same three
// states, just with or without the buttons to change them.
//
// A row that has not finished (`pending`/`running`) is refused by the
// backend (`PATCH /api/results/{id}`, "rate it once it has finished") — the
// parent is expected to pass `readonly` for those rows rather than this
// component discovering the 409 on click.
import { computed, ref, watch } from 'vue'
import Button from 'primevue/button'
import InputText from 'primevue/inputtext'
import Tag from 'primevue/tag'
import { resultsApi } from '../../api/runs'
import { RATING_META, RATINGS, type Rating } from '../../lib/rating'

const props = defineProps<{
  resultId: number
  rating: Rating | null
  ratingNote: string | null
  /** How the stored verdict was set. `'token'` means an agent judged it over
   * MCP, which is worth saying out loud next to the rating; rating from here
   * always overwrites it with `'session'`, so the badge answers "has a human
   * looked at this yet". */
  ratedVia?: 'session' | 'token' | null
  readonly?: boolean
}>()

const judged = computed(() => props.ratedVia === 'token')

const JUDGE_TITLE = 'Rated by an agent over MCP, not by a person. Rate it here to take it over.'

const emit = defineEmits<{
  change: [patch: { rating?: Rating | null; ratingNote?: string | null }]
}>()

const noteValue = ref(props.ratingNote ?? '')
const showNote = ref((props.ratingNote ?? '').length > 0)
const saving = ref(false)
const error = ref<string | null>(null)

watch(
  () => props.resultId,
  () => {
    noteValue.value = props.ratingNote ?? ''
    showNote.value = (props.ratingNote ?? '').length > 0
  },
)

async function setRating(next: Rating) {
  // Clicking the active rating clears it, so a mis-click is one click to undo.
  const value = props.rating === next ? null : next
  error.value = null
  emit('change', { rating: value })
  saving.value = true
  try {
    await resultsApi.rate(props.resultId, { rating: value ?? 'unrated' })
  } catch {
    error.value = 'Failed to save rating.'
  } finally {
    saving.value = false
  }
}

async function saveNote() {
  const trimmed = noteValue.value.trim()
  error.value = null
  emit('change', { ratingNote: trimmed.length > 0 ? trimmed : null })
  saving.value = true
  try {
    await resultsApi.rate(props.resultId, { note: trimmed })
  } catch {
    error.value = 'Failed to save note.'
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <div v-if="readonly" class="rating-readonly">
    <Tag v-if="rating" :severity="RATING_META[rating].severity">
      {{ RATING_META[rating].emoji }} {{ RATING_META[rating].label }}
    </Tag>
    <Tag v-if="judged" severity="secondary" value="judge" :title="JUDGE_TITLE" />
    <span v-if="ratingNote" class="note-text">{{ ratingNote }}</span>
  </div>

  <!-- One line, growing rightwards: the note input opens beside the pen
       instead of below the thumbs, so toggling it never pushes the response
       down. -->
  <div v-else class="rating-widget">
    <button
      v-for="value in RATINGS"
      :key="value"
      type="button"
      class="thumb"
      :class="{ active: rating === value }"
      :title="RATING_META[value].description"
      :aria-pressed="rating === value"
      :disabled="saving"
      @click="setRating(value)"
    >
      {{ RATING_META[value].emoji }}
    </button>
    <!-- Beside the thumbs, not in the card header: it qualifies the verdict,
         and clicking any thumb here replaces it with a human one. -->
    <Tag v-if="judged" severity="secondary" value="judge" :title="JUDGE_TITLE" />
    <!-- Default (primary) severity when a note exists, secondary otherwise:
         the pen itself answers "is there a note here" at a glance. -->
    <Button
      icon="pi pi-pencil"
      text
      rounded
      size="small"
      :severity="ratingNote ? undefined : 'secondary'"
      :title="showNote ? 'Hide note' : ratingNote ? 'Edit note' : 'Add note'"
      :aria-label="showNote ? 'Hide note' : ratingNote ? 'Edit note' : 'Add note'"
      :aria-expanded="showNote"
      @click="showNote = !showNote"
    />
    <InputText
      v-if="showNote"
      v-model="noteValue"
      placeholder="Optional note about this rating…"
      class="note-input"
      @blur="saveNote"
    />
    <span v-if="error" class="error-text">{{ error }}</span>
  </div>
</template>

<style scoped>
.rating-readonly {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  flex-wrap: wrap;
}

.note-text {
  font-size: 0.8125rem;
  color: var(--p-text-muted-color);
}

.rating-widget {
  display: flex;
  align-items: center;
  gap: 0.375rem;
  flex-wrap: wrap;
}

.thumb {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 1.75rem;
  height: 1.75rem;
  border-radius: var(--p-content-border-radius);
  border: 1px solid var(--p-content-border-color);
  background: transparent;
  font-size: 0.9rem;
  cursor: pointer;
}

.thumb:disabled {
  opacity: 0.5;
  cursor: default;
}

.thumb.active {
  border-color: var(--p-primary-color);
  background: var(--p-highlight-background);
}

.error-text {
  font-size: 0.75rem;
  color: var(--p-red-500);
}

/* Grows into whatever the line has left, up to a cap — a note is a remark,
   not an essay — and can shrink below its content so a narrow cell wraps it
   to the next line instead of overflowing. */
.note-input {
  flex: 1 1 8rem;
  min-width: 8rem;
  max-width: 24rem;
  font-size: 0.8125rem;
}

</style>
