<script setup lang="ts">
// The commit message dialog: a deliberate, git-style freeze of the current
// draft into an immutable version. Message is required — an empty commit
// message is refused client-side the same way the backend refuses a
// no-changes commit, both being "this history entry needs to mean something."
import { ref, watch } from 'vue'
import Button from 'primevue/button'
import Dialog from 'primevue/dialog'
import Message from 'primevue/message'
import Textarea from 'primevue/textarea'

const props = defineProps<{
  visible: boolean
  saving?: boolean
  error?: string | null
}>()

const emit = defineEmits<{
  'update:visible': [value: boolean]
  commit: [message: string]
}>()

const message = ref('')

// Cleared on every open so a previous commit's text never lingers into the
// next one.
watch(
  () => props.visible,
  (visible) => {
    if (visible) message.value = ''
  },
)

function submit() {
  const trimmed = message.value.trim()
  if (!trimmed) return
  emit('commit', trimmed)
}
</script>

<template>
  <Dialog
    :visible="visible"
    modal
    header="Commit draft"
    class="form-dialog"
    @update:visible="(value) => emit('update:visible', value)"
  >
    <form class="dialog-form" @submit.prevent="submit">
      <p class="hint">Freezes the current draft as the next immutable version.</p>
      <div class="field">
        <label for="commit-message">Commit message *</label>
        <Textarea
          id="commit-message"
          v-model="message"
          rows="3"
          auto-resize
          autofocus
          required
          placeholder="What changed and why"
        />
      </div>
      <Message v-if="error" severity="error" :closable="false">{{ error }}</Message>
      <div class="dialog-actions">
        <Button type="button" label="Cancel" text @click="emit('update:visible', false)" />
        <Button type="submit" label="Commit" :loading="saving" :disabled="!message.trim()" />
      </div>
    </form>
  </Dialog>
</template>
