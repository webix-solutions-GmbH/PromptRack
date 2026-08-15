<script setup lang="ts">
// App shell: a top bar naming the product and a side nav grouped the way the
// work happens — Suite is the versioned content (prompts, test cases),
// Environment is the credentials that run it (endpoints, toolsets; both
// admin-gated for the same reason the backend draws that line — see
// CLAUDE.md's "content vs. credentials"), then Evaluate, then Settings.
// Every item below has a route as of Task 5.2 (Results was the last inert
// label); `NavItem.to` stays optional so a future addition can still land
// ahead of its view.
//
// The shell itself is auth-gated: /login and /setup render with no nav at
// all (there is nothing scoped to a workspace to show yet), everything else
// gets the full chrome plus the workspace switcher and account controls.
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import Select from 'primevue/select'
import Button from 'primevue/button'
import Menu from 'primevue/menu'
import { useAuthStore } from '../stores/auth'
import { useThemeStore, type ThemeMode } from '../stores/theme'
import { versionApi, type Version } from '../api/version'

type NavItem = { label: string; to?: string; icon: string }
type NavSection = { label: string | null; items: NavItem[] }

const sections: NavSection[] = [
  { label: null, items: [{ label: 'Dashboard', to: '/', icon: 'pi-home' }] },
  {
    label: 'Suite',
    items: [
      { label: 'Prompts', to: '/prompts', icon: 'pi-file-edit' },
      { label: 'Test Cases', to: '/test-cases', icon: 'pi-list-check' },
    ],
  },
  {
    label: 'Environment',
    items: [
      { label: 'Endpoints', to: '/endpoints', icon: 'pi-server' },
      { label: 'Toolsets', to: '/toolsets', icon: 'pi-wrench' },
    ],
  },
  {
    label: 'Evaluate',
    items: [
      { label: 'Runs', to: '/runs', icon: 'pi-play-circle' },
      { label: 'Results', to: '/results', icon: 'pi-table' },
    ],
  },
  {
    label: 'Settings',
    items: [
      { label: 'Workspaces', to: '/workspaces', icon: 'pi-briefcase' },
      { label: 'MCP', to: '/mcp', icon: 'pi-share-alt' },
    ],
  },
]

const auth = useAuthStore()
const router = useRouter()
const theme = useThemeStore()

// AppLayout is the app's one always-mounted root (App.vue renders it
// unconditionally; the `v-if="!auth.user"` below only swaps its template),
// so its setup is the one place a listener meant to live for the whole app
// can be registered exactly once — including on the signed-out /login and
// /setup screens, which get the persisted class but no toggle to change it.
theme.init()

// Fetched once on mount; a failure renders nothing rather than an error,
// since a missing build identity is not worth interrupting the shell over.
const version = ref<Version | null>(null)
onMounted(async () => {
  try {
    version.value = await versionApi.get()
  } catch {
    version.value = null
  }
})

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

// A PrimeVue popup `Menu` (ref + `toggle()`, per its documented pattern) is
// the whole picker; a computed items array (rather than a static one) is
// what lets the `#item` template below re-render the checkmark as `theme.mode`
// changes without a second source of truth to keep in sync with it.
const themeMenu = ref()
const themeModeItems: { mode: ThemeMode; label: string; icon: string }[] = [
  { mode: 'light', label: 'Light', icon: 'pi pi-sun' },
  { mode: 'dark', label: 'Dark', icon: 'pi pi-moon' },
  { mode: 'system', label: 'System', icon: 'pi pi-desktop' },
]
const themeItems = computed(() =>
  themeModeItems.map((item) => ({
    ...item,
    command: () => theme.setMode(item.mode),
  })),
)

function toggleThemeMenu(event: Event) {
  themeMenu.value?.toggle(event)
}

// The mark's strokes are drawn near-black; on a dark topbar they'd vanish,
// so a second copy (scripts/make-dark-logo.py) remaps them toward white
// while leaving the green accent and checkmark untouched.
const logoSrc = computed(() =>
  theme.resolved === 'dark' ? '/brand/promptrack-mark-dark-128.png' : '/brand/promptrack-mark-128.png',
)

// Collapsed to an icon rail, persisted per device (not per user — same
// reasoning as the theme mode above) so there's no flash on load.
const NAV_COLLAPSED_KEY = 'promptrack-nav-collapsed'
function readNavCollapsed(): boolean {
  try {
    return localStorage.getItem(NAV_COLLAPSED_KEY) === '1'
  } catch {
    return false
  }
}
const navCollapsed = ref(readNavCollapsed())
watch(navCollapsed, (collapsed) => {
  try {
    if (collapsed) localStorage.setItem(NAV_COLLAPSED_KEY, '1')
    else localStorage.removeItem(NAV_COLLAPSED_KEY)
  } catch {
    // Same as the theme store: losing the preference is not worth crashing over.
  }
})
function toggleNavCollapsed() {
  navCollapsed.value = !navCollapsed.value
}

const currentThemeLabel = computed(
  () => themeModeItems.find((item) => item.mode === theme.mode)?.label ?? 'Theme',
)

</script>

<template>
  <div v-if="!auth.user" class="auth-shell">
    <RouterView />
  </div>
  <div v-else class="app-shell">
    <header class="app-topbar">
      <!-- The 1254px master carries a wide transparent margin that shrinks the
           glyph to a smudge at this size; this copy is trimmed to the artwork
           and resized to 128px tall (2x), 15 KB against the master's 565 KB. -->
      <img :src="logoSrc" alt="" class="app-logo" />
      <span class="app-title">PromptRack</span>
      <div class="topbar-spacer" />
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
          {{ option.name }}{{ option.is_base ? ' — Base' : '' }}{{
            option.archived ? ' (archived)' : ''
          }}
        </template>
      </Select>
    </header>
    <div class="app-body">
      <nav class="app-sidenav" :class="{ 'app-sidenav-collapsed': navCollapsed }">
        <div v-for="section in sections" :key="section.label ?? 'main'" class="nav-section">
          <h2 v-if="section.label && !navCollapsed" class="nav-section-label">{{ section.label }}</h2>
          <RouterLink
            v-for="item in section.items.filter((i) => i.to)"
            :key="item.label"
            :to="item.to!"
            class="nav-item"
            active-class="nav-item-active"
            :title="navCollapsed ? item.label : undefined"
          >
            <i class="pi nav-item-icon" :class="item.icon" />
            <span v-if="!navCollapsed" class="nav-item-label">{{ item.label }}</span>
          </RouterLink>
          <span
            v-for="item in section.items.filter((i) => !i.to)"
            :key="item.label"
            class="nav-item nav-item-disabled"
            :title="navCollapsed ? item.label : undefined"
          >
            <i class="pi nav-item-icon" :class="item.icon" />
            <span v-if="!navCollapsed" class="nav-item-label">{{ item.label }}</span>
          </span>
        </div>
        <div class="nav-spacer" />
        <!-- Just the chevron, sitting at the rail's bottom right above the
             footer's border; centered like every other icon once collapsed. -->
        <div class="sidenav-toggle-row">
          <Button
            :icon="navCollapsed ? 'pi pi-angle-double-right' : 'pi pi-angle-double-left'"
            text
            size="small"
            severity="secondary"
            class="sidenav-toggle-button"
            :aria-label="navCollapsed ? 'Expand navigation' : 'Collapse navigation'"
            :title="navCollapsed ? 'Expand navigation' : 'Collapse navigation'"
            @click="toggleNavCollapsed"
          />
        </div>
        <!-- One coherent footer: an account row (avatar, identity, sign out)
             and a utility row (theme, GitHub, build version) below a single
             top border, instead of two separately-bordered blocks. -->
        <div class="sidenav-footer">
          <template v-if="!navCollapsed">
            <div class="footer-account-row">
              <div class="footer-account-info">
                <span class="footer-email" :title="auth.user.email">{{ auth.user.email }}</span>
                <span class="footer-role">{{ auth.user.role }}</span>
              </div>
              <div class="footer-spacer" />
              <Button
                icon="pi pi-sign-out"
                text
                severity="secondary"
                title="Sign out"
                aria-label="Sign out"
                class="footer-icon-button"
                @click="signOut"
              />
            </div>
            <div class="footer-utility-row">
              <div class="footer-utility-left">
                <Button
                  :icon="theme.resolved === 'dark' ? 'pi pi-moon' : 'pi pi-sun'"
                  text
                  severity="secondary"
                  :title="currentThemeLabel"
                  aria-label="Change theme"
                  aria-haspopup="true"
                  aria-controls="theme-menu"
                  class="footer-icon-button"
                  @click="toggleThemeMenu"
                />
                <a
                  href="https://github.com/philphilphil/promptrack"
                  target="_blank"
                  rel="noopener noreferrer"
                  aria-label="PromptRack on GitHub"
                  class="footer-github-link"
                >
                  <i class="pi pi-github" />
                </a>
              </div>
              <span v-if="version" class="footer-version"
                >v{{ version.version }}<template v-if="version.commit"> · {{ version.commit }}</template></span
              >
            </div>
          </template>
          <template v-else>
            <Button
              :icon="theme.resolved === 'dark' ? 'pi pi-moon' : 'pi pi-sun'"
              text
              severity="secondary"
              :title="currentThemeLabel"
              aria-label="Change theme"
              aria-haspopup="true"
              aria-controls="theme-menu"
              class="footer-icon-button"
              @click="toggleThemeMenu"
            />
            <Button
              icon="pi pi-sign-out"
              text
              severity="secondary"
              title="Sign out"
              aria-label="Sign out"
              class="footer-icon-button"
              @click="signOut"
            />
          </template>
        </div>
        <Menu ref="themeMenu" id="theme-menu" :model="themeItems" :popup="true">
          <template #item="{ item, props }">
            <a class="theme-menu-item" v-bind="props.action">
              <span :class="item.icon" />
              <span class="theme-menu-label">{{ item.label }}</span>
              <i v-if="item.mode === theme.mode" class="pi pi-check theme-menu-check" />
            </a>
          </template>
        </Menu>
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

/* Topbar and sidenav are both pinned: the account block, workspace switcher
 * and build row live at the bottom of the nav, and a long page (Test Cases
 * renders every group at once) would otherwise push them thousands of pixels
 * down. Only the content column scrolls. */
.app-topbar {
  position: sticky;
  top: 0;
  z-index: 2;
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0 1.25rem;
  height: 3.5rem;
  border-bottom: 1px solid var(--p-content-border-color);
  background: var(--p-content-background);
}

.app-logo {
  height: 2rem;
  width: auto;
  display: block;
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
  position: sticky;
  top: 3.5rem;
  align-self: flex-start;
  height: calc(100vh - 3.5rem);
  overflow-y: auto;
  overflow-x: hidden;
  width: 15rem;
  flex-shrink: 0;
  padding: 1rem 0.75rem;
  border-right: 1px solid var(--p-content-border-color);
  display: flex;
  flex-direction: column;
  transition: width 150ms ease;
}

.app-sidenav-collapsed {
  width: 3.5rem;
  padding: 1rem 0.5rem;
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
  display: flex;
  align-items: center;
  gap: 0.6rem;
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

/* Fixed icon column so labels line up regardless of glyph width. */
.nav-item-icon {
  width: 1.1rem;
  flex-shrink: 0;
  text-align: center;
  font-size: 0.95rem;
}

.nav-item-label {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.app-sidenav-collapsed .nav-item,
.app-sidenav-collapsed .nav-item-disabled {
  justify-content: center;
  padding: 0.5rem;
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

/* Sized for the topbar's 3.5rem height rather than the full-width sidebar
 * column it used to sit in: a fixed width so it doesn't jostle the theme
 * toggle as workspace names vary, and `align-items: center` on the topbar
 * already handles vertical centering. */
.workspace-select {
  width: 14rem;
  flex-shrink: 0;
}

/* Pushes the workspace switcher to the far right. */
.topbar-spacer {
  flex: 1;
}

.theme-menu-item {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.625rem 0.875rem;
  color: var(--p-text-color);
  cursor: pointer;
}

.theme-menu-item:hover {
  background: var(--p-content-hover-background);
}

.theme-menu-label {
  flex: 1;
}

.theme-menu-check {
  color: var(--p-primary-color);
}

/* Right-aligned at the rail's bottom, directly above the footer's top
 * border; centered like every other collapsed-rail icon once collapsed. */
.sidenav-toggle-row {
  display: flex;
  justify-content: flex-end;
  margin-top: 0.5rem;
}

.app-sidenav-collapsed .sidenav-toggle-row {
  justify-content: center;
}

.sidenav-toggle-button {
  color: var(--p-text-muted-color);
}

/* One footer block below the nav list: an account row and a utility row,
 * padded to match .nav-item's horizontal padding so it reads as part of the
 * same column rather than a bolted-on panel. */
.sidenav-footer {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  padding: 0.75rem 0.75rem 0;
  border-top: 1px solid var(--p-content-border-color);
}

.app-sidenav-collapsed .sidenav-footer {
  align-items: center;
  gap: 0.5rem;
  padding: 0.75rem 0;
}

.footer-account-row {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.footer-account-info {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.footer-email {
  font-size: 0.75rem;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.footer-role {
  font-size: 0.6875rem;
  color: var(--p-text-muted-color);
  text-transform: capitalize;
}

.footer-spacer {
  flex: 1;
}

.footer-icon-button {
  flex-shrink: 0;
  width: 1.75rem;
  height: 1.75rem;
  padding: 0;
  color: var(--p-text-muted-color);
}

.footer-utility-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
  padding-bottom: 0.75rem;
}

.footer-utility-left {
  display: flex;
  align-items: center;
  gap: 0.25rem;
}

.footer-github-link {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 1.75rem;
  height: 1.75rem;
  flex-shrink: 0;
  color: var(--p-text-muted-color);
}

.footer-github-link:hover {
  color: var(--p-text-color);
}

.footer-version {
  font-size: 0.6875rem;
  color: var(--p-text-muted-color);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
