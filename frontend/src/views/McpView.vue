<script setup lang="ts">
// MCP settings: manage the API tokens an MCP client authenticates with, and
// show how to point Claude Code / Codex / OpenCode at this app's MCP server.
// Every signed-in role may hold tokens — a viewer's token simply cannot call
// a write tool once app.mcp checks its role, same as everywhere else (see
// CLAUDE.md's "This app as an MCP server").
import { computed, onMounted, ref } from 'vue'
import Button from 'primevue/button'
import Column from 'primevue/column'
import DataTable from 'primevue/datatable'
import Dialog from 'primevue/dialog'
import InputNumber from 'primevue/inputnumber'
import InputText from 'primevue/inputtext'
import Message from 'primevue/message'
import Tag from 'primevue/tag'
import { useConfirm } from 'primevue/useconfirm'
import { useToast } from 'primevue/usetoast'
import { tokensApi, type CreatedTokenView, type TokenView } from '../api/tokens'
import { ApiError } from '../api/client'
import { formatDateTime } from '../lib/format'
import CopyBlock from '../components/CopyBlock.vue'

const toast = useToast()
const confirm = useConfirm()

// The same URL this page lives at: the backend answers POST here and lets GET
// fall through to the SPA, so a client's server URL is also a link a human can
// open (see CLAUDE.md's "This app as an MCP server").
const mcpUrl = `${window.location.origin}/mcp`

// --- server URL -----------------------------------------------------------

const urlCopied = ref(false)
let urlCopyTimer: ReturnType<typeof setTimeout> | undefined

async function copyUrl() {
  await navigator.clipboard.writeText(mcpUrl)
  urlCopied.value = true
  clearTimeout(urlCopyTimer)
  urlCopyTimer = setTimeout(() => {
    urlCopied.value = false
  }, 2000)
}

// --- token list -------------------------------------------------------

const tokens = ref<TokenView[]>([])
const loading = ref(true)
const loadError = ref<string | null>(null)

async function load() {
  loading.value = true
  loadError.value = null
  try {
    tokens.value = await tokensApi.list()
  } catch (err) {
    loadError.value = err instanceof ApiError ? err.message : 'Failed to load tokens.'
  } finally {
    loading.value = false
  }
}

onMounted(load)

function rowClass(row: TokenView) {
  return { 'revoked-row': row.revoked_at !== null }
}

// --- create dialog ------------------------------------------------------

interface TokenFormState {
  name: string
  expiresInDays: number | null
}

function emptyForm(): TokenFormState {
  return { name: '', expiresInDays: null }
}

const dialogOpen = ref(false)
const form = ref<TokenFormState>(emptyForm())
const formError = ref<string | null>(null)
const saving = ref(false)

// The raw value the API just minted — held only in memory, shown once, and
// never refetchable: there is no route that returns it again.
const createdToken = ref<CreatedTokenView | null>(null)

function openCreate() {
  form.value = emptyForm()
  formError.value = null
  dialogOpen.value = true
}

async function submitForm() {
  formError.value = null
  saving.value = true
  try {
    const created = await tokensApi.create({
      name: form.value.name,
      expires_in_days: form.value.expiresInDays,
    })
    createdToken.value = created
    dialogOpen.value = false
    toast.add({ severity: 'success', summary: 'Token created', life: 5000 })
    await load()
  } catch (err) {
    formError.value = err instanceof ApiError ? err.message : 'Failed to create the token.'
  } finally {
    saving.value = false
  }
}

function dismissReveal() {
  createdToken.value = null
}

// --- revoke ---------------------------------------------------------------

const busyTokenId = ref<number | null>(null)

function confirmRevoke(row: TokenView) {
  confirm.require({
    header: 'Revoke token',
    message: `Revoke "${row.name}"? Any client using it will stop being able to authenticate.`,
    acceptProps: { label: 'Revoke', severity: 'danger' },
    rejectProps: { label: 'Cancel', text: true },
    accept: () => void revoke(row),
  })
}

async function revoke(row: TokenView) {
  busyTokenId.value = row.id
  try {
    await tokensApi.remove(row.id)
    await load()
    toast.add({ severity: 'success', summary: 'Token revoked', life: 5000 })
  } catch (err) {
    toast.add({
      severity: 'error',
      summary: 'Failed to revoke token',
      detail: err instanceof ApiError ? err.message : undefined,
      life: 5000,
    })
  } finally {
    busyTokenId.value = null
  }
}

// --- client setup snippets -------------------------------------------------

// The just-created raw token when there is one, otherwise a placeholder the
// user swaps in by hand — the snippets are plain copyable text, not code
// Vue executes, so the real value has to be interpolated here rather than
// left as a shell/template expression that would never expand.
const tokenForSnippets = computed(() => createdToken.value?.token ?? 'YOUR_API_TOKEN')

const claudeCodeSnippet = computed(
  () =>
    `claude mcp add --transport http promptrack ${mcpUrl} --header "x-api-key: ${tokenForSnippets.value}"`,
)

// Codex has no flag for a custom header, only `--bearer-token-env-var`, which
// is enough here: the server reads `Authorization: Bearer` when `x-api-key` is
// absent. The token stays in the environment rather than in config.toml, so the
// export belongs in a shell profile.
const codexSnippet = computed(
  () => `export PROMPTRACK_API_KEY="${tokenForSnippets.value}"   # add this line to your shell profile too
codex mcp add promptrack --url ${mcpUrl} --bearer-token-env-var PROMPTRACK_API_KEY`,
)

const opencodeSnippet = computed(
  () => `{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "promptrack": {
      "type": "remote",
      "url": "${mcpUrl}",
      "enabled": true,
      "headers": { "x-api-key": "${tokenForSnippets.value}" }
    }
  }
}`,
)
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div class="page-heading">
        <h1>MCP</h1>
        <p class="subtitle">
          PromptRack exposes its suite over MCP so an agent can push prompts and test cases in and
          read run results back, without going through the UI. Connect a client with an API token
          below.
        </p>
        <!-- One line rather than a panel of its own: the URL is a fact about
             this page, not a section of it. -->
        <div class="server-url-row">
          <span class="server-label">Server</span>
          <code class="server-url">{{ mcpUrl }}</code>
          <Button
            :icon="urlCopied ? 'pi pi-check' : 'pi pi-copy'"
            text
            size="small"
            severity="secondary"
            :aria-label="urlCopied ? 'Copied' : 'Copy server URL'"
            :title="urlCopied ? 'Copied' : 'Copy server URL'"
            @click="copyUrl"
          />
        </div>
      </div>
    </div>

    <div v-if="createdToken" class="panel reveal-panel">
      <h2>New token created</h2>
      <p class="hint">
        This token is shown only once — copy it now. It cannot be shown again; if it's lost,
        revoke it and create a new one.
      </p>
      <CopyBlock :code="createdToken.token" />
      <div class="dialog-actions">
        <Button label="I've copied it" text @click="dismissReveal" />
      </div>
    </div>

    <section class="section">
      <div class="section-header">
        <h2>API tokens</h2>
        <Button label="New token" icon="pi pi-plus" @click="openCreate" />
      </div>

      <Message v-if="loadError" severity="error" :closable="false">{{ loadError }}</Message>

      <DataTable
        :value="tokens"
        :loading="loading"
        data-key="id"
        class="table list-table"
        :row-class="rowClass"
      >
        <template #empty>No tokens yet — add one with "New token".</template>
        <Column field="name" header="Name">
          <template #body="{ data }: { data: TokenView }">
            <div class="name-cell">
              <span>{{ data.name }}</span>
              <Tag v-if="data.revoked_at !== null" value="revoked" severity="danger" />
            </div>
          </template>
        </Column>
        <Column header="Token" class="fit-column">
          <template #body="{ data }: { data: TokenView }">
            <span class="mono">{{ data.display_prefix }}…</span>
          </template>
        </Column>
        <Column field="created_at" header="Created" class="fit-column">
          <template #body="{ data }: { data: TokenView }">{{ formatDateTime(data.created_at) }}</template>
        </Column>
        <Column header="Last used" class="fit-column">
          <template #body="{ data }: { data: TokenView }">
            {{ data.last_used_at ? formatDateTime(data.last_used_at) : 'never' }}
          </template>
        </Column>
        <Column header="Expires" class="fit-column">
          <template #body="{ data }: { data: TokenView }">
            {{ data.expires_at ? formatDateTime(data.expires_at) : 'never' }}
          </template>
        </Column>
        <Column header="" class="actions-column">
          <template #body="{ data }: { data: TokenView }">
            <div v-if="data.revoked_at === null" class="row-actions">
              <Button
                icon="pi pi-trash"
                text
                size="small"
                severity="danger"
                aria-label="Revoke token"
                title="Revoke token"
                :loading="busyTokenId === data.id"
                @click="confirmRevoke(data)"
              />
            </div>
          </template>
        </Column>
      </DataTable>
    </section>

    <section class="section">
      <h2>Connect a client</h2>
      <p class="hint">
        Each snippet uses the token above once you've just created one, or the placeholder
        <code>YOUR_API_TOKEN</code> otherwise — swap in a real token before running it.
      </p>

      <div class="client-section">
        <h3>Claude Code</h3>
        <CopyBlock :code="claudeCodeSnippet" />
      </div>

      <div class="client-section">
        <h3>Codex</h3>
        <p class="hint">
          The token is read from your environment at run time, so keep the export in your shell
          profile.
        </p>
        <CopyBlock :code="codexSnippet" />
      </div>

      <div class="client-section">
        <h3>OpenCode</h3>
        <p class="hint">Add to <code>~/.config/opencode/opencode.json</code>.</p>
        <CopyBlock :code="opencodeSnippet" />
      </div>
    </section>

    <Dialog v-model:visible="dialogOpen" modal header="New token" class="form-dialog">
      <form class="dialog-form" @submit.prevent="submitForm">
        <div class="field">
          <label for="token-name">Name *</label>
          <InputText id="token-name" v-model="form.name" required placeholder="laptop" autofocus />
        </div>
        <div class="field">
          <label for="token-expires">Expires in (days)</label>
          <InputNumber
            id="token-expires"
            v-model="form.expiresInDays"
            :min="1"
            :max="3650"
            placeholder="never expires"
            show-buttons
          />
        </div>
        <Message v-if="formError" severity="error" :closable="false">{{ formError }}</Message>
        <div class="dialog-actions">
          <Button type="button" label="Cancel" text @click="dialogOpen = false" />
          <Button type="submit" label="Create token" :loading="saving" />
        </div>
      </form>
    </Dialog>
  </div>
</template>

<style scoped>
/* A heading and its content, without `.panel`'s box: only three things on this
 * page are worth a border, and two of them (the copy blocks) draw their own.
 * `.page` already spaces the sections 1.5rem apart, so a section states only
 * what holds it together internally — `.panel`'s own gap, minus the padding. */
.section {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.section h2 {
  font-size: 1.0625rem;
}

/* Centred rather than `.page-header`'s top alignment: a one-line h2 next to a
 * button reads as one row, where a heading plus a subtitle does not. */
.section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
}

.server-url-row {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin-top: 0.75rem;
}

.server-label {
  font-size: 0.8125rem;
  font-weight: 500;
  color: var(--p-text-muted-color);
}

.server-url {
  padding: 0.25rem 0.5rem;
  background: var(--p-content-hover-background);
  border: 1px solid var(--p-content-border-color);
  border-radius: var(--p-content-border-radius);
}

.reveal-panel {
  border-color: var(--p-primary-color);
}

.client-section {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.client-section h3 {
  font-size: 0.9375rem;
  font-weight: 600;
  margin: 0;
}

:deep(.revoked-row) {
  opacity: 0.55;
}
</style>
