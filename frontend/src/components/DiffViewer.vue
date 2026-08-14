<script setup lang="ts">
// Renders a unified diff exactly as the backend's `difflib.unified_diff`
// produces it — no Monaco dependency, per the plan. Deliberately dumb: this
// is a line classifier plus CSS, not a diff engine (the backend owns the
// diffing, this owns only turning `---`/`+++`/`@@`/`+`/`-` prefixes into
// legible colour).
import { computed } from 'vue'

const props = defineProps<{ diff: string[] }>()

type DiffLineKind = 'file-header' | 'hunk' | 'add' | 'remove' | 'context'

interface DiffLine {
  kind: DiffLineKind
  text: string
}

function classify(line: string): DiffLineKind {
  if (line.startsWith('--- ') || line.startsWith('+++ ')) return 'file-header'
  if (line.startsWith('@@')) return 'hunk'
  if (line.startsWith('+')) return 'add'
  if (line.startsWith('-')) return 'remove'
  return 'context'
}

// One entry per diff line, already newline-free — see the endpoint's
// `diff: list[str]`.
const lines = computed<DiffLine[]>(() =>
  props.diff.map((text) => ({ kind: classify(text), text })),
)
</script>

<template>
  <div class="diff-viewer">
    <p v-if="lines.length === 0" class="empty">No differences.</p>
    <div v-else class="diff-body">
      <div
        v-for="(line, index) in lines"
        :key="index"
        class="diff-line"
        :class="`diff-${line.kind}`"
      >{{ line.text.length ? line.text : ' ' }}</div>
    </div>
  </div>
</template>

<style scoped>
.diff-viewer {
  border: 1px solid var(--p-content-border-color);
  border-radius: var(--p-content-border-radius);
  overflow: auto;
  max-height: 28rem;
}

.empty {
  padding: 1rem;
  margin: 0;
  color: var(--p-text-muted-color);
  font-size: 0.875rem;
}

.diff-body {
  padding: 0.375rem 0;
  font-family: var(--p-font-family-mono, ui-monospace, monospace);
  font-size: 0.8125rem;
  line-height: 1.5;
}

.diff-line {
  padding: 0 0.75rem;
  white-space: pre-wrap;
  word-break: break-word;
}

.diff-add {
  background: color-mix(in srgb, var(--p-green-500, #22c55e) 16%, transparent);
  color: var(--p-green-700, #15803d);
}

.diff-remove {
  background: color-mix(in srgb, var(--p-red-500, #ef4444) 16%, transparent);
  color: var(--p-red-700, #b91c1c);
}

.diff-hunk {
  color: var(--p-text-muted-color);
  font-weight: 600;
}

.diff-file-header {
  color: var(--p-text-muted-color);
  font-style: italic;
}

.diff-context {
  color: var(--p-text-color);
}
</style>
