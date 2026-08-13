<script setup lang="ts">
// App shell: a top bar naming the product and a side nav grouped the way the
// work happens (configure it, run it, read it). Later tasks add routes and
// views for the sections below; until then, items with no route yet render
// as inert labels rather than dead links.
type NavItem = { label: string; to?: string }
type NavSection = { label: string | null; items: NavItem[] }

const sections: NavSection[] = [
  { label: null, items: [{ label: 'Dashboard', to: '/' }] },
  {
    label: 'Setup',
    items: [
      { label: 'Prompts' },
      { label: 'Test Cases' },
      { label: 'Toolsets' },
      { label: 'Machines' },
    ],
  },
  {
    label: 'Evaluate',
    items: [{ label: 'Runs' }, { label: 'Results' }],
  },
  {
    label: 'Settings',
    items: [{ label: 'Workspaces' }],
  },
]
</script>

<template>
  <div class="app-shell">
    <header class="app-topbar">
      <span class="app-title">modelfit</span>
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
</style>
