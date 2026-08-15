<script setup lang="ts">
// Row-based editor for a request-body param dict — the free JSON object that
// `backend/app/services/params.py` validates and `llm.py` merges into the
// request verbatim. `paramCatalog.ts` supplies per-platform name/type/description
// suggestions; a caller may type any key at all, so the AutoComplete is a
// suggestion list, never a whitelist.
//
// Two modes, chosen by whether the `defaults` prop is passed at all:
//
//   Endpoint mode (`defaults` omitted) — `modelValue` is the whole dict. Rows
//   initialize from it and the emitted value is every valid row, or `null` when
//   there are none.
//
//   Run mode (`defaults` passed, `null` included) — rows initialize from the
//   endpoint's `default_params`, and `modelValue` is the *override* dict alone:
//   a key not in the defaults or holding a different value is emitted with its
//   value, a default row the user **removed** is emitted as `null` (the unset
//   signal `merge_params` drops after merging), and an untouched default is
//   omitted entirely. Empty override set → `null`. A parent selecting run mode
//   must bind `null` rather than `undefined` when it has no defaults yet, since
//   `undefined` is indistinguishable from an unbound prop.
//
// Clearing a row's *value* is not the unset signal — only removing the row is.
// A row whose value is blank or whose JSON does not parse simply contributes
// nothing, which in run mode leaves the endpoint default standing.
import { computed, ref, watch } from 'vue'
import AutoComplete from 'primevue/autocomplete'
import Button from 'primevue/button'
import InputNumber from 'primevue/inputnumber'
import InputText from 'primevue/inputtext'
import Select from 'primevue/select'
import Tag from 'primevue/tag'
import Textarea from 'primevue/textarea'
import ToggleSwitch from 'primevue/toggleswitch'
import type { EndpointPlatform } from '../api/endpoints'
import { PARAM_CATALOG, RESERVED_PARAM_KEYS, type CatalogParam, type ParamType } from '../lib/paramCatalog'

const props = defineProps<{
  modelValue: Record<string, unknown> | null
  platform: EndpointPlatform
  /** Pass (even as `null`) to select run mode — see the note above. */
  defaults?: Record<string, unknown> | null
  disabled?: boolean
}>()

const emit = defineEmits<{ 'update:modelValue': [Record<string, unknown> | null] }>()

const isRunMode = computed(() => props.defaults !== undefined)

/** The type control offers no `enum`: an enum row is one whose key the catalog
 * knows, and only the catalog can supply the values to choose between. */
const CUSTOM_TYPES: ParamType[] = ['string', 'number', 'boolean', 'json']

/** One row's state. Each widget keeps its own field rather than sharing an
 * `unknown`, so switching a row's type neither needs a cast in the template nor
 * throws away what the other widget held. `rowValue` reads whichever the row's
 * current type names. */
interface Row {
  id: number
  key: string
  type: ParamType
  num: number | null
  bool: boolean
  /** Backs both `string` and `enum`. */
  str: string
  /** `json` rows keep the raw text; `parsed` only catches up on blur. */
  raw: string
  parsed: unknown
  jsonError: boolean
  /** Came from the endpoint's defaults (run mode only) — drives the tag and
   * nothing else; the emitted value is decided by comparison, not by this. */
  fromDefault: boolean
}

// --- catalog lookup ------------------------------------------------------
//
// Declared ahead of the rows because building the initial rows already reads
// the catalog, to tell an enum key from a plain string one.

const catalog = computed(() => PARAM_CATALOG[props.platform])

const catalogByName = computed(
  () => new Map(catalog.value.params.map((param) => [param.name, param])),
)

/** Reactive on `platform`, which is what makes a platform switch swap the
 * suggestions, descriptions and type labels while leaving the rows alone. */
function paramFor(key: string): CatalogParam | undefined {
  return catalogByName.value.get(key.trim())
}

function isReserved(key: string): boolean {
  return RESERVED_PARAM_KEYS.includes(key.trim())
}

// --- rows from a dict ----------------------------------------------------

let nextRowId = 0

const rows = ref<Row[]>(initialRows())
/** Transient AutoComplete state, keyed by row id — deliberately outside `rows`
 * so a keystroke in the dropdown cannot look like a change to the dict. */
const suggestions = ref<Record<number, string[]>>({})

function typeOf(key: string, value: unknown): ParamType {
  if (typeof value === 'number') return 'number'
  if (typeof value === 'boolean') return 'boolean'
  if (typeof value === 'string') return paramFor(key)?.type === 'enum' ? 'enum' : 'string'
  return 'json'
}

function makeRow(key: string, value: unknown, fromDefault: boolean): Row {
  const type = typeOf(key, value)
  return {
    id: nextRowId++,
    key,
    type,
    num: type === 'number' ? (value as number) : null,
    bool: type === 'boolean' ? (value as boolean) : false,
    str: type === 'string' || type === 'enum' ? (value as string) : '',
    // `?? null` because `JSON.stringify(undefined)` answers `undefined` rather
    // than a string, which would hand the Textarea a non-string model.
    raw: type === 'json' ? JSON.stringify(value ?? null, null, 2) : '',
    parsed: type === 'json' ? value : undefined,
    jsonError: false,
    fromDefault,
  }
}

function rowsFrom(source: Record<string, unknown> | null | undefined, fromDefault: boolean): Row[] {
  return Object.entries(source ?? {}).map(([key, value]) => makeRow(key, value, fromDefault))
}

function initialRows(): Row[] {
  // Run mode seeds from the defaults, not from `modelValue`: the overrides are
  // what this component *produces*, and a fresh run starts with none.
  return props.defaults !== undefined
    ? rowsFrom(props.defaults, true)
    : rowsFrom(props.modelValue, false)
}

// --- the emitted dict ----------------------------------------------------

function sameJson(a: unknown, b: unknown): boolean {
  return JSON.stringify(a) === JSON.stringify(b)
}

/** `undefined` means the row holds no value at all — a blank field or JSON that
 * does not parse. It is never a legitimate param value, so it doubles as the
 * "skip me" signal. */
function rowValue(row: Row): unknown {
  switch (row.type) {
    case 'number':
      return row.num ?? undefined
    case 'boolean':
      return row.bool
    case 'string':
    case 'enum':
      return row.str.length > 0 ? row.str : undefined
    case 'json':
      return row.jsonError ? undefined : row.parsed
  }
}

function buildValue(): Record<string, unknown> | null {
  const out: Record<string, unknown> = {}
  const present = new Set<string>()
  const defaults = props.defaults ?? {}

  for (const row of rows.value) {
    const key = row.key.trim()
    if (key.length === 0) continue
    // Presence is by key, independent of whether the row currently holds a
    // usable value — that is what keeps removal the only way to unset.
    present.add(key)
    const value = rowValue(row)
    if (value === undefined) continue
    if (isRunMode.value && key in defaults && sameJson(value, defaults[key])) continue
    out[key] = value
  }

  if (isRunMode.value) {
    for (const key of Object.keys(defaults)) {
      if (!present.has(key)) out[key] = null
    }
  }

  return Object.keys(out).length > 0 ? out : null
}

const emitted = computed(buildValue)

/** Guards the two watchers below against each other: whatever we last emitted,
 * serialized, so our own value coming back as `modelValue` is not mistaken for
 * an outside edit. */
let lastEmitted = JSON.stringify(emitted.value)

watch(emitted, (next) => {
  const json = JSON.stringify(next)
  if (json === lastEmitted) return
  lastEmitted = json
  emit('update:modelValue', next)
})

watch(
  () => props.modelValue,
  (next) => {
    if (isRunMode.value) return
    const json = JSON.stringify(next ?? null)
    if (json === lastEmitted) return
    lastEmitted = json
    rows.value = rowsFrom(next, false)
  },
)

// A different endpoint means different defaults, and an override is only
// meaningful against the defaults it was written for — so the edits go.
watch(
  () => props.defaults,
  () => {
    if (!isRunMode.value) return
    rows.value = rowsFrom(props.defaults, true)
  },
)

// --- row editing ---------------------------------------------------------

function addRow() {
  rows.value.push({
    id: nextRowId++,
    key: '',
    type: 'string',
    num: null,
    bool: false,
    str: '',
    raw: '',
    parsed: undefined,
    jsonError: false,
    fromDefault: false,
  })
}

function removeRow(row: Row) {
  rows.value = rows.value.filter((candidate) => candidate.id !== row.id)
}

/** Fires on every keystroke as well as on picking a suggestion, so a key typed
 * out in full lands on the catalog's type exactly as a picked one does. */
function setKey(row: Row, next: unknown) {
  row.key = typeof next === 'string' ? next : ''
  const param = paramFor(row.key)
  if (param) row.type = param.type
}

function completeKey(row: Row, query: string) {
  const taken = new Set(
    rows.value.filter((candidate) => candidate.id !== row.id).map((candidate) => candidate.key.trim()),
  )
  const needle = query.trim().toLowerCase()
  suggestions.value[row.id] = catalog.value.params
    .filter((param) => !taken.has(param.name))
    .filter((param) => needle.length === 0 || param.name.toLowerCase().includes(needle))
    .map((param) => param.name)
}

function parseJson(row: Row) {
  if (row.raw.trim().length === 0) {
    row.parsed = undefined
    row.jsonError = false
    return
  }
  try {
    row.parsed = JSON.parse(row.raw)
    row.jsonError = false
  } catch {
    row.jsonError = true
  }
}

/** While a row is in error the parse runs on every keystroke, so the complaint
 * disappears the moment the text becomes valid; an already-valid row keeps the
 * cheaper parse-on-blur posture. */
function onJsonInput(row: Row) {
  if (row.jsonError) parseJson(row)
}

const hasDefaultRow = computed(() => rows.value.some((row) => row.fromDefault))
</script>

<template>
  <div class="params-editor">
    <div v-if="rows.length > 0" class="param-grid param-header">
      <span class="label">Key</span>
      <span class="label">Type</span>
      <span class="label">Value</span>
      <span></span>
    </div>

    <div v-for="row in rows" :key="row.id" class="param-row">
      <div class="param-grid">
        <AutoComplete
          :model-value="row.key"
          :suggestions="suggestions[row.id] ?? []"
          :disabled="disabled"
          dropdown
          fluid
          placeholder="parameter name"
          aria-label="Parameter name"
          @update:model-value="(next: unknown) => setKey(row, next)"
          @complete="(event: { query: string }) => completeKey(row, event.query)"
        />

        <Select
          v-if="!paramFor(row.key)"
          v-model="row.type"
          :options="CUSTOM_TYPES"
          :disabled="disabled"
          fluid
          aria-label="Parameter type"
        />
        <span v-else class="type-label">{{ row.type }}</span>

        <InputNumber
          v-if="row.type === 'number'"
          v-model="row.num"
          :min="paramFor(row.key)?.min"
          :max="paramFor(row.key)?.max"
          :step="paramFor(row.key)?.step"
          :max-fraction-digits="6"
          :use-grouping="false"
          :disabled="disabled"
          fluid
          aria-label="Parameter value"
        />
        <ToggleSwitch
          v-else-if="row.type === 'boolean'"
          v-model="row.bool"
          :disabled="disabled"
          aria-label="Parameter value"
        />
        <Select
          v-else-if="row.type === 'enum'"
          v-model="row.str"
          :options="paramFor(row.key)?.values ?? []"
          :disabled="disabled"
          fluid
          show-clear
          placeholder="(unset)"
          aria-label="Parameter value"
        />
        <Textarea
          v-else-if="row.type === 'json'"
          v-model="row.raw"
          rows="2"
          auto-resize
          class="mono-input"
          :disabled="disabled"
          :placeholder="paramFor(row.key)?.placeholder"
          fluid
          aria-label="Parameter value (JSON)"
          @input="onJsonInput(row)"
          @blur="parseJson(row)"
        />
        <InputText
          v-else
          v-model="row.str"
          :disabled="disabled"
          :placeholder="paramFor(row.key)?.placeholder"
          fluid
          aria-label="Parameter value"
        />

        <Button
          icon="pi pi-times"
          text
          size="small"
          severity="danger"
          :disabled="disabled"
          aria-label="Remove parameter"
          @click="removeRow(row)"
        />
      </div>

      <div v-if="row.fromDefault || paramFor(row.key)" class="param-meta">
        <Tag v-if="row.fromDefault" value="default" severity="secondary" />
        <span v-if="paramFor(row.key)" class="hint">{{ paramFor(row.key)?.description }}</span>
      </div>
      <p v-if="row.jsonError" class="hint error">Must be valid JSON — this row is not sent.</p>
      <p v-if="isReserved(row.key)" class="hint warn">
        <code>{{ row.key.trim() }}</code> is set by the run itself and the server will refuse it.
      </p>
    </div>

    <p v-if="rows.length === 0" class="hint">No parameters — the server's own defaults apply.</p>

    <div>
      <Button
        label="Add parameter"
        icon="pi pi-plus"
        size="small"
        outlined
        :disabled="disabled"
        @click="addRow"
      />
    </div>

    <p v-if="hasDefaultRow" class="hint">
      Rows tagged <em>default</em> come from the endpoint — edit one to override it for this run, or
      remove it to unset it.
    </p>
    <p v-if="catalog.note" class="hint">{{ catalog.note }}</p>
  </div>
</template>

<style scoped>
.params-editor {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.param-row {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}

/* Key, type, value, remove. The value track gets the slack because it is the
 * one that holds a JSON textarea. */
.param-grid {
  display: grid;
  grid-template-columns: minmax(8rem, 1fr) 7rem minmax(10rem, 1.4fr) 2rem;
  gap: 0.5rem;
  align-items: center;
}

/* Grid items default to min-width:auto, which lets an input's intrinsic width
 * push a track wider than its share and bleed out of the panel. */
.param-grid > * {
  min-width: 0;
}

.param-header {
  /* The header only labels the tracks; without this it sits as far from the
   * first row as the rows sit from each other. */
  margin-bottom: -0.25rem;
}

.type-label {
  font-family: var(--p-font-family-mono, ui-monospace, monospace);
  font-size: 0.75rem;
  color: var(--p-text-muted-color);
}

.param-meta {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.param-meta .p-tag {
  font-size: 0.6875rem;
  padding: 0.0625rem 0.375rem;
}

.hint.warn {
  color: var(--p-orange-500);
}

.hint.error {
  color: var(--p-red-500, #ef4444);
}

.mono-input {
  font-family: var(--p-font-family-mono, ui-monospace, monospace);
}
</style>
