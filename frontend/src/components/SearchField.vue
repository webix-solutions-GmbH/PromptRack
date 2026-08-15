<script setup lang="ts">
// The list views' search box. A magnifier on the left, and a clear affordance
// on the right that only exists once there is something to clear — an always-
// visible × on an empty field is a control that does nothing. It is an
// `InputIcon`, not a Button, so it sits inside the field: `role`/`tabindex`
// and the Enter handler are what give it back the keyboard behaviour a Button
// would have brought.
//
// Extracted so /prompts, /test-cases and /runs cannot drift into three
// slightly different search boxes.
import IconField from 'primevue/iconfield'
import InputIcon from 'primevue/inputicon'
import InputText from 'primevue/inputtext'

defineProps<{
  modelValue: string
  placeholder: string
}>()

const emit = defineEmits<{
  'update:modelValue': [value: string]
}>()
</script>

<template>
  <IconField class="name-filter">
    <InputIcon class="pi pi-search" />
    <InputText
      :model-value="modelValue"
      :placeholder="placeholder"
      size="small"
      @update:model-value="(value: string | undefined) => emit('update:modelValue', value ?? '')"
    />
    <InputIcon
      v-if="modelValue !== ''"
      class="pi pi-times clear-search"
      role="button"
      tabindex="0"
      aria-label="Clear search"
      @click="emit('update:modelValue', '')"
      @keydown.enter="emit('update:modelValue', '')"
    />
  </IconField>
</template>

<style scoped>
.name-filter :deep(input) {
  width: 16rem;
}

.clear-search {
  cursor: pointer;
}
</style>
