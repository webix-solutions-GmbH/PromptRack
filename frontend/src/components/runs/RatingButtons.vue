<script setup lang="ts">
// A result's manual verdict — good/meh/bad plus an optional note. One
// component covers both read-only display and the interactive toggle
// buttons, since a viewer and a writer both need to see the same three
// states, just with or without the buttons to change them.
//
// The note is a rationale, not a label: an agent judging results over MCP
// writes several sentences about *why* a row is meh, and a rationale nobody
// can read is one nobody writes twice. So it is always on screen when it
// exists — in both modes, as a block under the thumbs — and editing it means
// a real textarea on its own line rather than a slot beside the buttons.
// Three lines is the ceiling it renders at, with "more"/"less" for the rest;
// see the template comment for why the ceiling exists at all.
//
// A row that has not finished (`pending`/`running`) is refused by the
// backend (`PATCH /api/results/{id}`, "rate it once it has finished") — the
// parent is expected to pass `readonly` for those rows rather than this
// component discovering the 409 on click.
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import Button from 'primevue/button'
import Textarea from 'primevue/textarea'
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
/** The textarea's state, not the note's: the note itself is always shown. */
const editing = ref(false)
const expanded = ref(false)
const overflowing = ref(false)
const noteEl = ref<HTMLElement | null>(null)
const editorEl = ref<{ $el: HTMLTextAreaElement } | null>(null)
const saving = ref(false)
const error = ref<string | null>(null)

// "Is there a fourth line" is a question only the clamped box can answer: a
// character count guesses at the column's width and the reader's font, and
// guesses wrong often enough to hang a "more" link under a one-line note.
// Never measured while expanded — the clamp is off then, so the two heights
// are equal by definition and the answer would delete the "less" link that
// got the reader here.
async function measureOverflow() {
  if (expanded.value) return
  await nextTick()
  const el = noteEl.value
  overflowing.value = el !== null && el.scrollHeight > el.clientHeight
}

onMounted(measureOverflow)

// A different text is a fresh question of whether it fits, and one left
// expanded would otherwise unfold the next note unasked — in a matrix cell
// that is a row that silently grew.
watch(
  () => props.ratingNote,
  () => {
    expanded.value = false
    void measureOverflow()
  },
)

// Closing the editor re-mounts the clamped block: a new element, unmeasured.
// Opening it has to move focus by hand: every other `autofocus` in this app
// sits inside a PrimeVue `Dialog`, which runs a focus trap that honours the
// attribute — an inline textarea toggled by `v-if` gets none, and Esc and
// Cmd/Ctrl+Enter below are keystrokes with nowhere to land until the field
// holds focus. The caret goes to the end, because the common act here is
// appending to a rationale, not overtyping one.
watch(editing, (open) => {
  void measureOverflow()
  if (!open) return
  void nextTick(() => {
    const el = editorEl.value?.$el
    if (!el) return
    el.focus()
    el.setSelectionRange(el.value.length, el.value.length)
  })
})

watch(
  () => props.resultId,
  () => {
    noteValue.value = props.ratingNote ?? ''
    editing.value = false
    expanded.value = false
    void measureOverflow()
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

function onNoteBlur() {
  // Clicking away is a way of being finished: it saves *and* closes, so the
  // note goes back to being the readable block it is the rest of the time
  // rather than leaving an open field behind on a page full of them.
  //
  // Esc and Cmd/Ctrl+Enter both close the editor too, and closing it can fire
  // this very blur — after Esc has already reverted `noteValue`, which a save
  // here would write straight back to the server as if it were an edit. So the
  // guard is the editor's own state: a blur arriving with the editor already
  // shut is the tail of a close that has decided what to do, and only a blur
  // with it still open is a click away from a live edit. Reading `editing`
  // rather than a "just cancelled" flag keeps it stateless — a flag would be
  // left set whenever the browser skips the blur of a removed element, waiting
  // to swallow the next honest save instead.
  if (!editing.value) return
  saveAndClose()
}

function cancelNote() {
  noteValue.value = props.ratingNote ?? ''
  editing.value = false
}

function saveAndClose() {
  // Closed before saved, so the blur the close may fire is a no-op (above) and
  // this stays the single write.
  editing.value = false
  void saveNote()
}


// One handler rather than two `@keydown` bindings, so the two shortcuts and
// their ordering against the blur above read in one place.
function onNoteKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') {
    cancelNote()
    return
  }
  if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) {
    // Otherwise the newline lands in the field on its way out.
    event.preventDefault()
    saveAndClose()
  }
}
</script>

<template>
  <div class="rating" :class="readonly ? 'rating-readonly' : 'rating-widget'">
    <div class="rating-row">
      <Tag v-if="readonly && rating" :severity="RATING_META[rating].severity">
        {{ RATING_META[rating].emoji }} {{ RATING_META[rating].label }}
      </Tag>
      <template v-if="!readonly">
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
      </template>
      <!-- Beside the thumbs, not in the card header: it qualifies the verdict,
           and clicking any thumb here replaces it with a human one. -->
      <Tag v-if="judged" severity="secondary" value="judge" :title="JUDGE_TITLE" />
      <!-- Only while the editor is shut, and not as a toggle: clicking away
           already saves and closes, so a pen still on screen would receive its
           click *after* that blur had closed the editor and would read it as a
           request to open one — the field would blink shut and straight back
           open. Default (primary) severity when a note exists, secondary
           otherwise: not "is there a note here" (the note itself answers that
           now) but which act the pen is offering, adding one or editing the
           one below. -->
      <Button
        v-if="!readonly && !editing"
        icon="pi pi-pencil"
        text
        rounded
        size="small"
        :severity="ratingNote ? undefined : 'secondary'"
        :title="ratingNote ? 'Edit note' : 'Add note'"
        :aria-label="ratingNote ? 'Edit note' : 'Add note'"
        @click="editing = true"
      />
    </div>

    <!--
      The note lives under the thumb row and is clamped there, in both modes.
      Under, because an agent's rationale runs to a paragraph and no strip
      beside a row of buttons can hold one — and clamped, because the same
      widget renders inside a `/results` matrix cell, which is content-sized
      (`MatrixTable`, top-of-file comment): an unclamped paragraph there would
      set the height of every row it lands in. Three lines carries the gist of
      a verdict; the rest is one click away, and only offered when there
      actually is a rest.
    -->
    <Textarea
      v-if="editing"
      ref="editorEl"
      v-model="noteValue"
      class="note-editor"
      rows="3"
      auto-resize
      placeholder="Optional note about this rating…"
      @blur="onNoteBlur"
      @keydown="onNoteKeydown"
    />
    <template v-else-if="ratingNote">
      <p ref="noteEl" class="note-text" :class="{ expanded }">{{ ratingNote }}</p>
      <button
        v-if="overflowing"
        type="button"
        class="note-toggle"
        :aria-expanded="expanded"
        @click="expanded = !expanded"
      >
        {{ expanded ? 'less' : 'more' }}
      </button>
    </template>

    <span v-if="error" class="error-text">{{ error }}</span>
  </div>
</template>

<style scoped>
/* A column, so the note can grow without the verdict moving. `min-width: 0`
   because the widget sits in flex parents (a run card, a matrix cell) whose
   default floor is the content's own width. */
.rating {
  display: flex;
  flex-direction: column;
  gap: 0.375rem;
  min-width: 0;
}

.rating-row {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  flex-wrap: wrap;
}

/* The thumbs sit tighter than the tags do — they read as one control. */
.rating-widget .rating-row {
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

/* The `-webkit-box` clamp is still the only one every browser here honours;
   `line-clamp` rides along for the day that changes. `pre-wrap` keeps an
   agent's own paragraph breaks, and breaking anywhere keeps a note carrying a
   path or a JSON fragment from widening a matrix column that has already
   decided how wide it is. */
.note-text {
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 3;
  line-clamp: 3;
  overflow: hidden;
  margin: 0;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font-size: 0.8125rem;
  color: var(--p-text-muted-color);
}

/* `block` drops the clamp outright rather than raising it, which is also what
   makes the measurement above skippable while expanded. */
.note-text.expanded {
  display: block;
  overflow: visible;
}

/* A link, not a Button: it belongs to the paragraph above it, and a rounded
   PrimeVue button would read as a fourth control beside the thumbs. */
.note-toggle {
  align-self: flex-start;
  padding: 0;
  border: none;
  background: none;
  font-size: 0.75rem;
  color: var(--p-primary-color);
  cursor: pointer;
}

/* Full width of whatever it is dropped into — a run card is wide, a matrix
   cell is not, and the editor should be as wide as the note it replaces. */
.note-editor {
  width: 100%;
  font-size: 0.8125rem;
}

.error-text {
  font-size: 0.75rem;
  color: var(--p-red-500);
}
</style>
