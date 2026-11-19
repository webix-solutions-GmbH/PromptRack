<script setup lang="ts">
// Editor for a manual tool's canned responses, chosen by call order: the Nth
// call of the tool within one result gets entry N, and the last entry repeats
// for every later call (`run_tool_loop`'s per-tool counter on the backend).
//
// The common case is one response, so zero or one entry renders as a single
// plain "Mock response" field with a quiet link to add a second — the list
// shape, "Call 1", "Call 2", only appears once there is a list to label. An
// empty array and `['']` look the same here on purpose; telling them apart is
// the caller's job when it builds the request (a sole blank entry means none).
//
// Self-contained so the test-case editor's per-tool override can reuse it:
// `modelValue` in, a fresh array out on every edit, nothing else assumed about
// the parent.
import { computed } from 'vue'
import Button from 'primevue/button'
import Textarea from 'primevue/textarea'
import { MAX_MOCK_RESPONSES } from '../api/toolsets'
import { formatChars } from '../lib/format'

const props = withDefaults(
  defineProps<{
    modelValue: string[]
    /** Prefix for the textareas' ids, so two editors on one page keep their
     * labels pointing at their own fields. */
    idPrefix?: string
    readonly?: boolean
  }>(),
  { idPrefix: 'mock-response', readonly: false },
)

const emit = defineEmits<{ 'update:modelValue': [string[]] }>()

/** What is rendered: always at least one field, so "no response yet" is an
 * empty textarea rather than nothing to type into. */
const entries = computed(() => (props.modelValue.length > 0 ? props.modelValue : ['']))
const isList = computed(() => entries.value.length > 1)
const canAdd = computed(() => !props.readonly && entries.value.length < MAX_MOCK_RESPONSES)

function fieldId(index: number): string {
  return `${props.idPrefix}-${index}`
}

function setEntry(index: number, value: string | undefined) {
  const next = [...entries.value]
  next[index] = value ?? ''
  emit('update:modelValue', next)
}

function addEntry() {
  emit('update:modelValue', [...entries.value, ''])
}

function removeEntry(index: number) {
  emit(
    'update:modelValue',
    entries.value.filter((_, candidate) => candidate !== index),
  )
}
</script>

<template>
  <div class="mock-responses">
    <div v-for="(entry, index) in entries" :key="index" class="field">
      <div class="entry-header">
        <label :for="fieldId(index)">{{ isList ? `Call ${index + 1}` : 'Mock response' }}</label>
        <Button
          v-if="isList && !readonly"
          icon="pi pi-times"
          text
          size="small"
          severity="danger"
          :aria-label="`Remove the response for call ${index + 1}`"
          @click="removeEntry(index)"
        />
      </div>
      <!-- A fixed height and no auto-resize: a JSON response can run to tens of
           thousands of characters, and a field that grows to fit would push the
           dialog's own buttons a screen away. It scrolls instead, and the user
           can still drag it taller. -->
      <Textarea
        :id="fieldId(index)"
        :model-value="entry"
        rows="12"
        :readonly="readonly"
        class="response-input"
        @update:model-value="(value: string | undefined) => setEntry(index, value)"
      />
      <p class="hint">{{ formatChars(entry.length) }}</p>
    </div>

    <p v-if="isList" class="hint">
      Call N gets response N; the last one repeats for every later call.
    </p>
    <div v-if="canAdd">
      <Button
        :label="isList ? 'Add a response' : 'Add a response for the next call'"
        :icon="isList ? 'pi pi-plus' : undefined"
        size="small"
        :link="!isList"
        :outlined="isList"
        class="add-button"
        @click="addEntry"
      />
    </div>
    <p v-else-if="!readonly" class="hint">
      {{ MAX_MOCK_RESPONSES }} responses is the most a tool can hold.
    </p>
  </div>
</template>

<style scoped>
.mock-responses {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.entry-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-height: 1.5rem;
}

/* PrimeVue's Textarea *is* the <textarea>, so the class lands on it directly. */
.response-input {
  font-family: var(--p-font-family-mono, ui-monospace, monospace);
  resize: vertical;
}

/* The link variant carries the button's own padding, which would indent it
 * from the field's left edge above it. */
.add-button.p-button-link {
  padding-left: 0;
}
</style>
