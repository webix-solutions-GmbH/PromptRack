<script setup lang="ts">
// A result's manual verdict — good/meh/bad plus an optional note. Combines
// the old app's two components (`rating-badge.tsx`, read-only display; and
// `result-rating.tsx`, the interactive toggle buttons) into one, since a
// viewer and a writer both need to see the same three states, just with or
// without the buttons to change them.
//
// A row that has not finished (`pending`/`running`) is refused by the
// backend (`PATCH /api/results/{id}`, "rate it once it has finished") — the
// parent is expected to pass `readonly` for those rows rather than this
// component discovering the 409 on click.
import { ref, watch } from 'vue'
import Button from 'primevue/button'
import InputText from 'primevue/inputtext'
import Tag from 'primevue/tag'
import { resultsApi } from '../../api/runs'
import { RATING_META, RATINGS, type Rating } from '../../lib/rating'

const props = defineProps<{
  resultId: number
  rating: Rating | null
  ratingNote: string | null
  readonly?: boolean
}>()

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
    <span v-if="ratingNote" class="note-text">{{ ratingNote }}</span>
  </div>

  <div v-else class="rating-widget">
    <div class="thumbs">
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
      <Button
        :label="showNote ? 'Hide note' : ratingNote ? 'Edit note' : 'Add note'"
        text
        size="small"
        @click="showNote = !showNote"
      />
      <span v-if="error" class="error-text">{{ error }}</span>
    </div>
    <InputText
      v-if="showNote"
      v-model="noteValue"
      placeholder="Optional note about this rating…"
      class="note-input"
      @blur="saveNote"
    />
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
  flex-direction: column;
  gap: 0.375rem;
}

.thumbs {
  display: flex;
  align-items: center;
  gap: 0.375rem;
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

.note-input {
  max-width: 24rem;
  font-size: 0.8125rem;
}
</style>
