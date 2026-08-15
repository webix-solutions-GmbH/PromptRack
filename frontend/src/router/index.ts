import { createRouter, createWebHistory } from 'vue-router'
import DashboardView from '../views/DashboardView.vue'
import LoginView from '../views/LoginView.vue'
import SetupView from '../views/SetupView.vue'
import EndpointsView from '../views/EndpointsView.vue'
import EndpointEditView from '../views/EndpointEditView.vue'
import ToolsetsView from '../views/ToolsetsView.vue'
import ToolsetEditView from '../views/ToolsetEditView.vue'
import PromptsView from '../views/PromptsView.vue'
import PromptEditView from '../views/PromptEditView.vue'
import TestCasesView from '../views/TestCasesView.vue'
import TestCaseEditView from '../views/TestCaseEditView.vue'
import CustomersView from '../views/CustomersView.vue'
import McpView from '../views/McpView.vue'
import RunsView from '../views/RunsView.vue'
import RunNewView from '../views/RunNewView.vue'
import RunDetailView from '../views/RunDetailView.vue'
import ResultsView from '../views/ResultsView.vue'
import { useAuthStore } from '../stores/auth'

declare module 'vue-router' {
  interface RouteMeta {
    /** Reachable while signed out. Everything else requires a session. */
    public?: boolean
  }
}

// Routes beyond the dashboard land in later tasks (prompts, runs, …); this
// scaffold wires just enough for the app shell to render and navigate.
const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      name: 'home',
      component: DashboardView,
    },
    {
      path: '/login',
      name: 'login',
      component: LoginView,
      meta: { public: true },
    },
    {
      path: '/setup',
      name: 'setup',
      component: SetupView,
      meta: { public: true },
    },
    {
      path: '/endpoints',
      name: 'endpoints',
      component: EndpointsView,
    },
    {
      path: '/endpoints/:id',
      name: 'endpoint-edit',
      component: EndpointEditView,
      props: true,
    },
    // Old paths, kept resolvable: a bookmark or an external link to
    // `/machines*` lands somewhere sensible rather than a 404.
    {
      path: '/machines',
      redirect: '/endpoints',
    },
    {
      path: '/machines/:id',
      redirect: (to) => ({ name: 'endpoint-edit', params: to.params }),
    },
    {
      path: '/prompts',
      name: 'prompts',
      component: PromptsView,
    },
    {
      path: '/prompts/:id',
      name: 'prompt-edit',
      component: PromptEditView,
      props: true,
    },
    {
      path: '/test-cases/new',
      name: 'test-case-new',
      component: TestCaseEditView,
    },
    {
      path: '/test-cases',
      name: 'test-cases',
      component: TestCasesView,
    },
    {
      path: '/test-cases/:id',
      name: 'test-case-edit',
      component: TestCaseEditView,
      props: true,
    },
    {
      path: '/toolsets',
      name: 'toolsets',
      component: ToolsetsView,
    },
    {
      path: '/toolsets/:id',
      name: 'toolset-edit',
      component: ToolsetEditView,
      props: true,
    },
    {
      path: '/workspaces',
      name: 'workspaces',
      component: CustomersView,
    },
    {
      path: '/mcp',
      name: 'mcp',
      component: McpView,
    },
    {
      path: '/runs/new',
      name: 'run-new',
      component: RunNewView,
    },
    {
      path: '/runs',
      name: 'runs',
      component: RunsView,
    },
    {
      path: '/runs/:id',
      name: 'run-detail',
      component: RunDetailView,
      props: true,
    },
    {
      path: '/results',
      name: 'results',
      component: ResultsView,
    },
  ],
})

// Single enforcement point for "signed in or not" on the client: unknown
// session state is resolved once (fetchMe is a no-op on later navigations,
// guarded by `initialized`), then every navigation is judged against it.
// This mirrors the old app's server-side guard in spirit — a page never
// renders past this point without an established session — but it is
// optimistic like the old proxy.ts, not authoritative: the API still
// enforces auth itself, this only keeps the SPA from showing a page it
// cannot use.
router.beforeEach(async (to) => {
  const auth = useAuthStore()
  if (!auth.initialized) {
    await auth.fetchMe()
  }

  if (auth.user) {
    // Signed in: /login and /setup have nothing left to offer.
    if (to.name === 'login' || to.name === 'setup') return { name: 'home' }
    return true
  }

  if (to.name === 'login' && auth.setupRequired) return { name: 'setup' }
  if (to.name === 'setup' && !auth.setupRequired) return { name: 'login' }
  if (to.meta.public) return true

  return {
    name: auth.setupRequired ? 'setup' : 'login',
    query: to.fullPath === '/' ? undefined : { redirect: to.fullPath },
  }
})

export default router
