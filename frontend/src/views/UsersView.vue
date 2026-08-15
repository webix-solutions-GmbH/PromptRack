<script setup lang="ts">
// Admin settings: who has an account, the invite links that create new ones,
// and a read-only view of how single sign-on is configured. Admin-only — the
// nav item and the route guard both hide it from everyone else, and every
// route this page calls is `Admin`-guarded on the backend, which is the real
// boundary.
//
// Three tabs rather than three pages because they answer one question ("who
// can get in, and how") and share nothing else with the rest of the app.
// Workspaces and MCP deliberately stay where they are: a workspace is
// `Writer`-creatable and tokens are per-user, so neither belongs behind an
// admin gate.
//
// Nobody can act on their own account here (the backend answers 409), so this
// page's own row renders its controls disabled with a title saying why —
// never offering a control that cannot be used, the same rule the role-gated
// buttons elsewhere follow.
import { computed, onMounted, ref } from 'vue'
import Button from 'primevue/button'
import Column from 'primevue/column'
import DataTable from 'primevue/datatable'
import Dialog from 'primevue/dialog'
import InputNumber from 'primevue/inputnumber'
import Message from 'primevue/message'
import Select from 'primevue/select'
import Tab from 'primevue/tab'
import TabList from 'primevue/tablist'
import TabPanel from 'primevue/tabpanel'
import TabPanels from 'primevue/tabpanels'
import Tabs from 'primevue/tabs'
import Tag from 'primevue/tag'
import { useConfirm } from 'primevue/useconfirm'
import { useToast } from 'primevue/usetoast'
import { ApiError } from '../api/client'
import { invitesApi, type CreatedInviteView, type InviteStatus, type InviteView } from '../api/invites'
import { usersApi, type OidcStatusView, type UserView } from '../api/users'
import { ROLE_LABELS, ROLE_OPTIONS, type Role, type RoleOption } from '../lib/roles'
import { formatDateTime } from '../lib/format'
import { useAuthStore } from '../stores/auth'
import CopyBlock from '../components/CopyBlock.vue'

const auth = useAuthStore()
const confirm = useConfirm()
const toast = useToast()

// --- users ----------------------------------------------------------------

const users = ref<UserView[]>([])
const loading = ref(true)
const loadError = ref<string | null>(null)

async function load() {
  loading.value = true
  loadError.value = null
  try {
    users.value = await usersApi.list()
  } catch (err) {
    loadError.value = err instanceof ApiError ? err.message : 'Failed to load users.'
  } finally {
    loading.value = false
  }
}

/** The one sentence every disabled control on your own row explains itself
 * with — the client-side half of the backend's 409, so the refusal is read
 * before the request rather than after it. */
const SELF_TITLE = 'You cannot change your own account here. Ask another administrator.'

function isSelf(row: UserView): boolean {
  return row.id === auth.user?.id
}

function rowClass(row: UserView) {
  return { 'disabled-row': row.disabled_at !== null }
}

const busyUserId = ref<number | null>(null)

function reportError(summary: string, err: unknown) {
  toast.add({
    severity: 'error',
    summary,
    detail: err instanceof ApiError ? err.message : undefined,
    life: 5000,
  })
}

async function changeRole(row: UserView, role: Role) {
  if (role === row.role) return
  busyUserId.value = row.id
  try {
    await usersApi.setRole(row.id, role)
    toast.add({
      severity: 'success',
      summary: `${row.email} is now ${ROLE_LABELS[role].toLowerCase()}`,
      life: 5000,
    })
  } catch (err) {
    reportError('Failed to change the role', err)
  } finally {
    busyUserId.value = null
    // Reloaded either way: on success for the canonical row, on failure to
    // put the Select back on the role that is actually stored.
    await load()
  }
}

function confirmDeactivate(row: UserView) {
  confirm.require({
    header: 'Deactivate account',
    message:
      `Deactivate ${row.email}? They are signed out immediately, their API tokens stop ` +
      `working — including any MCP client running under them — and they cannot sign in ` +
      `again until you reactivate the account. Nothing they authored is touched.`,
    acceptProps: { label: 'Deactivate', severity: 'danger' },
    rejectProps: { label: 'Cancel', text: true },
    accept: () => void setDisabled(row, true),
  })
}

async function setDisabled(row: UserView, disabled: boolean) {
  busyUserId.value = row.id
  try {
    await (disabled ? usersApi.deactivate(row.id) : usersApi.reactivate(row.id))
    await load()
    toast.add({
      severity: 'success',
      summary: disabled ? 'Account deactivated' : 'Account reactivated',
      life: 5000,
    })
  } catch (err) {
    reportError(disabled ? 'Failed to deactivate' : 'Failed to reactivate', err)
  } finally {
    busyUserId.value = null
  }
}

function confirmDelete(row: UserView) {
  confirm.require({
    header: 'Delete account',
    message:
      `Delete ${row.email}? Their prompt commits and deploy marks stay, but become ` +
      `authorless — the app records who committed each version and who marked one ` +
      `deployed, and deleting the account clears both. Their sessions and API tokens go ` +
      `with it. Deactivating instead keeps the attribution and can be undone; this ` +
      `cannot.`,
    acceptProps: { label: 'Delete', severity: 'danger' },
    rejectProps: { label: 'Cancel', text: true },
    accept: () => void removeUser(row),
  })
}

async function removeUser(row: UserView) {
  busyUserId.value = row.id
  try {
    await usersApi.remove(row.id)
    await load()
    toast.add({ severity: 'success', summary: 'Account deleted', life: 5000 })
  } catch (err) {
    // The last-admin and self-target guards answer with a sentence rather than
    // a bare status — surface it as-is.
    reportError('Could not delete the account', err)
  } finally {
    busyUserId.value = null
  }
}

// --- invites --------------------------------------------------------------

const invites = ref<InviteView[]>([])
const invitesLoading = ref(true)
const invitesError = ref<string | null>(null)

async function loadInvites() {
  invitesLoading.value = true
  invitesError.value = null
  try {
    invites.value = await invitesApi.list()
  } catch (err) {
    invitesError.value = err instanceof ApiError ? err.message : 'Failed to load invites.'
  } finally {
    invitesLoading.value = false
  }
}

const INVITE_STATUS_SEVERITY: Record<InviteStatus, 'info' | 'success' | 'secondary' | 'warn'> = {
  pending: 'info',
  redeemed: 'success',
  revoked: 'secondary',
  expired: 'warn',
}

function inviteRowClass(row: InviteView) {
  return { 'disabled-row': row.status !== 'pending' }
}

/** `app.auth.invites.DEFAULT_EXPIRY` in days, and `app.api.invites`'s ceiling. */
const DEFAULT_EXPIRY_DAYS = 7
const MAX_EXPIRY_DAYS = 90

const dialogOpen = ref(false)
const formRole = ref<Role>('member')
const formExpiresInDays = ref<number | null>(DEFAULT_EXPIRY_DAYS)
const formError = ref<string | null>(null)
const saving = ref(false)

// The link the API just minted — held only in memory, shown once, and never
// refetchable: only its hash is stored, so no route can return it again.
const createdInvite = ref<CreatedInviteView | null>(null)

function openCreate() {
  formRole.value = 'member'
  formExpiresInDays.value = DEFAULT_EXPIRY_DAYS
  formError.value = null
  dialogOpen.value = true
}

async function submitForm() {
  formError.value = null
  saving.value = true
  try {
    createdInvite.value = await invitesApi.create({
      role: formRole.value,
      // Not nullable on the wire (unlike an API token's expiry): a cleared
      // field means the server's own default, so send that rather than null.
      expires_in_days: formExpiresInDays.value ?? DEFAULT_EXPIRY_DAYS,
    })
    dialogOpen.value = false
    toast.add({ severity: 'success', summary: 'Invite created', life: 5000 })
    await loadInvites()
  } catch (err) {
    formError.value = err instanceof ApiError ? err.message : 'Failed to create the invite.'
  } finally {
    saving.value = false
  }
}

function dismissReveal() {
  createdInvite.value = null
}

const busyInviteId = ref<number | null>(null)

function confirmRevoke(row: InviteView) {
  confirm.require({
    header: 'Revoke invite',
    message: `Revoke this invite? The link stops working immediately for whoever holds it.`,
    acceptProps: { label: 'Revoke', severity: 'danger' },
    rejectProps: { label: 'Cancel', text: true },
    accept: () => void revoke(row),
  })
}

async function revoke(row: InviteView) {
  busyInviteId.value = row.id
  try {
    await invitesApi.revoke(row.id)
    await loadInvites()
    toast.add({ severity: 'success', summary: 'Invite revoked', life: 5000 })
  } catch (err) {
    reportError('Failed to revoke the invite', err)
  } finally {
    busyInviteId.value = null
  }
}

// --- authentication -------------------------------------------------------

const oidc = ref<OidcStatusView | null>(null)
const oidcError = ref<string | null>(null)

async function loadOidc() {
  oidcError.value = null
  try {
    oidc.value = await usersApi.oidcStatus()
  } catch (err) {
    oidcError.value =
      err instanceof ApiError ? err.message : 'Failed to load the authentication settings.'
  }
}

const oidcScopes = computed(() => oidc.value?.scopes.join(', ') || '—')

onMounted(() => {
  void load()
  void loadInvites()
  void loadOidc()
})
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div class="page-heading">
        <h1>Users</h1>
        <p class="subtitle">
          Who has an account on this install, the invite links that create new ones, and how
          single sign-on is configured. Sign-up closed after the first account, so every account
          since arrived through an invite or through the identity provider.
        </p>
      </div>
    </div>

    <Tabs value="users">
      <TabList>
        <Tab value="users">Users</Tab>
        <Tab value="invites">Invites</Tab>
        <Tab value="authentication">Authentication</Tab>
      </TabList>
      <TabPanels>
        <!-- Users ---------------------------------------------------------->
        <TabPanel value="users">
          <section class="section">
            <Message v-if="loadError" severity="error" :closable="false">{{ loadError }}</Message>

            <DataTable
              :value="users"
              :loading="loading"
              data-key="id"
              class="table list-table"
              :row-class="rowClass"
            >
              <template #empty>No users.</template>
              <Column field="name" header="Name">
                <template #body="{ data }: { data: UserView }">
                  <div class="name-cell">
                    <span class="name">{{ data.name }}</span>
                    <Tag v-if="isSelf(data)" value="you" severity="info" />
                  </div>
                </template>
              </Column>
              <Column field="email" header="Email">
                <template #body="{ data }: { data: UserView }">{{ data.email }}</template>
              </Column>
              <Column header="Role" class="fit-column">
                <template #body="{ data }: { data: UserView }">
                  <span :title="isSelf(data) ? SELF_TITLE : undefined">
                    <Select
                      :model-value="data.role"
                      :options="ROLE_OPTIONS"
                      option-label="label"
                      option-value="value"
                      size="small"
                      class="role-select"
                      :disabled="isSelf(data) || busyUserId === data.id"
                      :aria-label="`Role for ${data.email}`"
                      @update:model-value="(role: Role) => changeRole(data, role)"
                    >
                      <template #option="{ option }: { option: RoleOption }">
                        <div class="role-option">
                          <span>{{ option.label }}</span>
                          <span class="role-option-description">{{ option.description }}</span>
                        </div>
                      </template>
                    </Select>
                  </span>
                </template>
              </Column>
              <Column header="Sign-in" class="fit-column">
                <template #body="{ data }: { data: UserView }">
                  <div class="name-cell">
                    <Tag v-if="data.has_password" value="password" severity="secondary" />
                    <Tag v-if="data.has_oidc" value="SSO" severity="secondary" />
                    <span v-if="!data.has_password && !data.has_oidc" class="mono">—</span>
                  </div>
                </template>
              </Column>
              <Column header="Status" class="fit-column">
                <template #body="{ data }: { data: UserView }">
                  <Tag
                    v-if="data.disabled_at"
                    value="Deactivated"
                    severity="warn"
                    :title="`Deactivated ${formatDateTime(data.disabled_at)}`"
                  />
                  <Tag v-else value="Active" severity="success" />
                </template>
              </Column>
              <Column field="created_at" header="Created" class="fit-column">
                <template #body="{ data }: { data: UserView }">
                  {{ formatDateTime(data.created_at) }}
                </template>
              </Column>
              <Column header="" class="actions-column">
                <template #body="{ data }: { data: UserView }">
                  <div class="row-actions" :title="isSelf(data) ? SELF_TITLE : undefined">
                    <Button
                      :label="data.disabled_at ? 'Reactivate' : 'Deactivate'"
                      text
                      size="small"
                      :disabled="isSelf(data)"
                      :loading="busyUserId === data.id"
                      @click="data.disabled_at ? setDisabled(data, false) : confirmDeactivate(data)"
                    />
                    <Button
                      label="Delete"
                      text
                      size="small"
                      severity="danger"
                      :disabled="isSelf(data)"
                      :loading="busyUserId === data.id"
                      @click="confirmDelete(data)"
                    />
                  </div>
                </template>
              </Column>
            </DataTable>

            <p class="hint">
              Deactivating is the reversible answer to "this person is gone": it signs them out
              and stops their API tokens, and it can be undone. Deleting is for an account
              created by mistake.
            </p>
          </section>
        </TabPanel>

        <!-- Invites -------------------------------------------------------->
        <TabPanel value="invites">
          <section class="section">
            <div class="section-header">
              <h2>Invite links</h2>
              <Button label="New invite" icon="pi pi-plus" @click="openCreate" />
            </div>

            <p class="hint">
              An invite names a role, not a person — whoever opens the link first supplies their
              own email, name and password, and consumes it. One link lets in one person.
            </p>

            <div v-if="createdInvite" class="panel reveal-panel">
              <h2>New invite created</h2>
              <p class="hint">
                This link is shown only once — copy it now and send it to the person you are
                inviting. It cannot be shown again; if it's lost, revoke it and create another.
                It grants <strong>{{ ROLE_LABELS[createdInvite.role] }}</strong> and expires
                {{ formatDateTime(createdInvite.expires_at) }}.
              </p>
              <CopyBlock :code="createdInvite.url" />
              <div class="dialog-actions">
                <Button label="I've copied it" text @click="dismissReveal" />
              </div>
            </div>

            <Message v-if="invitesError" severity="error" :closable="false">
              {{ invitesError }}
            </Message>

            <DataTable
              :value="invites"
              :loading="invitesLoading"
              data-key="id"
              class="table list-table"
              :row-class="inviteRowClass"
            >
              <template #empty>No invites yet — create one with "New invite".</template>
              <Column header="Role" class="fit-column">
                <template #body="{ data }: { data: InviteView }">
                  {{ ROLE_LABELS[data.role] }}
                </template>
              </Column>
              <Column header="Link" class="fit-column">
                <template #body="{ data }: { data: InviteView }">
                  <span class="mono">{{ data.display_prefix }}…</span>
                </template>
              </Column>
              <Column header="Status" class="fit-column">
                <template #body="{ data }: { data: InviteView }">
                  <Tag :value="data.status" :severity="INVITE_STATUS_SEVERITY[data.status]" />
                </template>
              </Column>
              <Column header="Expires" class="fit-column">
                <template #body="{ data }: { data: InviteView }">
                  {{ formatDateTime(data.expires_at) }}
                </template>
              </Column>
              <Column header="Created" class="fit-column">
                <template #body="{ data }: { data: InviteView }">
                  {{ formatDateTime(data.created_at) }}
                  <span v-if="data.created_by_name" class="by-line">
                    by {{ data.created_by_name }}
                  </span>
                </template>
              </Column>
              <Column header="Redeemed">
                <template #body="{ data }: { data: InviteView }">
                  <template v-if="data.redeemed_at">
                    {{ formatDateTime(data.redeemed_at) }}
                    <span v-if="data.redeemed_by_name" class="by-line">
                      by {{ data.redeemed_by_name }}
                    </span>
                  </template>
                  <span v-else class="mono">—</span>
                </template>
              </Column>
              <Column header="" class="actions-column">
                <template #body="{ data }: { data: InviteView }">
                  <div v-if="data.status === 'pending'" class="row-actions">
                    <Button
                      icon="pi pi-trash"
                      text
                      size="small"
                      severity="danger"
                      aria-label="Revoke invite"
                      title="Revoke invite"
                      :loading="busyInviteId === data.id"
                      @click="confirmRevoke(data)"
                    />
                  </div>
                </template>
              </Column>
            </DataTable>
          </section>
        </TabPanel>

        <!-- Authentication ------------------------------------------------->
        <TabPanel value="authentication">
          <section class="section">
            <h2>Single sign-on</h2>

            <Message v-if="oidcError" severity="error" :closable="false">{{ oidcError }}</Message>

            <template v-else-if="oidc">
              <Message v-if="!oidc.configured" severity="info" :closable="false">
                Single sign-on is not configured. Set <code>OIDC_ISSUER</code>,
                <code>OIDC_CLIENT_ID</code> and <code>OIDC_CLIENT_SECRET</code> in the
                environment — optionally <code>OIDC_SCOPES</code> and
                <code>OIDC_DEFAULT_ROLE</code> as well — and redeploy to turn it on. Until then
                accounts are created by the invite links on the previous tab.
              </Message>

              <template v-else>
                <dl class="definition-list">
                  <div class="definition-row">
                    <dt>Status</dt>
                    <dd><Tag value="Configured" severity="success" /></dd>
                  </div>
                  <div class="definition-row">
                    <dt>Issuer</dt>
                    <dd class="mono">{{ oidc.issuer ?? '—' }}</dd>
                  </div>
                  <div class="definition-row">
                    <dt>Client ID</dt>
                    <dd class="mono">{{ oidc.client_id ?? '—' }}</dd>
                  </div>
                  <div class="definition-row">
                    <dt>Client secret</dt>
                    <dd>{{ oidc.secret_set ? 'Set' : 'Not set' }}</dd>
                  </div>
                  <div class="definition-row">
                    <dt>Scopes</dt>
                    <dd class="mono">{{ oidcScopes }}</dd>
                  </div>
                  <div class="definition-row">
                    <dt>Default role</dt>
                    <dd>{{ ROLE_LABELS[oidc.default_role] }}</dd>
                  </div>
                </dl>

                <p class="hint">
                  These values come from the environment and are read at start-up, so changing
                  single sign-on means editing the environment and redeploying — there is
                  nothing to edit here. Someone signing in through the provider for the first
                  time is provisioned automatically at the default role above, without an
                  invite.
                </p>
              </template>
            </template>
          </section>
        </TabPanel>
      </TabPanels>
    </Tabs>

    <Dialog v-model:visible="dialogOpen" modal header="New invite" class="form-dialog">
      <form class="dialog-form" @submit.prevent="submitForm">
        <div class="field">
          <label for="invite-role">Role *</label>
          <Select
            id="invite-role"
            v-model="formRole"
            :options="ROLE_OPTIONS"
            option-label="label"
            option-value="value"
          >
            <template #option="{ option }: { option: RoleOption }">
              <div class="role-option">
                <span>{{ option.label }}</span>
                <span class="role-option-description">{{ option.description }}</span>
              </div>
            </template>
          </Select>
          <p class="hint">The account created from this link lands at this role.</p>
        </div>
        <div class="field">
          <label for="invite-expires">Expires in (days)</label>
          <InputNumber
            id="invite-expires"
            v-model="formExpiresInDays"
            :min="1"
            :max="MAX_EXPIRY_DAYS"
            :placeholder="String(DEFAULT_EXPIRY_DAYS)"
            show-buttons
          />
        </div>
        <Message v-if="formError" severity="error" :closable="false">{{ formError }}</Message>
        <div class="dialog-actions">
          <Button type="button" label="Cancel" text @click="dialogOpen = false" />
          <Button type="submit" label="Create invite" :loading="saving" />
        </div>
      </form>
    </Dialog>
  </div>
</template>

<style scoped>
/* Same shape as McpView's: a heading and its content without `.panel`'s box.
 * The tab panel supplies the outer padding, so a section only states what
 * holds it together internally. */
.section {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.section h2 {
  font-size: 1.0625rem;
}

/* Centred, like McpView's: a one-line h2 next to a button reads as one row. */
.section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
}

.name {
  font-weight: 500;
}

/* Narrow enough that the email and name columns keep the width, wide enough
 * that "Member" does not truncate. */
.role-select {
  width: 8.5rem;
}

.role-option {
  display: flex;
  flex-direction: column;
  gap: 0.125rem;
}

.role-option-description {
  font-size: 0.75rem;
  color: var(--p-text-muted-color);
}

/* Same treatment McpView gives a revoked token: still readable, visibly spent. */
:deep(.disabled-row) {
  opacity: 0.55;
}

.reveal-panel {
  border-color: var(--p-primary-color);
}

.by-line {
  color: var(--p-text-muted-color);
}

/* A read-only key/value block. Grid rather than a plain <dl> so the terms line
 * up in a column of their own; only this page has one, so it stays scoped. */
.definition-list {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  margin: 0;
}

.definition-row {
  display: grid;
  grid-template-columns: 9rem 1fr;
  gap: 0.75rem;
  align-items: baseline;
}

.definition-row dt {
  font-size: 0.8125rem;
  font-weight: 500;
  color: var(--p-text-muted-color);
}

.definition-row dd {
  margin: 0;
  font-size: 0.875rem;
  overflow-wrap: anywhere;
}
</style>
