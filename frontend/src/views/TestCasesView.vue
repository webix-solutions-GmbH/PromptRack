<script setup lang="ts">
// Test cases list — the regression suite (the old app's `/prompts`, renamed
// per the pivot: a test case is now input + rubric + tool config, and
// references a *prompt* asset rather than duplicating it). Two panels, same
// split as the old `GroupSidebar` + `PromptsPanel`
// (`git show master:src/components/prompts/{group-sidebar,prompts-panel}.tsx`):
// groups on the left (create/rename/delete, member-writable — a group is
// content, not credentials), test cases of the selected group on the right.
// The selected group lives in the URL (`?group=<id>`) so a link to one
// group's suite is shareable, same as the old `/prompts?group=`.
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import Button from 'primevue/button'
import Column from 'primevue/column'
import DataTable from 'primevue/datatable'
import InputText from 'primevue/inputtext'
import Message from 'primevue/message'
import Tag from 'primevue/tag'
import Textarea from 'primevue/textarea'
import { useConfirm } from 'primevue/useconfirm'
import { useToast } from 'primevue/usetoast'
import { testCasesApi, testGroupsApi, type TestCase, type TestGroup } from '../api/testCases'
import { ApiError } from '../api/client'
import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()
const route = useRoute()
const router = useRouter()
const confirm = useConfirm()
const toast = useToast()

const groups = ref<TestGroup[]>([])
const testCases = ref<TestCase[]>([])
const loading = ref(true)
const loadError = ref<string | null>(null)

const selectedGroupId = computed<number | null>(() => {
  const raw = route.query.group
  const id = Number(Array.isArray(raw) ? raw[0] : raw)
  return Number.isFinite(id) && id > 0 ? id : null
})

async function load() {
  loading.value = true
  loadError.value = null
  try {
    const [groupRows, caseRows] = await Promise.all([
      testGroupsApi.list(),
      testCasesApi.list(selectedGroupId.value ?? undefined),
    ])
    groups.value = groupRows
    testCases.value = caseRows
  } catch (err) {
    loadError.value = err instanceof ApiError ? err.message : 'Failed to load test cases.'
  } finally {
    loading.value = false
  }
}

onMounted(load)
watch(selectedGroupId, load)

function selectGroup(groupId: number | null) {
  router.push({ query: { ...route.query, group: groupId ?? undefined } })
}

function promptRefFor(caseRow: TestCase): string {
  return caseRow.prompt_id === null ? '—' : `prompt #${caseRow.prompt_id}`
}

// --- group create / rename / delete --------------------------------------

const editingGroupId = ref<number | null>(null)
// `sort_order` is not editable here, but the rename route replaces the whole
// group — leaving it out of the body would reset it to 0.
const groupForm = ref({ name: '', description: '', sort_order: 0 })
const groupFormError = ref<string | null>(null)
const savingGroup = ref(false)

function startEditGroup(group: TestGroup) {
  editingGroupId.value = group.id
  groupForm.value = {
    name: group.name,
    description: group.description ?? '',
    sort_order: group.sort_order,
  }
  groupFormError.value = null
}

function cancelEditGroup() {
  editingGroupId.value = null
}

async function saveGroup(groupId: number) {
  groupFormError.value = null
  savingGroup.value = true
  try {
    await testGroupsApi.update(groupId, {
      name: groupForm.value.name,
      description: groupForm.value.description || null,
      sort_order: groupForm.value.sort_order,
    })
    editingGroupId.value = null
    await load()
  } catch (err) {
    groupFormError.value = err instanceof ApiError ? err.message : 'Failed to save the group.'
  } finally {
    savingGroup.value = false
  }
}

function confirmDeleteGroup(group: TestGroup) {
  confirm.require({
    header: 'Delete group',
    message: `Delete group "${group.name}" and its ${group.test_case_count} test case(s)? This cannot be undone.`,
    acceptProps: { label: 'Delete', severity: 'danger' },
    rejectProps: { label: 'Cancel', text: true },
    accept: () => void removeGroup(group),
  })
}

async function removeGroup(group: TestGroup) {
  try {
    await testGroupsApi.remove(group.id)
    if (selectedGroupId.value === group.id) {
      selectGroup(null)
    } else {
      await load()
    }
  } catch (err) {
    toast.add({
      severity: 'error',
      summary: 'Failed to delete group',
      detail: err instanceof ApiError ? err.message : undefined,
      life: 5000,
    })
  }
}

const newGroupName = ref('')
const newGroupDescription = ref('')
const creatingGroup = ref(false)
const createGroupError = ref<string | null>(null)

async function createGroup() {
  if (!newGroupName.value.trim()) return
  createGroupError.value = null
  creatingGroup.value = true
  try {
    const group = await testGroupsApi.create({
      name: newGroupName.value.trim(),
      description: newGroupDescription.value || null,
    })
    newGroupName.value = ''
    newGroupDescription.value = ''
    await load()
    selectGroup(group.id)
  } catch (err) {
    createGroupError.value = err instanceof ApiError ? err.message : 'Failed to create the group.'
  } finally {
    creatingGroup.value = false
  }
}

// --- test case delete ------------------------------------------------

const deletingCaseId = ref<number | null>(null)

function confirmDeleteCase(testCase: TestCase) {
  confirm.require({
    header: 'Delete test case',
    message: `Delete test case "${testCase.title}"? This cannot be undone.`,
    acceptProps: { label: 'Delete', severity: 'danger' },
    rejectProps: { label: 'Cancel', text: true },
    accept: () => void removeCase(testCase),
  })
}

async function removeCase(testCase: TestCase) {
  deletingCaseId.value = testCase.id
  try {
    await testCasesApi.remove(testCase.id)
    await load()
  } catch (err) {
    toast.add({
      severity: 'error',
      summary: 'Failed to delete test case',
      detail: err instanceof ApiError ? err.message : undefined,
      life: 5000,
    })
  } finally {
    deletingCaseId.value = null
  }
}
</script>

<template>
  <div class="page">
    <div class="page-heading">
      <h1>Test Cases</h1>
      <p class="subtitle">
        The regression suite: one input plus its rubric plus the tool config to run it with, each
        referencing a prompt asset rather than duplicating it.
      </p>
    </div>

    <Message v-if="loadError" severity="error" :closable="false">{{ loadError }}</Message>

    <div class="layout">
      <aside class="sidebar">
        <h2>Groups</h2>
        <ul class="group-list">
          <li v-if="groups.length === 0" class="empty">No groups yet.</li>
          <li
            v-for="group in groups"
            :key="group.id"
            :class="['group-item', { active: group.id === selectedGroupId }]"
          >
            <template v-if="editingGroupId === group.id">
              <form class="group-edit-form" @submit.prevent="saveGroup(group.id)">
                <InputText v-model="groupForm.name" required placeholder="Group name" size="small" />
                <Textarea
                  v-model="groupForm.description"
                  rows="2"
                  auto-resize
                  placeholder="description (optional)"
                />
                <Message v-if="groupFormError" severity="error" :closable="false">{{
                  groupFormError
                }}</Message>
                <div class="group-edit-actions">
                  <Button type="submit" label="Save" size="small" :loading="savingGroup" />
                  <Button
                    type="button"
                    label="Cancel"
                    size="small"
                    text
                    @click="cancelEditGroup"
                  />
                </div>
              </form>
            </template>
            <template v-else>
              <button type="button" class="group-link" @click="selectGroup(group.id)">
                {{ group.name }} <span class="count">({{ group.test_case_count }})</span>
              </button>
              <div v-if="auth.canWrite" class="group-actions">
                <button type="button" class="link-action" @click="startEditGroup(group)">
                  edit
                </button>
                <button
                  type="button"
                  class="link-action danger"
                  @click="confirmDeleteGroup(group)"
                >
                  delete
                </button>
              </div>
            </template>
          </li>
        </ul>

        <button
          v-if="selectedGroupId !== null"
          type="button"
          class="clear-filter"
          @click="selectGroup(null)"
        >
          Show all groups
        </button>

        <form v-if="auth.canWrite" class="new-group-form" @submit.prevent="createGroup">
          <label for="new-group-name">New group</label>
          <InputText id="new-group-name" v-model="newGroupName" required placeholder="Group name" />
          <Textarea v-model="newGroupDescription" rows="2" auto-resize placeholder="description (optional)" />
          <Message v-if="createGroupError" severity="error" :closable="false">{{
            createGroupError
          }}</Message>
          <Button type="submit" label="Create group" :loading="creatingGroup" size="small" />
        </form>
      </aside>

      <section class="main">
        <div class="page-header">
          <h2>
            {{ selectedGroupId === null ? 'All test cases' : (groups.find((g) => g.id === selectedGroupId)?.name ?? 'Test cases') }}
          </h2>
          <Button
            v-if="auth.canWrite"
            label="New test case"
            icon="pi pi-plus"
            @click="router.push({ path: '/test-cases/new', query: selectedGroupId ? { group: selectedGroupId } : {} })"
          />
        </div>

        <DataTable :value="testCases" :loading="loading" data-key="id" class="table">
          <template #empty>No test cases yet — add one with "New test case".</template>
          <Column field="title" header="Title">
            <template #body="{ data }: { data: TestCase }">
              <RouterLink :to="`/test-cases/${data.id}`" class="name-link">{{ data.title }}</RouterLink>
            </template>
          </Column>
          <Column header="Prompt">
            <template #body="{ data }: { data: TestCase }">{{ promptRefFor(data) }}</template>
          </Column>
          <Column header="Tools">
            <template #body="{ data }: { data: TestCase }">
              <Tag
                :value="data.tool_mode"
                :severity="data.tool_mode === 'none' ? 'secondary' : 'info'"
              />
            </template>
          </Column>
          <Column header="" class="actions-column">
            <template #body="{ data }: { data: TestCase }">
              <Button
                v-if="auth.canWrite"
                label="Delete"
                text
                size="small"
                severity="danger"
                :loading="deletingCaseId === data.id"
                @click="confirmDeleteCase(data)"
              />
            </template>
          </Column>
        </DataTable>
      </section>
    </div>
  </div>
</template>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  gap: 1.5rem;
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

.layout {
  display: grid;
  grid-template-columns: 16rem 1fr;
  gap: 2rem;
  align-items: start;
}

.sidebar {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.sidebar h2 {
  font-size: 1.0625rem;
  font-weight: 600;
  margin: 0;
}

.group-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}

.empty {
  font-size: 0.875rem;
  color: var(--p-text-muted-color);
}

.group-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
  border-radius: var(--p-content-border-radius);
  padding: 0.375rem 0.625rem;
}

.group-item.active {
  background: var(--p-highlight-background);
  color: var(--p-highlight-color);
}

.group-link {
  flex: 1;
  min-width: 0;
  text-align: left;
  background: none;
  border: none;
  padding: 0;
  font: inherit;
  color: inherit;
  cursor: pointer;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.count {
  opacity: 0.7;
}

.group-actions {
  display: flex;
  gap: 0.5rem;
  font-size: 0.75rem;
  flex-shrink: 0;
}

.link-action {
  background: none;
  border: none;
  padding: 0;
  font: inherit;
  color: inherit;
  text-decoration: underline;
  cursor: pointer;
  opacity: 0.8;
}

.link-action:hover {
  opacity: 1;
}

.link-action.danger {
  color: var(--p-red-500);
}

.group-edit-form {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  width: 100%;
  padding: 0.25rem 0;
}

.group-edit-actions {
  display: flex;
  gap: 0.5rem;
}

.clear-filter {
  align-self: flex-start;
  background: none;
  border: none;
  padding: 0;
  font-size: 0.8125rem;
  color: var(--p-text-muted-color);
  text-decoration: underline;
  cursor: pointer;
}

.new-group-form {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  border-top: 1px solid var(--p-content-border-color);
  padding-top: 1rem;
}

.new-group-form label {
  font-size: 0.8125rem;
  font-weight: 500;
  color: var(--p-text-muted-color);
}

.main {
  display: flex;
  flex-direction: column;
  gap: 1rem;
  min-width: 0;
}

.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 1rem;
}

.page-header h2 {
  font-size: 1.0625rem;
  font-weight: 600;
  margin: 0;
}

.name-link {
  font-weight: 500;
  color: var(--p-text-color);
  text-decoration: none;
}

.name-link:hover {
  text-decoration: underline;
}

.actions-column {
  width: 1%;
  white-space: nowrap;
}
</style>
