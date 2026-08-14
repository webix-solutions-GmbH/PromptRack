<script setup lang="ts">
// Run detail: header + live-streaming result list. Port of
// `git show master:src/components/runs/run-detail.tsx` (the NDJSON driver
// and its event-to-state patching) and `src/app/runs/[id]/page.tsx` (the
// header fields), renamed for the pivot's terminology and adding version
// attribution per spec §"Workflow & UI" ("run detail and results cells show
// which version a result tested").
//
// Deviation: the backend's run contract (`backend/app/api/runs.py`, Task
// 4.4) has no endpoint to edit a run's comment after creation — only
// archive/unarchive/delete/execute and the per-result rating patch exist —
// so `RunComment`'s edit UI has no port here; the comment renders read-only.
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useConfirm } from 'primevue/useconfirm'
import { useToast } from 'primevue/usetoast'
import Button from 'primevue/button'
import Message from 'primevue/message'
import Tag from 'primevue/tag'
import {
  executeRun,
  runsApi,
  RunAlreadyExecutingError,
  type RunEvent,
  type RunStatus,
  type RunResultView,
  type ToolCall,
  type TranscriptMessage,
} from '../api/runs'
import { ApiError } from '../api/client'
import { formatDateTime, formatDuration, formatParams, formatRate } from '../lib/format'
import { countRatings, type Rating } from '../lib/rating'
import { usePromptVersionLabels } from '../lib/promptVersionLabels'
import ResultRow from '../components/runs/ResultRow.vue'
import { useAuthStore } from '../stores/auth'

const props = defineProps<{ id: string }>()
const runId = computed(() => Number(props.id))

const auth = useAuthStore()
const router = useRouter()
const confirm = useConfirm()
const toast = useToast()

const endpointName = ref('(deleted endpoint)')
const baseUrl = ref<string | null>(null)
const cpu = ref<string | null>(null)
const ram = ref<string | null>(null)
const gpu = ref<string | null>(null)
const modelId = ref('')
const params = ref<Record<string, unknown> | null>(null)
const llmInfo = ref<{ server: string | null; version: string | null; details: Record<string, string> } | null>(
  null,
)
const comment = ref<string | null>(null)
const groupNames = ref<string[]>([])
const archivedAt = ref<string | null>(null)
const createdAt = ref<string | null>(null)

const results = ref<RunResultView[]>([])
const runStatus = ref<RunStatus>('pending')
const finishedAt = ref<string | null>(null)

const loading = ref(true)
const loadError = ref<string | null>(null)
const streamError = ref<string | null>(null)
const running = ref(false)
const busy = ref(false)

let abortController: AbortController | null = null
let autoStarted = false

// Both slots of every loaded row: a row can be attributed to one committed
// version in each, and the two prompts are versioned independently. Shared
// with the matrix's `CellDetail`/`MatrixTable` via the same module-level
// cache, and reactive to `results` so rows arriving mid-stream get resolved
// too.
const { versionLabel } = usePromptVersionLabels(() =>
  results.value.flatMap((row) => [row.system_prompt_version_id, row.task_prompt_version_id]),
)

async function load() {
  loading.value = true
  loadError.value = null
  try {
    const run = await runsApi.get(runId.value)
    endpointName.value = run.endpoint_snapshot?.name ?? '(deleted endpoint)'
    baseUrl.value = run.endpoint_snapshot?.base_url ?? null
    cpu.value = run.endpoint_snapshot?.cpu ?? null
    ram.value = run.endpoint_snapshot?.ram ?? null
    gpu.value = run.endpoint_snapshot?.gpu ?? null
    modelId.value = run.model_id
    params.value = run.params
    llmInfo.value = run.llm_info
    comment.value = run.comment
    groupNames.value = run.group_names
    archivedAt.value = run.archived_at
    createdAt.value = run.created_at
    results.value = run.results
    runStatus.value = run.status
    finishedAt.value = run.finished_at
  } catch (err) {
    loadError.value = err instanceof ApiError ? err.message : 'Failed to load the run.'
  } finally {
    loading.value = false
  }
}

function patchResult(id: number, patch: Partial<RunResultView>) {
  results.value = results.value.map((row) => (row.id === id ? { ...row, ...patch } : row))
}

type TranscriptPatch =
  | { kind: 'turnStart'; turn: number }
  | { kind: 'delta'; turn: number; text: string }
  | { kind: 'toolCall'; turn: number; calls: ToolCall[] }
  | { kind: 'toolResult'; message: TranscriptMessage }

/** Applies a live event to a tool run's growing transcript. Watching an
 * agent work is most of the value of a tool test, so the transcript is
 * assembled from the stream rather than waiting for the finished row; the
 * authoritative version replaces it on `resultDone`. */
function patchTranscript(
  current: TranscriptMessage[] | null,
  patch: TranscriptPatch,
): TranscriptMessage[] {
  const messages = current ? [...current] : []

  if (patch.kind === 'toolResult') {
    messages.push(patch.message)
    return messages
  }

  let index = messages.findIndex((message) => message.role === 'assistant' && message.turn === patch.turn)
  if (index === -1) {
    messages.push({ role: 'assistant', content: '', turn: patch.turn })
    index = messages.length - 1
  }

  if (patch.kind === 'delta') {
    messages[index] = { ...messages[index]!, content: patch.text }
  } else if (patch.kind === 'toolCall') {
    messages[index] = { ...messages[index]!, tool_calls: patch.calls }
  }
  return messages
}

function transcriptOf(id: number): TranscriptMessage[] | null {
  return results.value.find((row) => row.id === id)?.transcript ?? null
}

function applyEvent(event: RunEvent) {
  switch (event.type) {
    case 'runStart':
      runStatus.value = 'running'
      break
    case 'resultStart':
      patchResult(event.result_id, {
        status: 'running',
        response_text: '',
        error: null,
        transcript: null,
        turns: [],
        turn_count: null,
        tool_call_count: null,
        stopped_reason: null,
      })
      break
    case 'turnStart':
      patchResult(event.result_id, {
        response_text: '',
        transcript: patchTranscript(transcriptOf(event.result_id), {
          kind: 'turnStart',
          turn: event.turn,
        }),
      })
      break
    case 'delta':
      patchResult(event.result_id, {
        response_text: event.text,
        transcript:
          event.turn === undefined
            ? transcriptOf(event.result_id)
            : patchTranscript(transcriptOf(event.result_id), {
                kind: 'delta',
                turn: event.turn,
                text: event.text,
              }),
      })
      break
    case 'toolCall':
      patchResult(event.result_id, {
        transcript: patchTranscript(transcriptOf(event.result_id), {
          kind: 'toolCall',
          turn: event.turn,
          calls: event.calls,
        }),
      })
      break
    case 'toolResult':
      patchResult(event.result_id, {
        transcript: patchTranscript(transcriptOf(event.result_id), {
          kind: 'toolResult',
          message: event.message,
        }),
      })
      break
    case 'resultDone': {
      const current = results.value.find((row) => row.id === event.result_id)
      patchResult(event.result_id, {
        status: 'ok',
        response_text: event.text,
        error: null,
        duration_ms: event.metrics.duration_ms,
        ttft_ms: event.metrics.ttft_ms,
        prompt_tokens: event.metrics.prompt_tokens,
        completion_tokens: event.metrics.completion_tokens,
        tokens_per_sec: event.metrics.tokens_per_sec,
        tokens_estimated: event.metrics.tokens_estimated,
        turn_count: event.metrics.turn_count,
        tool_call_count: event.metrics.tool_call_count,
        // The finished row is authoritative; the live transcript was only
        // ever an approximation assembled from the stream.
        transcript: event.transcript ?? current?.transcript ?? null,
        turns: event.turns ?? current?.turns ?? null,
        stopped_reason: event.stopped_reason ?? current?.stopped_reason ?? null,
      })
      break
    }
    case 'resultError':
      patchResult(event.result_id, { status: 'error', error: event.error })
      break
    case 'aborted':
      if (event.result_id !== null) {
        patchResult(event.result_id, {
          status: 'pending',
          response_text: null,
          error: null,
          transcript: null,
          turns: [],
          turn_count: null,
          tool_call_count: null,
          stopped_reason: null,
        })
      }
      break
    case 'runDone':
      runStatus.value = event.status
      finishedAt.value = event.status === 'pending' ? null : new Date().toISOString()
      break
    case 'runError':
      streamError.value = event.error
      break
  }
}

async function start() {
  streamError.value = null
  running.value = true
  const controller = new AbortController()
  abortController = controller
  try {
    await executeRun(runId.value, applyEvent, controller.signal)
  } catch (err) {
    if (err instanceof RunAlreadyExecutingError) {
      streamError.value = err.message
    } else if (!(err instanceof DOMException && err.name === 'AbortError')) {
      streamError.value = err instanceof ApiError || err instanceof Error ? err.message : 'Execution failed.'
    }
  } finally {
    if (abortController === controller) abortController = null
    running.value = false
  }
}

function stop() {
  abortController?.abort()
}

async function loadAndMaybeStart() {
  abortController?.abort()
  autoStarted = false
  await load()
  if (!autoStarted && results.value.some((row) => row.status === 'pending')) {
    autoStarted = true
    void start()
  }
}

onMounted(loadAndMaybeStart)
watch(() => props.id, loadAndMaybeStart)
onBeforeUnmount(() => abortController?.abort())

function handleRatingChange(
  resultId: number,
  patch: { rating?: Rating | null; ratingNote?: string | null },
) {
  const values: Partial<RunResultView> = {}
  if ('rating' in patch) values.rating = patch.rating ?? null
  if ('ratingNote' in patch) values.rating_note = patch.ratingNote ?? null
  patchResult(resultId, values)
}

// --- archive / delete -------------------------------------------------

async function toggleArchive() {
  busy.value = true
  try {
    const updated = archivedAt.value !== null ? await runsApi.unarchive(runId.value) : await runsApi.archive(runId.value)
    archivedAt.value = updated.archived_at
  } catch (err) {
    toast.add({
      severity: 'error',
      summary: archivedAt.value !== null ? 'Failed to unarchive' : 'Failed to archive',
      detail: err instanceof ApiError ? err.message : undefined,
      life: 5000,
    })
  } finally {
    busy.value = false
  }
}

function confirmDelete() {
  confirm.require({
    header: 'Delete run',
    message: `Delete run #${runId.value} and all its results? This cannot be undone.`,
    acceptProps: { label: 'Delete', severity: 'danger' },
    rejectProps: { label: 'Cancel', text: true },
    accept: () => void removeRun(),
  })
}

async function removeRun() {
  busy.value = true
  try {
    await runsApi.remove(runId.value)
    await router.push('/runs')
  } catch (err) {
    toast.add({
      severity: 'error',
      summary: 'Failed to delete run',
      detail: err instanceof ApiError ? err.message : undefined,
      life: 5000,
    })
    busy.value = false
  }
}

// --- summary line -------------------------------------------------------

const statusSeverity: Record<RunStatus, 'secondary' | 'info' | 'success' | 'danger'> = {
  pending: 'secondary',
  running: 'info',
  completed: 'success',
  failed: 'danger',
}

const pendingCount = computed(() => results.value.filter((row) => row.status === 'pending').length)
// Rows stuck in 'running' while this tab is not driving are leftovers from a
// crashed process; the executor reclaims them as 'pending' on the next
// start, so offer Resume for them too (a live run in another tab answers
// with 409 instead).
const staleRunningCount = computed(() =>
  running.value ? 0 : results.value.filter((row) => row.status === 'running').length,
)
const resumableCount = computed(() => pendingCount.value + staleRunningCount.value)
const okCount = computed(() => results.value.filter((row) => row.status === 'ok').length)
const errorCount = computed(() => results.value.filter((row) => row.status === 'error').length)
const ratingTallies = computed(() => countRatings(results.value.map((row) => row.rating)))
const avgRate = computed(() => {
  const rates = results.value
    .map((row) => row.tokens_per_sec)
    .filter((rate): rate is number => typeof rate === 'number')
  return rates.length > 0 ? rates.reduce((total, rate) => total + rate, 0) / rates.length : null
})
const totalDuration = computed(() =>
  results.value.reduce((total, row) => total + (row.duration_ms ?? 0), 0),
)
</script>

<template>
  <div class="page">
    <Message v-if="loadError" severity="error" :closable="false">{{ loadError }}</Message>

    <template v-if="!loading">
      <section class="header-card">
        <div class="header-top">
          <div class="header-heading">
            <h1>
              Run #{{ runId }}
              <Tag :severity="statusSeverity[runStatus]" :value="runStatus" />
              <Tag v-if="archivedAt !== null" severity="warn" value="archived" />
            </h1>
            <p class="mono-line">{{ modelId }} @ {{ endpointName }}</p>
          </div>

          <div v-if="auth.canWrite" class="header-actions">
            <Button v-if="running" label="Stop" outlined @click="stop" />
            <template v-else>
              <Button
                v-if="resumableCount > 0"
                :label="`Resume (${resumableCount} pending)`"
                @click="start"
              />
              <Button
                :label="archivedAt !== null ? 'Unarchive run' : 'Archive run'"
                outlined
                :loading="busy"
                @click="toggleArchive"
              />
              <Button label="Delete run" outlined severity="danger" :loading="busy" @click="confirmDelete" />
            </template>
          </div>
        </div>

        <div class="field-grid">
          <div class="field">
            <span class="field-label">Base URL</span>
            <span class="field-value">{{ baseUrl ?? '—' }}</span>
          </div>
          <div class="field">
            <span class="field-label">CPU</span>
            <span class="field-value">{{ cpu ?? '—' }}</span>
          </div>
          <div class="field">
            <span class="field-label">RAM</span>
            <span class="field-value">{{ ram ?? '—' }}</span>
          </div>
          <div class="field">
            <span class="field-label">GPU</span>
            <span class="field-value">{{ gpu ?? '—' }}</span>
          </div>
          <div class="field">
            <span class="field-label">Groups</span>
            <span class="field-value">{{ groupNames.join(', ') || '—' }}</span>
          </div>
          <div class="field">
            <span class="field-label">Params</span>
            <span class="field-value">{{ formatParams(params) }}</span>
          </div>
          <div class="field">
            <span class="field-label">Created</span>
            <span class="field-value">{{ createdAt ? formatDateTime(createdAt) : '—' }}</span>
          </div>
          <div class="field">
            <span class="field-label">Finished</span>
            <span class="field-value">{{ finishedAt ? formatDateTime(finishedAt) : '—' }}</span>
          </div>
        </div>

        <details v-if="llmInfo && (llmInfo.server || Object.keys(llmInfo.details).length > 0)" class="llm-info">
          <summary>
            LLM info<template v-if="llmInfo.server">
              — {{ llmInfo.server }}{{ llmInfo.version ? ` ${llmInfo.version}` : '' }}</template
            >
          </summary>
          <div class="field-grid">
            <div v-for="(value, key) in llmInfo.details" :key="key" class="field">
              <span class="field-label">{{ key.replace(/_/g, ' ') }}</span>
              <span class="field-value">{{ value }}</span>
            </div>
          </div>
        </details>

        <div class="summary-line">
          <span>{{ okCount }} ok</span>
          <span>·</span>
          <span>{{ errorCount }} error</span>
          <span>·</span>
          <span>{{ pendingCount }} pending</span>
          <span>·</span>
          <span>{{ ratingTallies.good }} good</span>
          <span>·</span>
          <span>{{ ratingTallies.meh }} meh</span>
          <span>·</span>
          <span>{{ ratingTallies.bad }} bad</span>
          <span>·</span>
          <span>{{ ratingTallies.unrated }} unrated</span>
          <span>·</span>
          <span>avg {{ formatRate(avgRate) }}</span>
          <span>·</span>
          <span>total {{ formatDuration(totalDuration) }}</span>
        </div>

        <p class="comment-text">{{ comment && comment.length > 0 ? comment : 'No comment.' }}</p>

        <Message v-if="streamError" severity="error" :closable="false">{{ streamError }}</Message>
      </section>

      <section class="results">
        <ResultRow
          v-for="(result, index) in results"
          :key="result.id"
          :result="result"
          :index="index + 1"
          :can-write="auth.canWrite"
          :system-version-label="
            versionLabel(result.system_prompt_text, result.system_prompt_version_id)
          "
          :task-version-label="
            versionLabel(result.task_prompt_text, result.task_prompt_version_id)
          "
          @rating-change="(patch) => handleRatingChange(result.id, patch)"
        />
      </section>
    </template>
  </div>
</template>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  gap: 1.5rem;
}

.header-card {
  display: flex;
  flex-direction: column;
  gap: 1rem;
  padding: 1.5rem;
  border: 1px solid var(--p-content-border-color);
  border-radius: var(--p-content-border-radius);
}

.header-top {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-start;
  justify-content: space-between;
  gap: 1rem;
}

.header-heading h1 {
  display: flex;
  align-items: center;
  gap: 0.625rem;
  font-size: 1.5rem;
  font-weight: 600;
  margin: 0 0 0.25rem;
}

.mono-line {
  font-family: var(--p-font-family-mono, ui-monospace, monospace);
  font-size: 0.875rem;
  color: var(--p-text-muted-color);
  margin: 0;
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.field-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 1rem;
}

@media (min-width: 40rem) {
  .field-grid {
    grid-template-columns: repeat(4, 1fr);
  }
}

.field {
  display: flex;
  flex-direction: column;
  gap: 0.125rem;
  min-width: 0;
}

.field-label {
  font-size: 0.75rem;
  text-transform: uppercase;
  letter-spacing: 0.03em;
  color: var(--p-text-muted-color);
}

.field-value {
  font-size: 0.875rem;
  overflow-wrap: break-word;
}

.llm-info {
  border-top: 1px solid var(--p-content-border-color);
  padding-top: 1rem;
}

.llm-info summary {
  cursor: pointer;
  font-size: 0.75rem;
  font-weight: 500;
  text-transform: uppercase;
  letter-spacing: 0.03em;
  color: var(--p-text-muted-color);
}

.llm-info .field-grid {
  margin-top: 0.75rem;
}

.summary-line {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
  border-top: 1px solid var(--p-content-border-color);
  padding-top: 1rem;
  font-size: 0.75rem;
  color: var(--p-text-muted-color);
}

.comment-text {
  white-space: pre-wrap;
  font-size: 0.875rem;
  color: var(--p-text-color);
  margin: 0;
}

.results {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}
</style>
