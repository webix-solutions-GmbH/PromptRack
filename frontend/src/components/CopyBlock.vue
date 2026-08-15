<script setup lang="ts">
// A `<pre><code>` block with a copy button, shared by the three client-setup
// snippets on McpView — extracted so they cannot drift into three slightly
// different copy affordances. Feedback is a transient check icon rather than
// a toast: three of these can sit on one page, and a toast per click would
// stack up.
import { ref } from 'vue'
import Button from 'primevue/button'

defineProps<{ code: string }>()

const copied = ref(false)
let resetTimer: ReturnType<typeof setTimeout> | undefined

async function copy(text: string) {
  await navigator.clipboard.writeText(text)
  copied.value = true
  clearTimeout(resetTimer)
  resetTimer = setTimeout(() => {
    copied.value = false
  }, 2000)
}
</script>

<template>
  <div class="copy-block">
    <pre><code>{{ code }}</code></pre>
    <Button
      :icon="copied ? 'pi pi-check' : 'pi pi-copy'"
      text
      size="small"
      class="copy-button"
      :aria-label="copied ? 'Copied' : 'Copy to clipboard'"
      :title="copied ? 'Copied' : 'Copy to clipboard'"
      @click="copy(code)"
    />
  </div>
</template>

<style scoped>
.copy-block {
  position: relative;
}

.copy-block pre {
  margin: 0;
  padding: 0.75rem 2.5rem 0.75rem 0.875rem;
  background: var(--p-content-hover-background);
  border: 1px solid var(--p-content-border-color);
  border-radius: var(--p-content-border-radius);
  overflow-x: auto;
  white-space: pre;
}

.copy-block code {
  font-size: 0.8125rem;
}

.copy-button {
  position: absolute;
  top: 0.375rem;
  right: 0.375rem;
}
</style>
