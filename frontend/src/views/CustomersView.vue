<script setup lang="ts">
// Workspaces list. A workspace is a label, not a tenant — every signed-in
// user can switch into any of them (see AppLayout's switcher) — so this page
// is about naming and lifecycle, not membership. Rename/archive are member
// actions (`canWrite`); delete is admin-only, mirroring the old
// `deleteCustomer is admin-only (the rest is member)` split.
import { computed, onMounted, ref } from 'vue'
import Button from 'primevue/button'
import Column from 'primevue/column'
import DataTable from 'primevue/datatable'
import Dialog from 'primevue/dialog'
import InputText from 'primevue/inputtext'
import Message from 'primevue/message'
import Tag from 'primevue/tag'
import { useConfirm } from 'primevue/useconfirm'
import { useToast } from 'primevue/usetoast'
import { customersApi, type Customer } from '../api/customers'
import { ApiError } from '../api/client'
import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()
const confirm = useConfirm()
const toast = useToast()

const customers = ref<Customer[]>([])
const loading = ref(true)
const loadError = ref<string | null>(null)

// The oldest workspace (first in id order, which is how the list arrives)
// is the one every pre-workspace row was assigned to.
const defaultId = computed(() => customers.value[0]?.id)

async function load() {
  loading.value = true
  loadError.value = null
  try {
    customers.value = await customersApi.list()
  } catch (err) {
    loadError.value = err instanceof ApiError ? err.message : 'Failed to load workspaces.'
  } finally {
    loading.value = false
  }
}

onMounted(load)

// The sidebar switcher keeps its own copy of this list; refresh it too so a
// rename/archive shows up there without a full page reload.
async function reload() {
  await Promise.all([load(), auth.fetchCustomers()])
}

function describeContents(customer: Customer): string {
  const counts = customer.content
  const parts = [
    `${counts.endpoints} endpoint${counts.endpoints === 1 ? '' : 's'}`,
    `${counts.prompts} prompt${counts.prompts === 1 ? '' : 's'}`,
    `${counts.toolsets} toolset${counts.toolsets === 1 ? '' : 's'}`,
    `${counts.test_groups} test group${counts.test_groups === 1 ? '' : 's'}`,
    `${counts.runs} run${counts.runs === 1 ? '' : 's'}`,
  ]
  return parts.join(' · ')
}

// --- create / rename dialog ------------------------------------------------

const dialogOpen = ref(false)
const editing = ref<Customer | null>(null)
const formName = ref('')
const formDescription = ref('')
const formError = ref<string | null>(null)
const saving = ref(false)

function openCreate() {
  editing.value = null
  formName.value = ''
  formDescription.value = ''
  formError.value = null
  dialogOpen.value = true
}

function openEdit(customer: Customer) {
  editing.value = customer
  formName.value = customer.name
  formDescription.value = customer.description ?? ''
  formError.value = null
  dialogOpen.value = true
}

async function submitForm() {
  formError.value = null
  saving.value = true
  try {
    const input = { name: formName.value, description: formDescription.value || null }
    if (editing.value) {
      await customersApi.update(editing.value.id, input)
      toast.add({ severity: 'success', summary: 'Workspace renamed', life: 3000 })
    } else {
      await customersApi.create(input)
      toast.add({ severity: 'success', summary: 'Workspace created', life: 3000 })
    }
    dialogOpen.value = false
    await reload()
  } catch (err) {
    formError.value = err instanceof ApiError ? err.message : 'Failed to save the workspace.'
  } finally {
    saving.value = false
  }
}

// --- archive / delete --------------------------------------------------

const busyId = ref<number | null>(null)

async function toggleArchived(customer: Customer) {
  busyId.value = customer.id
  try {
    await customersApi.setArchived(customer.id, !customer.archived)
    await reload()
  } catch (err) {
    toast.add({
      severity: 'error',
      summary: 'Failed to change the workspace',
      detail: err instanceof ApiError ? err.message : undefined,
      life: 5000,
    })
  } finally {
    busyId.value = null
  }
}

function confirmDelete(customer: Customer) {
  confirm.require({
    header: 'Delete workspace',
    message: `Delete workspace "${customer.name}"? This cannot be undone.`,
    acceptProps: { label: 'Delete', severity: 'danger' },
    rejectProps: { label: 'Cancel', text: true },
    accept: () => void removeCustomer(customer),
  })
}

async function removeCustomer(customer: Customer) {
  busyId.value = customer.id
  try {
    await customersApi.remove(customer.id)
    toast.add({ severity: 'success', summary: 'Workspace deleted', life: 3000 })
    await reload()
  } catch (err) {
    // The delete guard answers with a sentence ("holds 3 endpoints, 1 run…")
    // rather than a raw constraint violation — surface it as-is.
    toast.add({
      severity: 'error',
      summary: 'Could not delete workspace',
      detail: err instanceof ApiError ? err.message : 'Unexpected error.',
      life: 8000,
    })
  } finally {
    busyId.value = null
  }
}
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div class="page-heading">
        <h1>Workspaces</h1>
        <p class="subtitle">
          One workspace per customer engagement. Endpoints, prompts, toolsets, test cases and runs
          each belong to exactly one — switch between them in the sidebar.
        </p>
      </div>
      <Button v-if="auth.canWrite" label="New workspace" icon="pi pi-plus" @click="openCreate" />
    </div>

    <Message v-if="loadError" severity="error" :closable="false">{{ loadError }}</Message>

    <DataTable :value="customers" :loading="loading" data-key="id" class="table list-table">
      <template #empty>No workspaces yet.</template>
      <Column field="name" header="Name">
        <template #body="{ data }: { data: Customer }">
          <div class="name-cell">
            <span class="name">{{ data.name }}</span>
            <Tag v-if="data.is_base" value="Base" severity="contrast" />
            <Tag v-if="data.id === auth.activeCustomer?.id" value="active" severity="info" />
            <Tag v-if="data.id === defaultId" value="default" severity="secondary" />
            <Tag v-if="data.archived" value="archived" severity="warn" />
          </div>
          <span v-if="data.description" class="description">{{ data.description }}</span>
        </template>
      </Column>
      <Column header="Contents">
        <template #body="{ data }: { data: Customer }">
          <span class="contents">{{ describeContents(data) }}</span>
        </template>
      </Column>
      <Column header="" class="actions-column">
        <template #body="{ data }: { data: Customer }">
          <div class="row-actions">
            <Button
              v-if="auth.canWrite"
              label="Rename"
              text
              size="small"
              @click="openEdit(data)"
            />
            <Button
              v-if="auth.canWrite && !data.is_base"
              :label="data.archived ? 'Unarchive' : 'Archive'"
              text
              size="small"
              :loading="busyId === data.id"
              @click="toggleArchived(data)"
            />
            <Button
              v-if="auth.canAdminister && !data.is_base"
              label="Delete"
              text
              size="small"
              severity="danger"
              :loading="busyId === data.id"
              @click="confirmDelete(data)"
            />
          </div>
        </template>
      </Column>
    </DataTable>

    <Dialog
      v-model:visible="dialogOpen"
      modal
      :header="editing ? 'Rename workspace' : 'New workspace'"
      class="form-dialog"
    >
      <form class="dialog-form" @submit.prevent="submitForm">
        <div class="field">
          <label for="customer-name">Name *</label>
          <InputText id="customer-name" v-model="formName" required placeholder="Acme GmbH" autofocus />
        </div>
        <div class="field">
          <label for="customer-description">Description</label>
          <InputText
            id="customer-description"
            v-model="formDescription"
            placeholder="Invoice agent evaluation, Q3"
          />
        </div>
        <Message v-if="formError" severity="error" :closable="false">{{ formError }}</Message>
        <div class="dialog-actions">
          <Button type="button" label="Cancel" text @click="dialogOpen = false" />
          <Button type="submit" label="Save" :loading="saving" />
        </div>
      </form>
    </Dialog>
  </div>
</template>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  gap: 1.5rem;
  max-width: 64rem;
}

.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 1rem;
}

.page-heading h1 {
  font-size: 1.5rem;
  font-weight: 600;
  margin: 0 0 0.375rem;
}

.subtitle {
  max-width: 48rem;
  color: var(--p-text-muted-color);
  font-size: 0.875rem;
  margin: 0;
}

.name-cell {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  flex-wrap: wrap;
}

.name {
  font-weight: 500;
}

.description {
  display: block;
  font-size: 0.8125rem;
  color: var(--p-text-muted-color);
  margin-top: 0.125rem;
}

.contents {
  font-size: 0.8125rem;
  color: var(--p-text-muted-color);
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

.dialog-form {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 0.375rem;
}

.field label {
  font-size: 0.8125rem;
  font-weight: 500;
  color: var(--p-text-muted-color);
}

.dialog-actions {
  display: flex;
  justify-content: flex-end;
  gap: 0.5rem;
}
</style>
