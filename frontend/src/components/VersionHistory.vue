<script setup lang="ts">
// The prompt editor's history panel: every immutable commit, newest first,
// with the two pointers that hang off them (`deployed`, `baseline: run #N`)
// and the four per-version actions the spec calls out — view, diff, deploy,
// restore.
//
// Deploy/restore are gated on `canWrite` (content is member-writable, same
// line as the rest of a prompt's own fields); view/diff cost nothing to a
// reader and stay open to everyone who can see the panel at all.
import Button from 'primevue/button'
import Column from 'primevue/column'
import DataTable from 'primevue/datatable'
import Tag from 'primevue/tag'
import type { PromptVersion } from '../api/prompts'
import { formatDateTime } from '../lib/format'

const props = defineProps<{
  versions: PromptVersion[]
  deployedVersionId: number | null
  canWrite: boolean
  /** The version currently mid-action (deploy/restore in flight), so only
   * its own row shows a spinner rather than the whole table. */
  busyVersionId?: number | null
}>()

const emit = defineEmits<{
  view: [version: PromptVersion]
  diff: [version: PromptVersion]
  deploy: [version: PromptVersion]
  restore: [version: PromptVersion]
}>()

function isDeployed(version: PromptVersion): boolean {
  return version.id === props.deployedVersionId
}

function isBusy(version: PromptVersion): boolean {
  return props.busyVersionId === version.id
}
</script>

<template>
  <DataTable :value="versions" data-key="id" class="table">
    <template #empty>No commits yet — save the draft and commit to start the history.</template>
    <Column header="Version">
      <template #body="{ data }: { data: PromptVersion }">
        <div class="version-cell">
          <span class="version-number">v{{ data.version }}</span>
          <Tag v-if="isDeployed(data)" value="deployed" severity="success" />
          <Tag v-if="data.baseline_run_id" :value="`baseline: run #${data.baseline_run_id}`" severity="info" />
        </div>
      </template>
    </Column>
    <Column field="message" header="Message" />
    <Column header="Author">
      <!-- No user-lookup endpoint exists yet to resolve a name (see
           api/prompts.ts) — the bare id is what the backend model carries. -->
      <template #body="{ data }: { data: PromptVersion }">{{
        data.created_by !== null ? `user #${data.created_by}` : '—'
      }}</template>
    </Column>
    <Column header="Date">
      <template #body="{ data }: { data: PromptVersion }">{{ formatDateTime(data.created_at) }}</template>
    </Column>
    <Column header="" class="actions-column">
      <template #body="{ data }: { data: PromptVersion }">
        <div class="row-actions">
          <Button label="View" text size="small" @click="emit('view', data)" />
          <Button label="Diff" text size="small" @click="emit('diff', data)" />
          <Button
            v-if="canWrite"
            label="Deploy"
            text
            size="small"
            :disabled="isDeployed(data)"
            :loading="isBusy(data)"
            @click="emit('deploy', data)"
          />
          <Button
            v-if="canWrite"
            label="Restore"
            text
            size="small"
            :loading="isBusy(data)"
            @click="emit('restore', data)"
          />
        </div>
      </template>
    </Column>
  </DataTable>
</template>

<style scoped>
.version-cell {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.version-number {
  font-family: var(--p-font-family-mono, ui-monospace, monospace);
  font-weight: 600;
}

.actions-column {
  width: 1%;
  white-space: nowrap;
}

.row-actions {
  display: flex;
  gap: 0.25rem;
  justify-content: flex-end;
}
</style>
