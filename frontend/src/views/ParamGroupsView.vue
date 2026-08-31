<script setup lang="ts">
// Parameter groups — named, reusable request-param presets ("no thinking",
// "temp 0"). Deliberately one level above endpoints and models: the same
// preset is selectable on a run against any box, merged between the
// endpoint's defaults and the run's own overrides. Content, not credentials,
// so every writer can manage them — same as prompts and test groups.
import { computed, onMounted, ref } from 'vue'
import type { DataTableRowClickEvent } from 'primevue/datatable'
import Button from 'primevue/button'
import Column from 'primevue/column'
import DataTable from 'primevue/datatable'
import Dialog from 'primevue/dialog'
import InputText from 'primevue/inputtext'
import Message from 'primevue/message'
import Select from 'primevue/select'
import Textarea from 'primevue/textarea'
import { useConfirm } from 'primevue/useconfirm'
import { useToast } from 'primevue/usetoast'
import { paramGroupsApi, type ParamGroup } from '../api/paramGroups'
import type { EndpointPlatform } from '../api/endpoints'
import { ApiError } from '../api/client'
import ParamsEditor from '../components/ParamsEditor.vue'
import { formatDateTime, formatParams } from '../lib/format'
import { PARAM_CATALOG } from '../lib/paramCatalog'
import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()
const confirm = useConfirm()
const toast = useToast()

const groups = ref<ParamGroup[]>([])
const loading = ref(true)
const loadError = ref<string | null>(null)

async function load() {
  loading.value = true
  loadError.value = null
  try {
    groups.value = await paramGroupsApi.list()
  } catch (err) {
    loadError.value = err instanceof ApiError ? err.message : 'Failed to load parameter groups.'
  } finally {
    loading.value = false
  }
}

onMounted(load)

// --- create / edit dialog ------------------------------------------------

/** Which platform's catalog feeds the editor's suggestions. Deliberately not
 * persisted: a group is not bound to a platform — this only picks which key
 * suggestions show while authoring (a vLLM-flavored group wants
 * `chat_template_kwargs` offered). */
const suggestionPlatform = ref<EndpointPlatform>('generic')

const platformOptions = (Object.keys(PARAM_CATALOG) as EndpointPlatform[]).map((key) => ({
  label: PARAM_CATALOG[key].label,
  value: key,
}))

const dialogOpen = ref(false)
const editingId = ref<number | null>(null)
const formName = ref('')
const formDescription = ref('')
const formParams = ref<Record<string, unknown> | null>(null)
const formError = ref<string | null>(null)
const saving = ref(false)

const dialogHeader = computed(() =>
  editingId.value === null ? 'New parameter group' : 'Edit parameter group',
)

function openCreate() {
  editingId.value = null
  formName.value = ''
  formDescription.value = ''
  formParams.value = null
  formError.value = null
  suggestionPlatform.value = 'generic'
  dialogOpen.value = true
}

function openEdit(group: ParamGroup) {
  editingId.value = group.id
  formName.value = group.name
  formDescription.value = group.description ?? ''
  formParams.value = { ...group.params }
  formError.value = null
  dialogOpen.value = true
}

function onRowClick(event: DataTableRowClickEvent) {
  const target = event.originalEvent.target as HTMLElement | null
  if (target?.closest('a, button')) return
  if (!auth.canWrite) return
  openEdit(event.data as ParamGroup)
}

async function submitForm() {
  formError.value = null
  saving.value = true
  try {
    const input = {
      name: formName.value,
      description: formDescription.value.trim() || null,
      params: formParams.value ?? {},
    }
    if (editingId.value === null) {
      await paramGroupsApi.create(input)
      toast.add({ severity: 'success', summary: 'Parameter group created', life: 5000 })
    } else {
      await paramGroupsApi.update(editingId.value, input)
      toast.add({ severity: 'success', summary: 'Parameter group saved', life: 5000 })
    }
    dialogOpen.value = false
    await load()
  } catch (err) {
    formError.value = err instanceof ApiError ? err.message : 'Failed to save the parameter group.'
  } finally {
    saving.value = false
  }
}

// --- delete ---------------------------------------------------------------

function confirmDelete(group: ParamGroup) {
  confirm.require({
    header: 'Delete parameter group',
    message: `Delete parameter group "${group.name}"? Past runs keep the merged params and the name they froze.`,
    acceptProps: { label: 'Delete', severity: 'danger' },
    rejectProps: { label: 'Cancel', text: true },
    accept: () => void removeGroup(group),
  })
}

async function removeGroup(group: ParamGroup) {
  try {
    await paramGroupsApi.remove(group.id)
    await load()
  } catch (err) {
    toast.add({
      severity: 'error',
      summary: 'Failed to delete parameter group',
      detail: err instanceof ApiError ? err.message : undefined,
      life: 5000,
    })
  }
}
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div class="page-heading">
        <h1>Parameter groups</h1>
        <p class="subtitle">
          Named request-parameter presets — "no thinking", "temp 0" — selectable on any run,
          whatever the endpoint or model, and merged between the endpoint's defaults and the
          run's own parameters.
        </p>
      </div>
      <Button v-if="auth.canWrite" label="New group" icon="pi pi-plus" @click="openCreate" />
    </div>

    <Message v-if="loadError" severity="error" :closable="false">{{ loadError }}</Message>

    <DataTable
      :value="groups"
      :loading="loading"
      data-key="id"
      class="table list-table"
      :class="{ 'row-nav': auth.canWrite }"
      removable-sort
      @row-click="onRowClick"
    >
      <template #empty
        >No parameter groups yet — add one here, or save a run's parameters as one on the
        new-run page.</template
      >
      <Column field="name" header="Name" sortable />
      <Column field="description" header="Description">
        <template #body="{ data }: { data: ParamGroup }">
          <span class="muted">{{ data.description }}</span>
        </template>
      </Column>
      <Column header="Parameters">
        <template #body="{ data }: { data: ParamGroup }">
          <code class="params-line" :title="formatParams(data.params)">{{
            formatParams(data.params)
          }}</code>
        </template>
      </Column>
      <Column field="updated_at" header="Updated" sortable>
        <template #body="{ data }: { data: ParamGroup }">{{
          formatDateTime(data.updated_at)
        }}</template>
      </Column>
      <Column v-if="auth.canWrite" class="row-actions">
        <template #body="{ data }: { data: ParamGroup }">
          <Button
            icon="pi pi-trash"
            text
            severity="danger"
            size="small"
            :title="`Delete ${data.name}`"
            @click="confirmDelete(data)"
          />
        </template>
      </Column>
    </DataTable>

    <Dialog v-model:visible="dialogOpen" modal :header="dialogHeader" class="form-dialog">
      <form class="dialog-form" @submit.prevent="submitForm">
        <div class="field">
          <label for="param-group-name">Name *</label>
          <InputText
            id="param-group-name"
            v-model="formName"
            required
            placeholder="no thinking"
            autofocus
          />
        </div>
        <div class="field">
          <label for="param-group-description">Description</label>
          <Textarea
            id="param-group-description"
            v-model="formDescription"
            rows="2"
            auto-resize
            placeholder="Disables Qwen3 thinking via chat_template_kwargs (vLLM)"
          />
        </div>
        <div class="field">
          <label for="param-group-platform">Suggestions for</label>
          <Select
            id="param-group-platform"
            v-model="suggestionPlatform"
            :options="platformOptions"
            option-label="label"
            option-value="value"
          />
          <p class="hint">
            Only picks which parameter names the editor suggests — a group is not bound to a
            platform, and every key is sent verbatim wherever the group is used.
          </p>
        </div>
        <div class="field">
          <span class="label">Parameters</span>
          <ParamsEditor v-model="formParams" :platform="suggestionPlatform" />
        </div>
        <Message v-if="formError" severity="error" :closable="false">{{ formError }}</Message>
        <div class="dialog-actions">
          <Button type="button" label="Cancel" text @click="dialogOpen = false" />
          <Button
            type="submit"
            :label="editingId === null ? 'Create group' : 'Save group'"
            :loading="saving"
          />
        </div>
      </form>
    </Dialog>
  </div>
</template>

<style scoped>
.muted {
  color: var(--p-text-muted-color);
}

.params-line {
  display: inline-block;
  max-width: 24rem;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  vertical-align: bottom;
  font-size: 0.8125rem;
}
</style>
