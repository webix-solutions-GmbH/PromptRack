<script setup lang="ts">
// The comparison matrix itself: test cases as rows, one column per selected
// run (run mode) or model (model mode). Port of the old app's
// `compare-row.tsx` table body, back to that file's inline layout after the
// dialog it briefly used turned out to defeat the point of a matrix: a modal
// can only ever show one cell, so comparing two models' answers to the same
// test case meant opening, closing and remembering.
//
// Every row is open, always, and open means *fully*: a response renders at
// its natural height with no inner scrollport, so reading a long answer is
// one continuous scroll of the matrix rather than a hunt for which nested
// box the wheel is over. A verbose model therefore sets its own row's
// height — deliberately: with the case text, prompts, rubric and tools all
// reading as peeks, the row header carries only identity (group and title)
// and a row costs no height beyond the answers themselves.
//
// Open content is much wider than a clamped preview was, which is what
// `.cell-detail`'s min-width plus `.matrix-wrap`'s existing horizontal scroll
// is for: squeezing five columns into the viewport would make the side-by-side
// reading unreadable.
//
// Which is also why each fact is rendered exactly once, at the altitude it is
// true at: the column header names the model, the row header the group, the
// test case text, the drift note and the prompts and rubric the row's cells
// agree on, and the cell itself only what differs per cell (`CellDetail`).
// Four columns otherwise repeat one prompt and one drift note four times.
//
// Column headers differ by pivot (run mode names the run and its own
// good/meh/bad; model mode uses `columnTallies`, computed over the cells
// actually on screen rather than the whole run — see
// `backend/app/api/results.py`'s `_tallies`), so this component takes both
// column shapes and picks per `mode` rather than making the caller normalize
// them into one.
import { computed, nextTick, onBeforeUnmount, ref } from 'vue'
import { RouterLink } from 'vue-router'
import Dialog from 'primevue/dialog'
import Popover from 'primevue/popover'
import Tag from 'primevue/tag'
import { formatDateTime, formatDuration, formatRate, formatTokenLabel } from '../../lib/format'
import { usePromptVersionLabels } from '../../lib/promptVersionLabels'
import type { Rating } from '../../lib/rating'
import CellDetail from './CellDetail.vue'
import type {
  ColumnTally,
  CompareCellView,
  CompareMode,
  CompareRowView,
  CompareRunView,
  ModelColumnView,
} from '../../api/results'

const props = defineProps<{
  mode: CompareMode
  rows: CompareRowView[]
  runColumns: CompareRunView[]
  modelColumns: ModelColumnView[]
  columnTallies: ColumnTally[]
  canWrite: boolean
}>()

const emit = defineEmits<{
  ratingChange: [
    payload: { cell: CompareCellView; patch: { rating?: Rating | null; ratingNote?: string | null } },
  ]
}>()

interface ColumnHeader {
  key: string
  modelId: string
  endpointName: string
  subtitle: string
  good: number
  meh: number
  bad: number
  avgRate: number | null
  /** Sum of `duration_ms` over the column's cells — model generation time
   * only, shown beside the rate because a high tok/s can still be a slow
   * suite if the model over-reasons. */
  totalDurationMs: number | null
  runId: number | null
}

const columnHeaders = computed<ColumnHeader[]>(() => {
  if (props.mode === 'runs') {
    return props.runColumns.map((run) => ({
      key: String(run.id),
      modelId: run.model_id,
      endpointName: run.endpoint_name,
      subtitle: formatDateTime(run.created_at),
      good: run.good,
      meh: run.meh,
      bad: run.bad,
      avgRate: run.avg_rate,
      totalDurationMs: run.total_duration_ms,
      runId: run.id,
    }))
  }
  return props.modelColumns.map((column, index) => {
    const tally = props.columnTallies[index]
    return {
      key: column.key,
      modelId: column.model_id,
      endpointName: column.endpoint_name,
      subtitle: `${tally?.answered ?? 0}/${props.rows.length} answered · latest ${formatDateTime(column.latest_run_at)}`,
      good: tally?.good ?? 0,
      meh: tally?.meh ?? 0,
      bad: tally?.bad ?? 0,
      avgRate: tally?.avg_rate ?? null,
      totalDurationMs: tally?.total_duration_ms ?? null,
      runId: null,
    }
  })
})

/** Two voices in one list: `"<part> edited since"` compares this row against the
 * live test case, everything else compares the cells against each other. The
 * three text parts are named separately now ("system prompt", "task prompt",
 * "test case text"), so the old single-string special case would have missed
 * two of them. */
function driftLabel(drift: string[]): string {
  const edited = drift.filter((entry) => entry.endsWith('edited since'))
  const differs = drift.filter((entry) => !entry.endsWith('edited since'))
  const parts: string[] = []
  if (differs.length > 0) parts.push(`differs across cells: ${differs.join(', ')}`)
  if (edited.length > 0) parts.push(edited.join(', '))
  return parts.join(' · ')
}

// --- row-level prompt texts ----------------------------------------------

// A prompt slot is frozen per cell, but a row's cells hold the same text
// unless drift says otherwise — repeating it in every open cell of a
// four-column row shows the reader one prompt four times. So the row header
// carries it, and only a slot the cells genuinely disagree on goes back down
// into the cells (`CellDetail`'s `showSystemPrompt`/`showTaskPrompt`).

type Slot = 'system' | 'task'

interface SlotText {
  text: string | null
  versionId: number | null
}

/** A slot known to carry text, which is the only kind worth displaying. */
interface FilledSlot {
  text: string
  versionId: number | null
}

interface RowPrompts {
  /** The one copy the row header shows. Null both when the cells disagree —
   * `<slot>Differs` is then set — and when the whole row leaves the slot
   * empty, in which case nobody shows it: a disclosure that opens onto
   * "(none)" is not information. */
  system: FilledSlot | null
  task: FilledSlot | null
  systemDiffers: boolean
  taskDiffers: boolean
}

const NO_PROMPTS: RowPrompts = {
  system: null,
  task: null,
  systemDiffers: false,
  taskDiffers: false,
}

function slotOf(cell: CompareCellView, slot: Slot): SlotText {
  return slot === 'system'
    ? { text: cell.system_prompt_text, versionId: cell.system_prompt_version_id }
    : { text: cell.task_prompt_text, versionId: cell.task_prompt_version_id }
}

/** The version id is compared alongside the text, not only the text: two cells
 * can freeze identical text against different prompt assets, and one row-level
 * `v4` badge would then name a version only one of them ran. */
function classifySlot(
  cells: CompareCellView[],
  slot: Slot,
): { shared: FilledSlot | null; differs: boolean } {
  const first = slotOf(cells[0], slot)
  for (const cell of cells) {
    const other = slotOf(cell, slot)
    if (other.text !== first.text || other.versionId !== first.versionId) {
      return { shared: null, differs: true }
    }
  }
  const text = first.text
  if (text === null || text.trim().length === 0) return { shared: null, differs: false }
  return { shared: { text, versionId: first.versionId }, differs: false }
}

const rowPrompts = computed<Map<string, RowPrompts>>(() => {
  const map = new Map<string, RowPrompts>()
  for (const row of props.rows) {
    const cells = row.cells.filter((cell): cell is CompareCellView => cell !== null)
    if (cells.length === 0) {
      map.set(row.key, NO_PROMPTS)
      continue
    }
    const system = classifySlot(cells, 'system')
    const task = classifySlot(cells, 'task')
    map.set(row.key, {
      system: system.shared,
      task: task.shared,
      systemDiffers: system.differs,
      taskDiffers: task.differs,
    })
  }
  return map
})

function promptsFor(row: CompareRowView): RowPrompts {
  return rowPrompts.value.get(row.key) ?? NO_PROMPTS
}

// One lookup per row rather than one per cell is the point of moving the
// display up here: a version badge costs a request, and a row whose cells
// agree asks for its two ids once instead of once per column.
const { versionLabel } = usePromptVersionLabels(() =>
  props.rows.flatMap((row) => {
    const prompts = promptsFor(row)
    return [prompts.system?.versionId ?? null, prompts.task?.versionId ?? null]
  }),
)

/** The row's prompt texts in display order — an array so the peek content
 * renders both slots with one `v-for` instead of two near-identical
 * branches. */
function rowPromptBlocks(
  row: CompareRowView,
): { key: Slot; label: string; text: string; version: string | null }[] {
  const prompts = promptsFor(row)
  const blocks: { key: Slot; label: string; text: string; version: string | null }[] = []
  if (prompts.system !== null) {
    blocks.push({
      key: 'system',
      label: 'System prompt',
      text: prompts.system.text,
      version: versionLabel(prompts.system.text, prompts.system.versionId),
    })
  }
  if (prompts.task !== null) {
    blocks.push({
      key: 'task',
      label: 'Task prompt',
      text: prompts.task.text,
      version: versionLabel(prompts.task.text, prompts.task.versionId),
    })
  }
  return blocks
}

// --- prompt / rubric peeks ------------------------------------------------

// The prompts and the rubric used to open as `<details>` inside the 16rem
// sticky column, which could only ever render them as a few dozen characters
// per line crushed against the fixed row height. They are read on demand
// instead: hovering a peek button shows the text in a popover at reading
// width (the quick glance between two ratings), clicking pins the same
// content as a non-modal draggable dialog that survives scrolling the matrix
// and rating cells behind it. One popover and one dialog serve the whole
// table — only their content swaps per row.

interface PeekBlock {
  key: string
  /** Null when the peek holds a single text needing no sub-heading (the
   * rubric); the prompts peek labels its two slots, the tools peek each
   * tool. */
  label: string | null
  /** Rendered as a secondary Tag beside the label: a prompt's version, a
   * tool's toolset. */
  badge: string | null
  /** Verbatim-identifier labels (tool names) render mono and case-preserved
   * instead of the uppercase eyebrow the prose labels get. */
  mono?: boolean
  text: string
}

interface PeekContent {
  /** What the peek shows, for the dialog header: "Prompts" / "Expected output". */
  source: string
  caseTitle: string
  blocks: PeekBlock[]
}

function caseTextPeek(row: CompareRowView): PeekContent {
  return {
    source: 'Test case',
    caseTitle: row.test_case_title,
    // `content` is nullable — a task prompt can be the whole user message —
    // and the button hides on a null, so this never renders a placeholder.
    blocks: [{ key: 'case', label: null, badge: null, text: row.test_case_text ?? '' }],
  }
}

function promptsPeek(row: CompareRowView): PeekContent {
  return {
    source: 'Prompts',
    caseTitle: row.test_case_title,
    blocks: rowPromptBlocks(row).map((block) => ({
      key: block.key,
      label: block.label,
      badge: block.version,
      text: block.text,
    })),
  }
}

function expectedPeek(row: CompareRowView): PeekContent {
  return {
    source: 'Expected output',
    caseTitle: row.test_case_title,
    blocks: [{ key: 'expected', label: null, badge: null, text: row.expected_output ?? '' }],
  }
}

/** The parsed shape of `tools_snapshot` this peek reads — a projection of the
 * backend's `SnapshotTool` (see `frontend/src/api/runs.ts`), narrowed to what
 * the list shows. */
interface SnapshotToolLite {
  definition: { type: string; function?: { name?: string; description?: string } }
  toolset_name: string
}

interface RowTools {
  count: number
  content: PeekContent
}

/** The tools every cell of the row offered, lifted to the row the same way
 * the prompts are: only when the cells froze byte-identical snapshots — a
 * disagreeing row gets no pill, and `drift` already names "tools" there.
 * A computed Map rather than a per-call parse: the template asks per render,
 * and `JSON.parse` per row per render is the kind of cost that grows with the
 * matrix. */
const rowToolsMap = computed<Map<string, RowTools | null>>(() => {
  const map = new Map<string, RowTools | null>()
  for (const row of props.rows) {
    map.set(row.key, computeRowTools(row))
  }
  return map
})

function computeRowTools(row: CompareRowView): RowTools | null {
  const cells = row.cells.filter((cell): cell is CompareCellView => cell !== null)
  if (cells.length === 0) return null
  const first = cells[0].tools_snapshot
  if (!first) return null
  if (cells.some((cell) => cell.tools_snapshot !== first)) return null
  let parsed: unknown
  try {
    parsed = JSON.parse(first)
  } catch {
    return null
  }
  if (!Array.isArray(parsed) || parsed.length === 0) return null
  const tools = parsed as SnapshotToolLite[]
  return {
    count: tools.length,
    content: {
      source: 'Tools offered',
      caseTitle: row.test_case_title,
      blocks: tools.map((tool, index) => ({
        key: `tool-${index}`,
        label: tool.definition?.function?.name ?? tool.definition?.type ?? 'tool',
        badge: tool.toolset_name ?? null,
        mono: true,
        text: tool.definition?.function?.description?.trim() || '(no description)',
      })),
    },
  }
}

function toolsFor(row: CompareRowView): RowTools | null {
  return rowToolsMap.value.get(row.key) ?? null
}

// Null-guarded wrappers for the template: the button only renders when
// `toolsFor` is non-null, but the template's type checker cannot carry that
// narrowing from the `v-if` into the handlers.
function toolsLabel(row: CompareRowView): string {
  const tools = toolsFor(row)
  if (tools === null) return ''
  return `${tools.count} ${tools.count === 1 ? 'tool' : 'tools'}`
}

function enterTools(event: MouseEvent, row: CompareRowView) {
  const tools = toolsFor(row)
  if (tools !== null) peekEnter(event, tools.content)
}

function pinTools(row: CompareRowView) {
  const tools = toolsFor(row)
  if (tools !== null) pin(tools.content)
}

const peekPopover = ref<InstanceType<typeof Popover> | null>(null)
const hovered = ref<PeekContent | null>(null)
const pinned = ref<PeekContent | null>(null)
const pinnedVisible = ref(false)

// Two timers, deliberately asymmetric: the show delay keeps a cursor passing
// over the buttons from flashing popovers, the hide delay is the grace period
// that lets the cursor travel from the button into the popover to scroll a
// long text without the popover vanishing underneath it.
let showTimer: ReturnType<typeof setTimeout> | undefined
let hideTimer: ReturnType<typeof setTimeout> | undefined

function peekEnter(event: MouseEvent, content: PeekContent) {
  // Captured now: by the time the timer fires the event has been dispatched
  // and `currentTarget` is null, so `show` gets the element explicitly.
  const target = event.currentTarget as HTMLElement
  clearTimeout(hideTimer)
  clearTimeout(showTimer)
  showTimer = setTimeout(() => {
    hovered.value = content
    const popover = peekPopover.value
    if (popover === null) return
    // hide-then-show rather than show alone: `show` on an already-visible
    // popover updates nothing, so moving straight from one button to another
    // within the grace period would leave it aligned to the old button.
    popover.hide()
    void nextTick(() => popover.show(event, target))
  }, 150)
}

function peekLeave() {
  clearTimeout(showTimer)
  clearTimeout(hideTimer)
  hideTimer = setTimeout(() => peekPopover.value?.hide(), 200)
}

/** Entering the popover itself cancels the pending hide — see the grace
 * period above. */
function peekHoverHold() {
  clearTimeout(hideTimer)
}

function pin(content: PeekContent) {
  clearTimeout(showTimer)
  peekPopover.value?.hide()
  pinned.value = content
  pinnedVisible.value = true
}

onBeforeUnmount(() => {
  clearTimeout(showTimer)
  clearTimeout(hideTimer)
})

/** A cell carries no prompt-token count, so the shared formatter's two-sided
 * form never applies here — only the `~` an estimated count is marked with. */
function tokenLabel(cell: CompareCellView): string | null {
  const label = formatTokenLabel(null, cell.completion_tokens, cell.tokens_estimated)
  return label === null ? null : `${label} tok`
}
</script>

<template>
  <div class="matrix-wrap">
    <table class="matrix">
      <thead>
        <tr>
          <th class="row-header-cell">Test case</th>
          <th v-for="column in columnHeaders" :key="column.key" class="col-header-cell">
            <div class="col-header">
              <span class="mono model-id">{{ column.modelId }}</span>
              <span class="endpoint-name">@ {{ column.endpointName }}</span>
              <span class="col-sub">
                <RouterLink v-if="column.runId !== null" :to="`/runs/${column.runId}`"
                  >run #{{ column.runId }}</RouterLink
                >
                <span v-else>{{ column.subtitle }}</span>
              </span>
              <span class="col-sub rating-tallies">
                <span class="good-count">{{ column.good }}</span
                >/<span class="meh-count">{{ column.meh }}</span
                >/<span class="bad-count">{{ column.bad }}</span>
                <span class="rate">{{ formatRate(column.avgRate) }}</span>
                <span class="rate">{{ formatDuration(column.totalDurationMs) }}</span>
              </span>
            </div>
          </th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="row.key">
          <th scope="row" class="row-header-cell">
            <div class="row-header">
              <span class="row-group">
                {{ row.group_name }}<template v-if="row.test_case_id === null"> · deleted test case</template>
              </span>
              <span class="row-title">{{ row.test_case_title }}</span>
              <!--
                Everything the row references — its own case text, the
                prompts its cells share, and the rubric they're judged
                against — as peek buttons rather than inline text: hover for
                a glance, click to pin (see the peek section in the script).
                All three are reference material read occasionally, and
                rendering any of them inline was unreadable at any height in
                a sticky column narrow enough to leave room for the answers.
                Each button hides when it has nothing to show: a null
                `test_case_text` (a task prompt can be the whole user
                message), an empty prompt row, a row with no rubric — the
                last also covering the cells disagreeing about the rubric
                (the row's `expected_output` is null then and `drift` says
                "expected output"): showing one cell's rubric as the row's
                would be a claim about how the other cells were graded.
              -->
              <div
                v-if="
                  row.test_case_text ||
                  rowPromptBlocks(row).length > 0 ||
                  toolsFor(row) !== null ||
                  row.expected_output
                "
                class="row-peeks"
              >
                <button
                  v-if="row.test_case_text"
                  type="button"
                  class="peek-button"
                  @mouseenter="peekEnter($event, caseTextPeek(row))"
                  @mouseleave="peekLeave"
                  @click="pin(caseTextPeek(row))"
                >
                  <i class="pi pi-align-left" aria-hidden="true" />
                  Test case
                </button>
                <button
                  v-if="rowPromptBlocks(row).length > 0"
                  type="button"
                  class="peek-button"
                  @mouseenter="peekEnter($event, promptsPeek(row))"
                  @mouseleave="peekLeave"
                  @click="pin(promptsPeek(row))"
                >
                  <i class="pi pi-comment" aria-hidden="true" />
                  Prompts
                </button>
                <button
                  v-if="toolsFor(row) !== null"
                  type="button"
                  class="peek-button"
                  @mouseenter="enterTools($event, row)"
                  @mouseleave="peekLeave"
                  @click="pinTools(row)"
                >
                  <i class="pi pi-wrench" aria-hidden="true" />
                  {{ toolsLabel(row) }}
                </button>
                <button
                  v-if="row.expected_output"
                  type="button"
                  class="peek-button"
                  @mouseenter="peekEnter($event, expectedPeek(row))"
                  @mouseleave="peekLeave"
                  @click="pin(expectedPeek(row))"
                >
                  <i class="pi pi-check-circle" aria-hidden="true" />
                  Expected
                </button>
              </div>
              <p v-if="row.drift.length > 0" class="drift-note">{{ driftLabel(row.drift) }}</p>
            </div>
          </th>
          <td v-for="(cell, index) in row.cells" :key="index" class="cell">
            <span v-if="cell === null" class="empty-cell">—</span>
            <!-- A flex column filling the cell, purely so the metrics footer
                 can `margin-top: auto` to the bottom. The flexbox goes on this
                 wrapper rather than on the `<td>` itself: `display: flex` on a
                 table cell takes it out of the table formatting context, and
                 the column widths and sticky headers depend on it staying in. -->
            <div v-else class="cell-inner">
              <!-- No rating chip here: `RatingButtons` inside `CellDetail`
                   already shows the verdict on the control that sets it, and
                   a second read-only thumb beside it was the same fact twice.
                   Skipped entirely when it would be empty, since an empty flex
                   line still leaves a gap that reads as missing content. -->
              <span
                v-if="cell.status !== 'ok' || cell.turn_count !== null"
                class="cell-top"
              >
                <span v-if="cell.status !== 'ok'" class="cell-status">{{ cell.status }}</span>
                <span v-if="cell.turn_count !== null" class="cell-status"
                  >{{ cell.turn_count }}t / {{ cell.tool_call_count ?? 0 }} calls</span
                >
              </span>
              <div class="cell-detail">
                <CellDetail
                  :cell="cell"
                  :show-system-prompt="promptsFor(row).systemDiffers"
                  :show-task-prompt="promptsFor(row).taskDiffers"
                  :can-write="canWrite"
                  @rating-change="(patch) => emit('ratingChange', { cell, patch })"
                />
              </div>
              <!-- One footer, pinned to the bottom of every cell: which run
                   answered on the left, what the answer cost on the right.
                   They were two stacked rows at different heights per column,
                   which is what made comparing two models' numbers a hunt.
                   Provenance lives here rather than in `CellDetail` so both
                   halves can share the line. -->
              <div class="cell-footer">
                <span class="cell-provenance">
                  <RouterLink :to="`/runs/${cell.run_id}`">run #{{ cell.run_id }}</RouterLink>
                  <span>·</span>
                  <span>{{ formatDateTime(cell.run_created_at) }}</span>
                  <span v-if="cell.superseded" class="superseded">
                    newer attempt ({{ cell.superseded.status }}) in run
                    #{{ cell.superseded.run_id }} skipped
                  </span>
                </span>
                <span class="cell-metrics">
                  <span class="cell-metric">{{ formatRate(cell.tokens_per_sec) }}</span>
                  <span class="cell-metric">{{ formatDuration(cell.duration_ms) }}</span>
                  <span class="cell-metric">ttft {{ formatDuration(cell.ttft_ms) }}</span>
                  <span v-if="tokenLabel(cell)" class="cell-metric">{{ tokenLabel(cell) }}</span>
                </span>
              </div>
            </div>
          </td>
        </tr>
      </tbody>
    </table>
    <!-- Both teleport to <body>, so their inner styling lives in the
         unscoped style block below. The hover handlers sit on the content
         div rather than the component: the overlay root is teleported and
         only the markup here is guaranteed to receive them. -->
    <Popover ref="peekPopover" class="matrix-peek">
      <div
        v-if="hovered"
        class="peek-content peek-scroll"
        @mouseenter="peekHoverHold"
        @mouseleave="peekLeave"
      >
        <div v-for="block in hovered.blocks" :key="block.key" class="peek-block">
          <span v-if="block.label" class="peek-block-label" :class="{ mono: block.mono }">
            {{ block.label }}
            <Tag v-if="block.badge" severity="secondary" :value="block.badge" />
          </span>
          <p class="peek-text">{{ block.text }}</p>
        </div>
      </div>
    </Popover>
    <!-- Deliberately non-modal (PrimeVue's default): the whole point of
         pinning is rating cells against the rubric, so the matrix behind it
         must stay scrollable and clickable, and the dialog dragged wherever
         it isn't in the way. -->
    <Dialog
      v-model:visible="pinnedVisible"
      class="matrix-peek-dialog"
      :header="pinned ? `${pinned.source} — ${pinned.caseTitle}` : ''"
    >
      <div v-if="pinned" class="peek-content">
        <div v-for="block in pinned.blocks" :key="block.key" class="peek-block">
          <span v-if="block.label" class="peek-block-label" :class="{ mono: block.mono }">
            {{ block.label }}
            <Tag v-if="block.badge" severity="secondary" :value="block.badge" />
          </span>
          <p class="peek-text">{{ block.text }}</p>
        </div>
      </div>
    </Dialog>
  </div>
</template>

<style scoped>
/* The scrollport for *both* axes, which is what makes the sticky headers
   below work at all: `overflow-x: auto` alone already forced `overflow-y` to
   `auto`, so a top-sticky `<thead>` was constrained to this box rather than to
   the viewport — and a box that grows with its content never scrolls, so
   nothing would ever have stuck. Bounding the height moves the vertical
   scrolling in here, where the constraint applies.

   The height is the viewport less the pinned topbar (3.5rem in `AppLayout`)
   and the content column's bottom padding (1.5rem): with nothing below the
   matrix on the page, scrolling all the way down then lands the table's top
   edge exactly under the topbar rather than behind it. */
.matrix-wrap {
  overflow: auto;
  max-height: calc(100vh - 5rem);
  border: 1px solid var(--p-content-border-color);
  border-radius: var(--p-content-border-radius);
}

.matrix {
  width: 100%;
  min-width: max-content;
  border-collapse: collapse;
  text-align: left;
  font-size: 0.8125rem;
}

thead {
  background: var(--p-content-hover-background);
}

/* Four sticky layers, and all four have to be ordered explicitly or each one
   is overlapped by whatever scrolls past it: the corner over the header row,
   the header row over the body's row headers, those over the cells.
   A sticky cell also needs an opaque background of its own — the surface it
   sits on is the table's, which scrolls away underneath it — and the
   `box-shadow` insets stand in for the borders: with `border-collapse:
   collapse` the collapsed borders belong to the table rather than to the cell,
   so they scroll out from under a stuck cell while the shadow does not. */
.row-header-cell {
  position: sticky;
  left: 0;
  z-index: 2;
  /* Narrow on purpose: the header holds only group, title, peek chips and
     the drift note — every longer text opens as a peek — so its width is
     screen the answer columns get to keep. The chips stack vertically at
     this width, which reads as a tidy list rather than a squeezed row. */
  width: 10rem;
  min-width: 10rem;
  max-width: 10rem;
  border-right: 1px solid var(--p-content-border-color);
  border-bottom: 1px solid var(--p-content-border-color);
  box-shadow: inset -1px 0 0 var(--p-content-border-color);
  background: var(--p-content-background);
  /* Zero-padding cell, padding on the inner `.row-header` — the same split
     `.cell`/`.cell-inner` use, so both columns own their full box. */
  padding: 0;
  vertical-align: top;
  font-weight: 400;
}

thead .row-header-cell {
  top: 0;
  z-index: 4;
  /* The corner cell holds bare text, not a `.row-header`, so it takes the
     padding the body header cells delegate to their inner box. */
  padding: 0.75rem 1rem;
  box-shadow:
    inset -1px 0 0 var(--p-content-border-color),
    inset 0 -1px 0 var(--p-content-border-color);
  background: var(--p-content-hover-background);
  font-size: 0.6875rem;
  text-transform: uppercase;
  letter-spacing: 0.03em;
  color: var(--p-text-muted-color);
}

.col-header-cell {
  position: sticky;
  top: 0;
  z-index: 3;
  width: 22rem;
  min-width: 20rem;
  max-width: 26rem;
  border-left: 1px solid var(--p-content-border-color);
  border-bottom: 1px solid var(--p-content-border-color);
  box-shadow: inset 0 -1px 0 var(--p-content-border-color);
  /* `thead`'s own background is painted by the row, which the header cells
     leave behind as soon as they stick. */
  background: var(--p-content-hover-background);
  padding: 0.75rem 1rem;
  vertical-align: top;
}

.col-header {
  display: flex;
  flex-direction: column;
  gap: 0.125rem;
}

.mono {
  font-family: var(--p-font-family-mono, ui-monospace, monospace);
}

.model-id {
  font-size: 0.75rem;
  font-weight: 600;
}

.endpoint-name {
  font-size: 0.75rem;
  color: var(--p-text-muted-color);
}

.col-sub {
  font-size: 0.6875rem;
  color: var(--p-text-muted-color);
}

.rating-tallies {
  display: flex;
  align-items: center;
  gap: 0.25rem;
}

.good-count {
  color: var(--p-green-600, var(--p-green-500));
}

.meh-count {
  color: var(--p-yellow-600, var(--p-yellow-500));
}

.bad-count {
  color: var(--p-red-600, var(--p-red-500));
}

.rate {
  margin-left: 0.25rem;
}

.row-header {
  padding: 0.75rem 1rem;
  display: flex;
  flex-direction: column;
  gap: 0.1875rem;
}

.row-group {
  font-size: 0.6875rem;
  text-transform: uppercase;
  letter-spacing: 0.03em;
  color: var(--p-text-muted-color);
}

.row-title {
  font-size: 0.8125rem;
  font-weight: 600;
  color: var(--p-text-color);
}

.row-peeks {
  display: flex;
  flex-wrap: wrap;
  gap: 0.25rem;
  margin-top: 0.25rem;
}

/* Quiet pill chips in the app's own component vocabulary — bordered like a
   PrimeVue outlined control, muted until hovered: up to three per row of a
   long matrix, they must read as affordances without competing with the
   answers they annotate. */
.peek-button {
  display: inline-flex;
  align-items: center;
  gap: 0.3125rem;
  padding: 0.1875rem 0.625rem;
  border: 1px solid var(--p-content-border-color);
  border-radius: 999px;
  background: transparent;
  font: inherit;
  font-size: 0.6875rem;
  font-weight: 500;
  color: var(--p-text-muted-color);
  cursor: pointer;
  white-space: nowrap;
  transition:
    background-color 0.15s,
    border-color 0.15s,
    color 0.15s;
}

.peek-button .pi {
  font-size: 0.625rem;
}

.peek-button:hover {
  background: var(--p-content-hover-background);
  border-color: var(--p-text-muted-color);
  color: var(--p-text-color);
}

.peek-button:focus-visible {
  outline: 1px solid var(--p-primary-color);
  outline-offset: 1px;
}

.drift-note {
  margin: 0;
  padding: 0.25rem 0.5rem;
  border: 1px solid var(--p-yellow-300, var(--p-yellow-500));
  border-radius: var(--p-content-border-radius);
  background: var(--p-yellow-50, transparent);
  color: var(--p-yellow-700, var(--p-yellow-600));
  font-size: 0.6875rem;
}

.cell {
  border-left: 1px solid var(--p-content-border-color);
  border-bottom: 1px solid var(--p-content-border-color);
  padding: 0;
  vertical-align: top;
  /* The containing block for the pinned `.cell-footer`. A cell's box is
     always the full row height in table layout (the borders already draw
     that), which is exactly the definite height the content-sized
     `.cell-inner` cannot pass down — the `height: 1px` + `height: 100%`
     td hack resolves in Chrome but collapses every row in Firefox, so the
     footer escapes the flow entirely instead of asking flex for spare
     height. */
  position: relative;
}

/* Content-sized (top-of-file comment): the tallest cell sets the row, and a
 * flex column just stacks status line and detail. The bottom padding
 * reserves the absolutely-pinned footer's band so the tallest cell's own
 * content never runs under it — sized to the footer's one-line height,
 * which its `nowrap` metrics keep it at down to `.cell-detail`'s minimum
 * width. */
.cell-inner {
  display: flex;
  flex-direction: column;
  padding-bottom: 2.5rem;
}

.empty-cell {
  display: block;
  padding: 0.75rem 1rem;
  color: var(--p-text-muted-color);
}

.cell-top {
  display: flex;
  align-items: center;
  gap: 0.375rem;
  flex-wrap: wrap;
  padding: 0.75rem 1rem 0;
}

.cell-status {
  font-size: 0.6875rem;
  font-weight: 500;
  color: var(--p-text-muted-color);
}

/* Pinned to the row's bottom edge by absolute position against the `.cell`
 * (see there for why not flex), so the numbers read straight across the
 * columns whatever each cell's content height is. `.cell-inner`'s bottom
 * padding reserves this band. */
.cell-footer {
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 0.5rem;
  padding: 0.5rem 1rem 0.75rem;
}

.cell-provenance {
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: 0.25rem;
  font-size: 0.6875rem;
  color: var(--p-text-muted-color);
}

.cell-metrics {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
}

/* Moved here with the provenance line it annotates: model mode can skip a
   newer failed attempt in favour of an older good one, and the cell has to
   say so rather than look like the latest word. */
.superseded {
  color: var(--p-orange-600, var(--p-orange-500));
}

.cell-metric {
  font-size: 0.6875rem;
  color: var(--p-text-muted-color);
}

/* The detail was sized for a dialog; in a `<td>` with four or five columns it
   would be squeezed to a ribbon, so it keeps a floor of its own and
   `.matrix-wrap` scrolls sideways instead. Content-sized like everything in
   the cell — the flex-chain-to-a-scrollport plumbing this used to head went
   with the fixed row height. */
.cell-detail {
  min-width: 24rem;
  border-top: 1px solid var(--p-content-border-color);
  padding: 0.75rem 1rem;
}
</style>

<style>
/* The peek popover and dialog teleport to <body>, out of reach of the scoped
   block above, so their sizing and text styles live here, under the two
   classes the template hands them. */
.matrix-peek.p-popover {
  max-width: 36rem;
}

.matrix-peek-dialog {
  width: min(48rem, 90vw);
}

/* The popover is a glance, so it caps its own height and scrolls; the pinned
   dialog leaves scrolling to PrimeVue's own `.p-dialog-content`. */
.matrix-peek .peek-scroll {
  max-height: 24rem;
  overflow: auto;
}

.peek-content {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.peek-block {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}

.peek-block-label {
  display: flex;
  align-items: center;
  gap: 0.375rem;
  font-size: 0.6875rem;
  text-transform: uppercase;
  letter-spacing: 0.03em;
  color: var(--p-text-muted-color);
}

/* Tool names are identifiers: shown verbatim, never uppercased into a
   different name. */
.peek-block-label.mono {
  text-transform: none;
  letter-spacing: 0;
  font-family: var(--p-font-family-mono, ui-monospace, monospace);
  font-weight: 600;
  color: var(--p-text-color);
}

/* Full text color and a step up from the matrix's 0.6875rem row text: these
   two surfaces exist to be read, not scanned. */
.peek-text {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
  font-family: var(--p-font-family-mono, ui-monospace, monospace);
  font-size: 0.75rem;
  line-height: 1.55;
  color: var(--p-text-color);
}
</style>
