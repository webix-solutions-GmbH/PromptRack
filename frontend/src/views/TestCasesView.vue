<script setup lang="ts">
// Test cases list — the regression suite (the old app's `/prompts`, renamed
// per the pivot: a test case is now input + rubric + tool config, and
// references a *prompt* asset rather than duplicating it).
//
// Group-first, single column: one collapsible `Panel` per test group, all of
// them on the page at once, each holding its own table of cases. The suite's
// structure *is* groups-containing-cases, and the old two-pane split (a
// sidebar of group names, a flat table of whichever one was selected — the
// old `GroupSidebar` + `PromptsPanel`) showed one group at a time and read as
// a filter rather than as the shape of the suite. `Panel` over `Accordion`
// because each header carries its own edit/delete/new-case controls and every
// group collapses independently; neither component is used anywhere else in
// the app, so there was no existing idiom to follow.
//
// `?group=<id>` now solos one group (that group alone, expanded) rather than
// filtering a table — still the whole state of the view, so a link to one
// group's suite stays shareable exactly as `/prompts?group=` was. Every group
// starts **expanded**: with 5 groups the page is one scroll, and a
// collapsed-by-default page would open on nothing but five bars, hiding the
// content the page exists to show behind a click each.
import { computed, onMounted, ref } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import Button from 'primevue/button'
import Column from 'primevue/column'
import DataTable from 'primevue/datatable'
import Dialog from 'primevue/dialog'
import InputText from 'primevue/inputtext'
import Message from 'primevue/message'
import Panel from 'primevue/panel'
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

/** The group `?group=<id>` solos, if any. */
const soloGroupId = computed<number | null>(() => {
  const raw = route.query.group
  const id = Number(Array.isArray(raw) ? raw[0] : raw)
  return Number.isFinite(id) && id > 0 ? id : null
})

// Every case is fetched once and bucketed client-side: the page renders all
// groups anyway, and soloing one is then a filter over data already in hand
// rather than a round trip.
async function load() {
  loading.value = true
  loadError.value = null
  try {
    const [groupRows, caseRows] = await Promise.all([testGroupsApi.list(), testCasesApi.list()])
    groups.value = groupRows
    testCases.value = caseRows
  } catch (err) {
    loadError.value = err instanceof ApiError ? err.message : 'Failed to load test cases.'
  } finally {
    loading.value = false
  }
}

onMounted(load)

const casesByGroup = computed(() => {
  const byGroup = new Map<number, TestCase[]>()
  for (const testCase of testCases.value) {
    const bucket = byGroup.get(testCase.group_id)
    if (bucket) bucket.push(testCase)
    else byGroup.set(testCase.group_id, [testCase])
  }
  return byGroup
})

function casesFor(groupId: number): TestCase[] {
  return casesByGroup.value.get(groupId) ?? []
}

const visibleGroups = computed(() =>
  soloGroupId.value === null
    ? groups.value
    : groups.value.filter((group) => group.id === soloGroupId.value),
)

const collapsedGroupIds = ref(new Set<number>())

function soloGroup(groupId: number | null) {
  // A soloed group is shown expanded whatever it was collapsed to before.
  if (groupId !== null) collapsedGroupIds.value.delete(groupId)
  router.push({ query: { ...route.query, group: groupId ?? undefined } })
}

function setCollapsed(groupId: number, collapsed: boolean) {
  if (collapsed) collapsedGroupIds.value.add(groupId)
  else collapsedGroupIds.value.delete(groupId)
}

/** Both slots in one column, prefixed by which channel each one is sent on —
 * a case can reference a system prompt, a task prompt, both, or neither. */
function promptRefFor(caseRow: TestCase): string {
  const refs: string[] = []
  if (caseRow.system_prompt_name) refs.push(`system: ${caseRow.system_prompt_name}`)
  if (caseRow.task_prompt_name) refs.push(`task: ${caseRow.task_prompt_name}`)
  return refs.length > 0 ? refs.join(' · ') : '—'
}

function newCaseIn(groupId: number) {
  router.push({ path: '/test-cases/new', query: { group: groupId } })
}

// --- group create / edit dialog -------------------------------------------

interface GroupFormState {
  name: string
  description: string
  // Not editable here, but the update route replaces the whole group —
  // leaving it out of the body would reset it to 0.
  sort_order: number
}

const dialogOpen = ref(false)
const editingGroup = ref<TestGroup | null>(null)
const form = ref<GroupFormState>({ name: '', description: '', sort_order: 0 })
const formError = ref<string | null>(null)
const saving = ref(false)

function openCreateGroup() {
  editingGroup.value = null
  form.value = { name: '', description: '', sort_order: 0 }
  formError.value = null
  dialogOpen.value = true
}

function openEditGroup(group: TestGroup) {
  editingGroup.value = group
  form.value = {
    name: group.name,
    description: group.description ?? '',
    sort_order: group.sort_order,
  }
  formError.value = null
  dialogOpen.value = true
}

async function submitForm() {
  formError.value = null
  saving.value = true
  try {
    if (editingGroup.value) {
      await testGroupsApi.update(editingGroup.value.id, {
        name: form.value.name,
        description: form.value.description || null,
        sort_order: form.value.sort_order,
      })
      toast.add({ severity: 'success', summary: 'Group saved', life: 3000 })
    } else {
      // Not soloed after creating: every group is on the page already, so
      // jumping into the new (empty) one would only hide the rest.
      await testGroupsApi.create({
        name: form.value.name,
        description: form.value.description || null,
      })
      toast.add({ severity: 'success', summary: 'Group created', life: 3000 })
    }
    dialogOpen.value = false
    await load()
  } catch (err) {
    formError.value = err instanceof ApiError ? err.message : 'Failed to save the group.'
  } finally {
    saving.value = false
  }
}

// --- group delete ---------------------------------------------------------

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
    // Soloing a group that no longer exists would leave the page empty.
    if (soloGroupId.value === group.id) soloGroup(null)
    await load()
  } catch (err) {
    toast.add({
      severity: 'error',
      summary: 'Failed to delete group',
      detail: err instanceof ApiError ? err.message : undefined,
      life: 5000,
    })
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
    <div class="page-header">
      <div class="page-heading">
        <h1>Test Cases</h1>
        <p class="subtitle">
          The regression suite: one input plus its rubric plus the tool config to run it with, each
          referencing a prompt asset rather than duplicating it.
        </p>
      </div>
      <div v-if="auth.canWrite" class="header-actions">
        <Button label="New group" icon="pi pi-plus" outlined @click="openCreateGroup" />
        <!-- Kept alongside each panel's own prefilled button: with no groups
             yet, this is the only way into the editor (which picks the group
             itself). -->
        <Button
          label="New test case"
          icon="pi pi-plus"
          @click="router.push({ path: '/test-cases/new' })"
        />
      </div>
    </div>

    <Message v-if="loadError" severity="error" :closable="false">{{ loadError }}</Message>

    <div v-if="soloGroupId !== null" class="solo-row">
      <button type="button" class="clear-filter" @click="soloGroup(null)">Show all groups</button>
    </div>

    <p v-if="loading" class="empty">Loading…</p>
    <p v-else-if="visibleGroups.length === 0" class="empty">
      {{
        soloGroupId === null
          ? 'No groups yet — add one with "New group".'
          : 'That group no longer exists.'
      }}
    </p>

    <Panel
      v-for="group in visibleGroups"
      :key="group.id"
      class="group-panel"
      toggleable
      :collapsed="collapsedGroupIds.has(group.id)"
      @update:collapsed="setCollapsed(group.id, $event)"
    >
      <template #header>
        <div class="group-title">
          <button
            v-if="soloGroupId === null"
            type="button"
            class="group-name"
            title="Show only this group"
            @click="soloGroup(group.id)"
          >
            {{ group.name }}
          </button>
          <span v-else class="group-name static">{{ group.name }}</span>
          <span class="count">
            {{ group.test_case_count }} test case{{ group.test_case_count === 1 ? '' : 's' }}
          </span>
        </div>
      </template>
      <template #icons>
        <template v-if="auth.canWrite">
          <Button
            label="New test case"
            icon="pi pi-plus"
            text
            size="small"
            @click="newCaseIn(group.id)"
          />
          <Button label="Edit" text size="small" @click="openEditGroup(group)" />
          <Button
            label="Delete"
            text
            size="small"
            severity="danger"
            @click="confirmDeleteGroup(group)"
          />
        </template>
      </template>

      <p v-if="group.description" class="group-description">{{ group.description }}</p>

      <DataTable :value="casesFor(group.id)" data-key="id" class="table">
        <template #empty>No test cases in this group yet.</template>
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
    </Panel>

    <Dialog
      v-model:visible="dialogOpen"
      modal
      :header="editingGroup ? 'Edit group' : 'New group'"
      class="form-dialog"
    >
      <form class="dialog-form" @submit.prevent="submitForm">
        <div class="field">
          <label for="group-name">Name *</label>
          <InputText
            id="group-name"
            v-model="form.name"
            required
            placeholder="Invoice extraction"
            autofocus
          />
        </div>
        <div class="field">
          <label for="group-description">Description</label>
          <Textarea
            id="group-description"
            v-model="form.description"
            rows="3"
            auto-resize
            placeholder="What this group of test cases covers"
          />
        </div>
        <Message v-if="formError" severity="error" :closable="false">{{ formError }}</Message>
        <div class="dialog-actions">
          <Button type="button" label="Cancel" text @click="dialogOpen = false" />
          <Button
            type="submit"
            :label="editingGroup ? 'Save group' : 'Create group'"
            :loading="saving"
          />
        </div>
      </form>
    </Dialog>
  </div>
</template>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 1rem;
  margin-bottom: 0.5rem;
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

.header-actions {
  display: flex;
  gap: 0.5rem;
  flex-shrink: 0;
}

.solo-row {
  display: flex;
}

.clear-filter {
  background: none;
  border: none;
  padding: 0;
  font-size: 0.8125rem;
  color: var(--p-text-muted-color);
  text-decoration: underline;
  cursor: pointer;
}

.empty {
  font-size: 0.875rem;
  color: var(--p-text-muted-color);
  margin: 0;
}

.group-title {
  display: flex;
  align-items: baseline;
  gap: 0.625rem;
  min-width: 0;
}

.group-name {
  background: none;
  border: none;
  padding: 0;
  font: inherit;
  font-size: 1.0625rem;
  font-weight: 600;
  color: inherit;
  cursor: pointer;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.group-name.static {
  cursor: default;
}

.group-name:not(.static):hover {
  text-decoration: underline;
}

.count {
  font-size: 0.8125rem;
  font-weight: 400;
  color: var(--p-text-muted-color);
  white-space: nowrap;
}

.group-description {
  font-size: 0.875rem;
  color: var(--p-text-muted-color);
  margin: 0 0 0.75rem;
  max-width: 60rem;
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
