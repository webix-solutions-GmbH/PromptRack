<script setup lang="ts">
// The brand mark, in whichever ink the current theme needs. Four surfaces
// render it — the topbar and the three auth screens outside the app shell.
//
// Two files, not one recoloured with a CSS filter: the strokes are near-black
// and the accent bar is green, and every filter that lifts the strokes to
// white also drags the green off-brand. `logo-dark.png` is the same drawing
// with the strokes already inverted and the green untouched.
import { computed } from 'vue'
import { useThemeStore } from '../stores/theme'

const props = withDefaults(defineProps<{ size?: number; alt?: string }>(), {
  size: 32,
  alt: '',
})

const theme = useThemeStore()
const src = computed(() => (theme.resolved === 'dark' ? '/logo-dark.png' : '/logo.png'))
</script>

<template>
  <img class="brand-mark" :src="src" :width="size" :height="size" :alt="alt" decoding="async" />
</template>

<style scoped>
.brand-mark {
  display: block;
  /* The artwork is taller than it is wide inside a square, transparent box.
     Constraining both axes keeps the box square at every call site, so the
     mark's own optical weight is what varies with `size`, not its footprint. */
  object-fit: contain;
  flex: none;
}
</style>
