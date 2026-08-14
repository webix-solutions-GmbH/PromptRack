<script setup lang="ts">
// The comparison matrix itself: test cases as rows, one column per selected
// run (run mode) or model (model mode). Port of the old app's
// `compare-row.tsx` table body, back to that file's inline layout after the
// dialog it briefly used turned out to defeat the point of a matrix: a modal
// can only ever show one cell, so comparing two models' answers to the same
// test case meant opening, closing and remembering.
//
// Every row is open, always. A per-row expand/collapse was the same defeat one
// step smaller — a reader who has to click a row before it says anything is
// still reading one row at a time. What keeps an always-open row scannable
// instead is a cap on the two texts that can run to hundreds of lines (the
// test case text here, the response in `CellDetail`), each scrolling in its
// own box so one verbose model cannot set the height of the whole row. Only
// those texts scroll: the rating buttons, metrics and provenance have to be
// reachable without scrolling anything.
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
import { computed } from 'vue'
import { RouterLink } from 'vue-router'
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

/** The row header's prompt disclosures, in display order — an array so the
 * template renders both slots with one `v-for` instead of two near-identical
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
              <!-- `content` is nullable: a task prompt can be the whole user
                   message on its own. -->
              <p class="row-text row-case-text">{{ row.test_case_text ?? '(no content)' }}</p>
              <!--
                The prompts the whole row shares, beside the test case text
                they were sent with. Still behind a disclosure, unlike the test
                case text: a row is identified by its case, while the prompts
                are the same in every row of a suite and would push the cells
                they explain off the screen.
              -->
              <details v-for="block in rowPromptBlocks(row)" :key="block.key" class="row-details">
                <summary>
                  {{ block.label }}
                  <Tag v-if="block.version" severity="secondary" :value="block.version" />
                </summary>
                <p class="row-text row-prompt-text">{{ block.text }}</p>
              </details>
              <!--
                The rubric, in the sticky column on purpose: it is what every
                cell of the row is being judged against, so it has to stay on
                screen while the reader scrolls sideways through the answers —
                a right-hand column would slide out of view exactly when the
                comparison is happening. Absent when the row has no rubric, and
                absent when the cells disagree about it (the row's
                `expected_output` is null then and `drift` says "expected
                output"): showing one cell's rubric as the row's would be a
                claim about how the other cells were graded.
              -->
              <details v-if="row.expected_output" class="row-details">
                <summary>Expected output</summary>
                <p class="row-text row-expected-text">{{ row.expected_output }}</p>
              </details>
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
  /* One height for every row, read by `.cell-inner` and `.row-header` alike so
     the two columns cannot drift apart. Roughly a third of a 1080p viewport:
     enough that a typical answer needs no scrolling, small enough that three
     rows are comparable at once. */
  --matrix-row-height: 26rem;
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
  width: 16rem;
  min-width: 16rem;
  max-width: 16rem;
  border-right: 1px solid var(--p-content-border-color);
  border-bottom: 1px solid var(--p-content-border-color);
  box-shadow: inset -1px 0 0 var(--p-content-border-color);
  background: var(--p-content-background);
  /* No padding on the cell: the header column and the body cells must resolve
     to exactly the same total height, or the taller one stretches the row and
     the other ends short of its bottom edge. Padding here sat *around* the
     26rem `.row-header`, making this column 1.5rem taller than the 26rem
     `.cell-inner` in a zero-padding `.cell` — the gap under every cell footer.
     The padding lives on `.row-header` instead, inside its border-box height. */
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
  height: var(--matrix-row-height);
  /* The cell's padding, carried here so `border-box` counts it inside the row
     height — see `.row-header-cell` for the equal-height invariant. */
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

/* No `display: flex`, same as `CellDetail`'s prompt disclosures: flex drops the
   native triangle in WebKit/Blink, and these summaries no longer carry a
   chevron of their own to stand in for it. */
.row-details summary {
  cursor: pointer;
  font-size: 0.6875rem;
  color: var(--p-text-muted-color);
}

.row-text {
  margin: 0.25rem 0 0;
  white-space: pre-wrap;
  word-break: break-word;
  font-family: var(--p-font-family-mono, ui-monospace, monospace);
  font-size: 0.6875rem;
  color: var(--p-text-muted-color);
}

/* Both texts scroll rather than growing the row: with every row open, the
   longest test case or prompt in the matrix would otherwise decide how many
   rows fit on a screen. `max-height`, not `height`, so a one-line test case
   still takes one line. The caps are deliberately different — a prompt is read
   in full when it is opened at all, a test case is scanned to recognise the
   row, and the response in `CellDetail` gets more room than either because
   comparing responses is what the view is for. */
/* The only flexible child of `.row-header`, so it absorbs whatever the group,
 * title, prompts and drift note leave of the fixed row height. `min-height: 0`
 * over the flex default of `auto`, whose floor is the text's own height — with
 * `auto` this would overflow the row rather than scroll inside it. */
.row-case-text {
  flex: 1 1 auto;
  min-height: 0;
  overflow: auto;
}

/* The rubric is read the same way an opened prompt is — in full, once — so it
   takes the same cap rather than one of its own. */
.row-prompt-text,
.row-expected-text {
  max-height: 14rem;
  overflow: auto;
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
}

/* An explicit height, deliberately, not a percentage. Filling the cell needs a
 * *definite* height to distribute, and a table cell cannot supply one: on a
 * content-sized row `height: 100%` collapses to `auto` and does nothing, while
 * `height: 1px` makes this box 1px — and as the cell's only child it then stops
 * forcing the row's height at all, collapsing every row. Naming the height
 * outright sidesteps the whole question, and a comparison matrix wants uniform
 * rows anyway: scanning down a column is only meaningful if the rows line up.
 *
 * The cost is accepted, not overlooked — a row whose answers are all short
 * still occupies `--matrix-row-height`. */
.cell-inner {
  height: var(--matrix-row-height);
  display: flex;
  flex-direction: column;
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

/* Pinned to the bottom edge two ways over, and both are load-bearing:
 * `.cell-detail` above grows into the spare height, and `margin-top: auto`
 * here takes any remainder — so the line sits on the cell's floor whether the
 * answer overflows its scrollport or is a single word. That is what lets the
 * numbers be compared straight across columns.
 *
 * No `border-top`: the scrollport above already ends on its own edge, and a
 * second rule a few pixels under it just read as a stray line. */
.cell-footer {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 0.5rem;
  margin-top: auto;
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
   `.matrix-wrap` scrolls sideways instead. */
/* Takes the height left between the status line and the footer, but is *not*
 * the scrollport — as one it scrolled the rating buttons and the "Response"
 * label along with the answer, and those have to stay reachable without
 * scrolling anything. It is instead the top link of a flex chain that hands
 * the leftover height down to `CellDetail`'s `.answer-text`, which scrolls
 * alone. Every link needs `min-height: 0`: a flex item's default floor is its
 * own content, which would turn "scrolls" back into "overflows". */
.cell-detail {
  flex: 1 1 auto;
  min-height: 0;
  display: flex;
  flex-direction: column;
  min-width: 24rem;
  border-top: 1px solid var(--p-content-border-color);
  padding: 0.75rem 1rem;
}
</style>
