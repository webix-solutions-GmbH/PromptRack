<script setup lang="ts">
// Dashboard — the landing page for the active workspace. Three things: counts
// to orient a fresh session, recent runs for "what happened last", and the
// pivot's headline signal — which deployed prompts are running something
// other than what was last verified (spec §"Deployed signal").
//
// There is no dedicated stats endpoint, so this composes three reads already
// used elsewhere:
// `GET /customers` (the same call `AppLayout`'s workspace switcher makes,
// which is where its embedded `counts` come from), `GET /runs` and
// `GET /prompts`. `describeVersionStatus` is the exact sentence
// `PromptsView`/`PromptEditView` already show for one prompt — reused rather
// than re-derived so the wording never disagrees with itself.
import { computed, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import Message from 'primevue/message'
import Tag from 'primevue/tag'
import { customersApi, type CustomerCounts } from '../api/customers'
import { describeVersionStatus, promptsApi, type Prompt } from '../api/prompts'
import { runsApi, type RunView } from '../api/runs'
import { ApiError } from '../api/client'
import { endpointLabel, formatDateTime } from '../lib/format'
import { RUN_STATUS_SEVERITY } from '../lib/runStatus'
import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()

const counts = ref<CustomerCounts | null>(null)
const recentRuns = ref<RunView[]>([])
const driftedPrompts = ref<Prompt[]>([])
const loading = ref(true)
const loadError = ref<string | null>(null)

/** A prompt whose deployed version is not its head — "is what's live at the
 * customer what we last verified" per spec §"Deployed signal". A prompt with
 * an unstaged draft but nothing deployed yet is ordinary editing-in-progress,
 * not a warning, so `dirty` alone does not qualify it for this list. */
function isDrifted(prompt: Prompt): boolean {
  return (
    prompt.deployed_version !== null &&
    prompt.head_version !== null &&
    prompt.deployed_version.version !== prompt.head_version.version
  )
}

async function load() {
  loading.value = true
  loadError.value = null
  try {
    const [customers, runs, prompts] = await Promise.all([
      customersApi.list(),
      runsApi.list({ archived: 'exclude', limit: 5 }),
      promptsApi.list(),
    ])
    counts.value = customers.find((customer) => customer.id === auth.activeCustomer?.id)?.content ?? null
    recentRuns.value = runs
    driftedPrompts.value = prompts.filter(isDrifted)
  } catch (err) {
    loadError.value = err instanceof ApiError ? err.message : 'Failed to load the dashboard.'
  } finally {
    loading.value = false
  }
}

onMounted(load)

const countTiles = computed(() => {
  const c = counts.value
  if (!c) return []
  return [
    { label: 'Prompts', value: c.prompts, to: '/prompts' },
    { label: 'Test groups', value: c.test_groups, to: '/test-cases' },
    { label: 'Toolsets', value: c.toolsets, to: '/toolsets' },
    { label: 'Endpoints', value: c.endpoints, to: '/endpoints' },
    { label: 'Runs', value: c.runs, to: '/runs' },
  ]
})
</script>

<template>
  <div class="page">
    <h1>Dashboard</h1>

    <Message v-if="loadError" severity="error" :closable="false">{{ loadError }}</Message>

    <section v-if="countTiles.length > 0" class="counts-row">
      <RouterLink v-for="tile in countTiles" :key="tile.label" :to="tile.to" class="count-tile">
        <span class="count-value">{{ tile.value }}</span>
        <span class="count-label">{{ tile.label }}</span>
      </RouterLink>
    </section>

    <section class="two-col">
      <div class="panel">
        <h2>Deployed ≠ head</h2>
        <p v-if="!loading && driftedPrompts.length === 0" class="empty-state">
          Every deployed prompt matches what was last verified.
        </p>
        <ul v-else class="entry-list">
          <li v-for="prompt in driftedPrompts" :key="prompt.id" class="entry">
            <RouterLink :to="`/prompts/${prompt.id}`" class="entry-name">{{ prompt.name }}</RouterLink>
            <span class="entry-detail">{{ describeVersionStatus(prompt) }}</span>
          </li>
        </ul>
      </div>

      <div class="panel">
        <h2>Recent runs</h2>
        <p v-if="!loading && recentRuns.length === 0" class="empty-state">
          No runs yet — start one from <RouterLink to="/runs/new">New run</RouterLink>.
        </p>
        <ul v-else class="entry-list">
          <li v-for="run in recentRuns" :key="run.id" class="entry run-entry">
            <RouterLink :to="`/runs/${run.id}`" class="entry-name">#{{ run.id }}</RouterLink>
            <span class="mono">{{ run.model_id }}</span>
            <span class="entry-detail">@ {{ endpointLabel(run.endpoint_snapshot?.name) }}</span>
            <Tag :severity="RUN_STATUS_SEVERITY[run.status]" :value="run.status" />
            <span class="entry-detail">{{ formatDateTime(run.created_at) }}</span>
          </li>
        </ul>
      </div>
    </section>
  </div>
</template>

<style scoped>
h1 {
  font-size: 1.5rem;
  font-weight: 600;
  margin: 0;
}

.counts-row {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 0.75rem;
}

@media (min-width: 40rem) {
  .counts-row {
    grid-template-columns: repeat(5, 1fr);
  }
}

.count-tile {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  padding: 1rem;
  border: 1px solid var(--p-content-border-color);
  border-radius: var(--p-content-border-radius);
  text-decoration: none;
  color: inherit;
}

.count-tile:hover {
  background: var(--p-content-hover-background);
}

.count-value {
  font-size: 1.5rem;
  font-weight: 600;
}

.count-label {
  font-size: 0.75rem;
  color: var(--p-text-muted-color);
}

.two-col {
  display: grid;
  grid-template-columns: 1fr;
  gap: 1rem;
}

@media (min-width: 56rem) {
  .two-col {
    grid-template-columns: 1fr 1fr;
  }
}

.entry-list {
  display: flex;
  flex-direction: column;
  gap: 0.625rem;
  margin: 0;
  padding: 0;
  list-style: none;
}

.entry {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.8125rem;
}

.entry-name {
  font-weight: 500;
  color: var(--p-text-color);
}

.entry-detail {
  color: var(--p-text-muted-color);
}
</style>
