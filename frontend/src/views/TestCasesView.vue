<script setup lang="ts">
// Test cases list — the regression suite. A test case is input + rubric +
// tool config, and references a *prompt* asset rather than duplicating it.
//
// Two layouts, chosen by `?view=`. Grouped (default) is one collapsible
// `Panel` per test group, all of them on the page at once, each holding its
// own table of cases. The suite's structure *is* groups-containing-cases, and
// a two-pane split (a sidebar of group names, a flat table of whichever one
// was selected) shows one group at a time and reads as a filter rather than
// as the shape of the suite. `Panel` over `Accordion` because each header
// carries its own edit/delete/new-case controls and every group collapses
// independently; neither component is used anywhere else in the app, so
// there was no existing idiom to follow. `?view=flat` is the other layout:
// one ungrouped, sortable `DataTable` of every case with a Group column —
// "where is the case that mentions X" and "sort every case by group, by
// title, by tool mode", questions the panel layout cannot answer at all.
// The mode lives in the URL, not localStorage, matching every other toggle
// on this page: a link is the whole state of the view, so a colleague
// opening it sees what the sender saw.
//
// `?group=<id>` still solos one group (that group alone, expanded) rather
// than filtering a table — still the whole state of the view, so a link to
// one group's suite stays shareable exactly as `/prompts?group=` was.
//
// Groups now start **collapsed**. This page used to argue the opposite: with
// 5 groups it was one scroll, and a collapsed-by-default page would open on
// nothing but five bars, hiding the content the page exists to show behind a
// click each. The suite has grown past the point where that premise holds —
// an expanded-by-default page now opens on a wall of rows in which no group
// is findable, and the collapsed group list is the useful overview a suite
// this size actually needs. Collapsed gives the page back its index,
// `?group=` still solos one group expanded regardless of the default, and
// `?view=flat` covers the case the expanded default used to protect: seeing
// every case at once.
//
// `?prompt=<id>` (arriving from a prompt's "N test cases" link, which forces
// `?view=flat` there since a prompt's cases span groups) filters to cases
// referencing that prompt in **either** slot, system or task — kind is a
// property of the asset, and a task prompt's cases are just as much its
// blast radius.
import { computed, onMounted, ref } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import Button from 'primevue/button'
import Column from 'primevue/column'
import DataTable from 'primevue/datatable'
import type { DataTableRowClickEvent } from 'primevue/datatable'
import Dialog from 'primevue/dialog'
import InputText from 'primevue/inputtext'
import Message from 'primevue/message'
import Panel from 'primevue/panel'
import Select from 'primevue/select'
import SelectButton from 'primevue/selectbutton'
import Tag from 'primevue/tag'
import Textarea from 'primevue/textarea'
import { useConfirm } from 'primevue/useconfirm'
import { useToast } from 'primevue/usetoast'
import SearchField from '../components/SearchField.vue'
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

/** The prompt `?prompt=<id>` filters to, if any — see the module comment. */
const promptFilterId = computed<number | null>(() => {
  const raw = route.query.prompt
  const id = Number(Array.isArray(raw) ? raw[0] : raw)
  return Number.isFinite(id) && id > 0 ? id : null
})

function clearPromptFilter() {
  router.push({ query: { ...route.query, prompt: undefined } })
}

type ViewMode = 'grouped' | 'flat'

const viewModeOptions: { label: string; value: ViewMode }[] = [
  { label: 'Grouped', value: 'grouped' },
  { label: 'Flat', value: 'flat' },
]

/** `?view=flat` is the only non-default value — anything else (including
 * absent) reads as grouped, the same "unknown falls back to the safe default"
 * rule `?mode=` uses on `/results`. */
const viewMode = computed<ViewMode>(() => (route.query.view === 'flat' ? 'flat' : 'grouped'))

function setViewMode(mode: ViewMode) {
  router.push({ query: { ...route.query, view: mode === 'grouped' ? undefined : mode } })
}

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

/** `?prompt=<id>` narrowed to cases referencing it in either slot; otherwise
 * every case. Both the grouped and flat layouts read from this, not from
 * `testCases` directly, so the filter holds regardless of which one is on
 * screen. */
const filteredCases = computed(() =>
  promptFilterId.value === null
    ? testCases.value
    : testCases.value.filter(
        (testCase) =>
          testCase.system_prompt_id === promptFilterId.value ||
          testCase.task_prompt_id === promptFilterId.value,
      ),
)

/** The referenced prompt's name, read off whichever slot of whichever
 * filtered case holds it — a client-side filter has no separate fetch of the
 * prompt itself to name it from. `null` only if the id is stale (the prompt
 * was deleted after the link was shared). */
const promptFilterName = computed<string | null>(() => {
  if (promptFilterId.value === null) return null
  for (const testCase of filteredCases.value) {
    if (testCase.system_prompt_id === promptFilterId.value) return testCase.system_prompt_name
    if (testCase.task_prompt_id === promptFilterId.value) return testCase.task_prompt_name
  }
  return null
})

const casesByGroup = computed(() => {
  const byGroup = new Map<number, TestCase[]>()
  for (const testCase of filteredCases.value) {
    const bucket = byGroup.get(testCase.group_id)
    if (bucket) bucket.push(testCase)
    else byGroup.set(testCase.group_id, [testCase])
  }
  return byGroup
})

function casesFor(groupId: number): TestCase[] {
  return casesByGroup.value.get(groupId) ?? []
}

const groupNameById = computed(() => {
  const byId = new Map<number, string>()
  for (const group of groups.value) byId.set(group.id, group.name)
  return byId
})

/** The flat table's rows: every filtered case, each carrying its own group
 * name so the Group column can sort natively off a plain field rather than a
 * lookup the DataTable can't see into. */
const flatRows = computed(() =>
  filteredCases.value.map((testCase) => ({
    ...testCase,
    group_name: groupNameById.value.get(testCase.group_id) ?? `#${testCase.group_id}`,
  })),
)

// --- flat-view filters (same shapes as /prompts) --------------------------

const titleFilter = ref('')
// 0 is "all groups": a real id is never 0, and unlike `null` it is a value
// PrimeVue's Select will actually display instead of showing the placeholder.
const ALL_GROUPS = 0
const groupFilter = ref<number>(ALL_GROUPS)
// 'all' rather than null: SelectButton cannot highlight an option whose value is null.
const ALL_TOOL_MODES = 'all'
const toolModeFilter = ref<TestCase['tool_mode'] | typeof ALL_TOOL_MODES>(ALL_TOOL_MODES)

const groupFilterOptions = computed(() => [
  { label: 'All groups', value: ALL_GROUPS },
  ...groups.value.map((group) => ({ label: group.name, value: group.id })),
])

const toolModeFilterOptions: { label: string; value: TestCase['tool_mode'] | typeof ALL_TOOL_MODES }[] = [
  { label: 'All', value: ALL_TOOL_MODES },
  { label: 'none', value: 'none' },
  { label: 'definitions', value: 'definitions' },
  { label: 'execute', value: 'execute' },
]

const visibleFlatRows = computed(() => {
  const query = titleFilter.value.trim().toLowerCase()
  return flatRows.value.filter(
    (row) =>
      (groupFilter.value === ALL_GROUPS || row.group_id === groupFilter.value) &&
      (toolModeFilter.value === ALL_TOOL_MODES || row.tool_mode === toolModeFilter.value) &&
      (query === '' || row.title.toLowerCase().includes(query)),
  )
})

const flatFiltersActive = computed(
  () =>
    titleFilter.value.trim() !== '' ||
    groupFilter.value !== ALL_GROUPS ||
    toolModeFilter.value !== ALL_TOOL_MODES,
)

const visibleGroups = computed(() =>
  soloGroupId.value === null
    ? groups.value
    : groups.value.filter((group) => group.id === soloGroupId.value),
)

// Groups start collapsed (see the module comment); this set tracks ids a
// reader has explicitly opened, so the default only has to be inverted here
// rather than at every read site.
const expandedGroupIds = ref(new Set<number>())

function isExpanded(groupId: number): boolean {
  // A soloed group is always shown expanded, whatever it was toggled to
  // before — soloing IS "show me this one, fully".
  return soloGroupId.value === groupId || expandedGroupIds.value.has(groupId)
}

function setExpanded(groupId: number, expanded: boolean) {
  if (expanded) expandedGroupIds.value.add(groupId)
  else expandedGroupIds.value.delete(groupId)
}

function soloGroup(groupId: number | null) {
  router.push({ query: { ...route.query, group: groupId ?? undefined } })
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
      toast.add({ severity: 'success', summary: 'Group saved', life: 5000 })
    } else {
      // Not soloed after creating: every group is on the page already, so
      // jumping into the new (empty) one would only hide the rest.
      await testGroupsApi.create({
        name: form.value.name,
        description: form.value.description || null,
      })
      toast.add({ severity: 'success', summary: 'Group created', life: 5000 })
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

// Same row-navigation contract as /prompts: the whole row opens the case,
// while anchors and buttons inside a cell (the title link, Delete) keep
// their own actions.
function onCaseRowClick(event: DataTableRowClickEvent) {
  const target = event.originalEvent.target as HTMLElement | null
  if (target?.closest('a, button')) return
  void router.push(`/test-cases/${(event.data as TestCase).id}`)
}

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
        <!-- On the heading's side of the header row, not among the create
             buttons: it changes how the page reads, it does not add anything. -->
        <SelectButton
          class="view-toggle"
          :model-value="viewMode"
          :options="viewModeOptions"
          option-label="label"
          option-value="value"
          :allow-empty="false"
          size="small"
          @update:model-value="setViewMode"
        />
      </div>
      <div class="header-actions">
        <template v-if="auth.canWrite">
          <Button label="New group" icon="pi pi-plus" outlined @click="openCreateGroup" />
          <!-- Kept alongside each panel's own prefilled button: with no
               groups yet, this is the only way into the editor (which picks
               the group itself). -->
          <Button
            label="New test case"
            icon="pi pi-plus"
            @click="router.push({ path: '/test-cases/new' })"
          />
        </template>
      </div>
    </div>

    <Message v-if="loadError" severity="error" :closable="false">{{ loadError }}</Message>

    <div v-if="promptFilterId !== null" class="solo-row">
      <span class="filter-text">
        Showing test cases referencing prompt
        <strong>{{ promptFilterName ?? `#${promptFilterId}` }}</strong>
      </span>
      <button type="button" class="clear-filter" @click="clearPromptFilter">Clear filter</button>
    </div>

    <div v-if="viewMode === 'grouped' && soloGroupId !== null" class="solo-row">
      <button type="button" class="clear-filter" @click="soloGroup(null)">Show all groups</button>
    </div>

    <template v-if="viewMode === 'flat'">
      <div class="filter-row">
        <SearchField v-model="titleFilter" placeholder="Search by title" />
        <label class="filter-label" for="test-case-group-filter">Group</label>
        <Select
          v-model="groupFilter"
          input-id="test-case-group-filter"
          :options="groupFilterOptions"
          option-label="label"
          option-value="value"
          size="small"
          class="group-filter"
        />
        <span class="filter-label">Tools</span>
        <SelectButton
          v-model="toolModeFilter"
          :options="toolModeFilterOptions"
          option-label="label"
          option-value="value"
          :allow-empty="false"
          size="small"
        />
      </div>
      <DataTable
        :value="visibleFlatRows"
        data-key="id"
        sort-field="group_name"
        :sort-order="1"
        removable-sort
        :loading="loading"
        class="table list-table row-nav"
        @row-click="onCaseRowClick"
      >
        <template #empty>
          {{
            flatFiltersActive
              ? 'No test cases match this filter.'
              : 'No test cases yet — add one with "New test case".'
          }}
        </template>
        <Column field="group_name" header="Group" sortable />
        <Column field="title" header="Title" sortable>
          <template #body="{ data }: { data: TestCase }">
            <RouterLink :to="`/test-cases/${data.id}`" class="name-link">{{ data.title }}</RouterLink>
          </template>
        </Column>
        <Column header="Prompt">
          <template #body="{ data }: { data: TestCase }">{{ promptRefFor(data) }}</template>
        </Column>
        <Column field="tool_mode" header="Tools" sortable>
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
    </template>

    <template v-else>
      <p v-if="!loading && visibleGroups.length === 0" class="empty-state">
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
        :collapsed="!isExpanded(group.id)"
        @update:collapsed="setExpanded(group.id, !$event)"
      >
        <template #header>
          <div class="group-title">
            <!-- Panel's own toggler sits after the action buttons on the far
                 right; a disclosure chevron reads left of the name, so the
                 built-in one is hidden (CSS below) and this one drives the
                 same collapsed state. -->
            <button
              type="button"
              class="group-toggle"
              :aria-expanded="isExpanded(group.id)"
              :title="isExpanded(group.id) ? 'Collapse group' : 'Expand group'"
              @click="setExpanded(group.id, !isExpanded(group.id))"
            >
              <i class="pi" :class="isExpanded(group.id) ? 'pi-chevron-down' : 'pi-chevron-right'" />
            </button>
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
              {{ casesFor(group.id).length }} test case{{
                casesFor(group.id).length === 1 ? '' : 's'
              }}
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

        <DataTable
          :value="casesFor(group.id)"
          data-key="id"
          removable-sort
          :loading="loading"
          class="table list-table row-nav"
          @row-click="onCaseRowClick"
        >
          <template #empty>No test cases in this group yet.</template>
          <Column field="title" header="Title" sortable>
            <template #body="{ data }: { data: TestCase }">
              <RouterLink :to="`/test-cases/${data.id}`" class="name-link">{{ data.title }}</RouterLink>
            </template>
          </Column>
          <Column header="Prompt">
            <template #body="{ data }: { data: TestCase }">{{ promptRefFor(data) }}</template>
          </Column>
          <Column field="tool_mode" header="Tools" sortable>
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
    </template>

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
.view-toggle {
  margin-top: 0.75rem;
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  flex-shrink: 0;
}

.solo-row {
  display: flex;
  align-items: baseline;
  gap: 0.625rem;
}

.filter-text {
  font-size: 0.8125rem;
  color: var(--p-text-muted-color);
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

.group-title {
  display: flex;
  align-items: baseline;
  gap: 0.625rem;
  min-width: 0;
}

/* The disclosure chevron left of the name replaces Panel's built-in toggle
 * button, which can only render on the far right after the action buttons. */
.group-toggle {
  background: none;
  border: none;
  padding: 0.125rem;
  cursor: pointer;
  color: var(--p-text-muted-color);
  font-size: 0.8125rem;
  align-self: center;
}

.group-panel :deep(.p-panel-header .p-panel-toggle-button) {
  display: none;
}

.group-filter {
  min-width: 12rem;
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
</style>
