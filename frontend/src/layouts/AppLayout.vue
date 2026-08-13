<script setup lang="ts">
// App shell: a top bar naming the product and a side nav grouped the way the
// work happens (configure it, run it, read it). Later tasks add routes and
// views for the sections below; until then, items with no route yet render
// as inert labels rather than dead links.
//
// The shell itself is auth-gated: /login and /setup render with no nav at
// all (there is nothing scoped to a workspace to show yet), everything else
// gets the full chrome plus the workspace switcher and account controls.
import { computed, watch } from 'vue'
import { useRouter } from 'vue-router'
import Select from 'primevue/select'
import Button from 'primevue/button'
import { useAuthStore } from '../stores/auth'

type NavItem = { label: string; to?: string }
type NavSection = { label: string | null; items: NavItem[] }

const sections: NavSection[] = [
  { label: null, items: [{ label: 'Dashboard', to: '/' }] },
  {
    label: 'Setup',
    items: [
      { label: 'Prompts', to: '/prompts' },
      { label: 'Test Cases', to: '/test-cases' },
      { label: 'Toolsets', to: '/toolsets' },
      { label: 'Machines', to: '/machines' },
    ],
  },
  {
    label: 'Evaluate',
    items: [{ label: 'Runs' }, { label: 'Results' }],
  },
  {
    label: 'Settings',
    items: [{ label: 'Workspaces', to: '/workspaces' }],
  },
]

const auth = useAuthStore()
const router = useRouter()

// Workspaces are a label, not a tenant: every signed-in user may switch
// into any of them, so the list is fetched as soon as a session exists
// rather than gated on canAdminister.
watch(
  () => auth.user?.id,
  (id) => {
    if (id !== undefined) void auth.fetchCustomers()
  },
  { immediate: true },
)

// Archived workspaces stay hidden unless the user is standing in one,
// which happens when someone archives the workspace they were working in.
const visibleCustomers = computed(() =>
  auth.customers.filter(
    (customer) => !customer.archived || customer.id === auth.activeCustomer?.id,
  ),
)

async function onWorkspaceChange(customerId: number) {
  if (customerId === auth.activeCustomer?.id) return
  await auth.switchCustomer(customerId)
  // Every page's data is scoped to the active workspace; reloading is the
  // simplest correct way to refetch all of it without a cross-view data bus.
  window.location.reload()
}

async function signOut() {
  await auth.logout()
  await router.push({ name: 'login' })
}
</script>

<template>
  <div v-if="!auth.user" class="auth-shell">
    <RouterView />
  </div>
  <div v-else class="app-shell">
    <header class="app-topbar">
      <span class="app-title">PromptRack</span>
    </header>
    <div class="app-body">
      <nav class="app-sidenav">
        <div v-for="section in sections" :key="section.label ?? 'main'" class="nav-section">
          <h2 v-if="section.label" class="nav-section-label">{{ section.label }}</h2>
          <RouterLink
            v-for="item in section.items.filter((i) => i.to)"
            :key="item.label"
            :to="item.to!"
            class="nav-item"
            active-class="nav-item-active"
          >
            {{ item.label }}
          </RouterLink>
          <span
            v-for="item in section.items.filter((i) => !i.to)"
            :key="item.label"
            class="nav-item nav-item-disabled"
          >
            {{ item.label }}
          </span>
        </div>
        <div class="nav-spacer" />
        <div class="nav-section">
          <h2 class="nav-section-label">Workspace</h2>
          <Select
            :model-value="auth.activeCustomer?.id"
            :options="visibleCustomers"
            option-label="name"
            option-value="id"
            placeholder="Select workspace"
            class="workspace-select"
            @update:model-value="onWorkspaceChange"
          >
            <template #option="{ option }">
              {{ option.name }}{{ option.archived ? ' (archived)' : '' }}
            </template>
          </Select>
        </div>
        <div class="nav-section account-section">
          <span class="account-email">{{ auth.user.email }}</span>
          <span class="account-role">{{ auth.user.role }}</span>
          <Button label="Sign out" text size="small" class="sign-out-button" @click="signOut" />
        </div>
      </nav>
      <main class="app-content">
        <RouterView />
      </main>
    </div>
  </div>
</template>

<style scoped>
.app-shell {
  display: flex;
  flex-direction: column;
  min-height: 100vh;
}

.app-topbar {
  display: flex;
  align-items: center;
  padding: 0 1.25rem;
  height: 3.5rem;
  border-bottom: 1px solid var(--p-content-border-color);
  background: var(--p-content-background);
}

.app-title {
  font-weight: 600;
  font-size: 1.05rem;
}

.app-body {
  display: flex;
  flex: 1;
  min-height: 0;
}

.app-sidenav {
  width: 15rem;
  flex-shrink: 0;
  padding: 1rem 0.75rem;
  border-right: 1px solid var(--p-content-border-color);
  display: flex;
  flex-direction: column;
}

.nav-section {
  display: flex;
  flex-direction: column;
  gap: 0.125rem;
  margin-bottom: 1.25rem;
}

.nav-section-label {
  padding: 0 0.75rem 0.25rem;
  font-size: 0.6875rem;
  font-weight: 600;
  letter-spacing: 0.05em;
  text-transform: uppercase;
  color: var(--p-text-muted-color);
}

.nav-item {
  display: block;
  padding: 0.5rem 0.75rem;
  border-radius: var(--p-content-border-radius);
  font-size: 0.875rem;
  font-weight: 500;
  text-decoration: none;
  color: var(--p-text-color);
}

.nav-item:hover {
  background: var(--p-content-hover-background);
}

.nav-item-active {
  background: var(--p-highlight-background);
  color: var(--p-highlight-color);
}

.nav-item-disabled {
  color: var(--p-text-muted-color);
  cursor: default;
}

.app-content {
  flex: 1;
  min-width: 0;
  padding: 1.5rem;
  overflow: auto;
}

.auth-shell {
  min-height: 100vh;
}

.nav-spacer {
  flex: 1;
}

.workspace-select {
  width: 100%;
}

.account-section {
  padding: 0.75rem 0.75rem 0;
  border-top: 1px solid var(--p-content-border-color);
  flex-direction: row;
  align-items: center;
  gap: 0.5rem;
  margin-bottom: 0;
}

.account-email {
  flex: 1;
  min-width: 0;
  font-size: 0.8125rem;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.account-role {
  font-size: 0.6875rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.03em;
  color: var(--p-text-muted-color);
}

.sign-out-button {
  flex-shrink: 0;
}
</style>
