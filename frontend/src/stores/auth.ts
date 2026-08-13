// Session state for the whole SPA: who is signed in, their role's two
// booleans, and the active customer workspace. Everything else (route
// guards, the workspace switcher, role-gated controls) reads this store
// rather than re-deriving auth state of its own.
//
// Contract this is built against (Task 2.1, backend/app/auth/router.py —
// under construction alongside this task): `GET /api/auth/me` depends on
// the `current_user` guard and so answers 401 when signed out, with a body
// shaped `{ user, active_customer, can_write, can_administer }` when
// signed in. `POST /api/auth/{sign-up,login,logout,switch-customer}` exist
// per the plan but their response shapes are not specified — this store
// never depends on them beyond success/failure, and always re-reads
// `/auth/me` afterward for the canonical state, so a shape mismatch there
// cannot desync the store.
//
// Assumption (undocumented in the plan, flagged for reconciliation with
// Task 2.1): the 401 body from `/auth/me` carries `setup_required: true`
// when the `users` table is empty, so the router guard can send a fresh
// install to `/setup` instead of a login form with no account to log into.
// If the backend lands without this field, `setupRequired` simply stays
// false and every unauthenticated visitor lands on `/login`.
import { defineStore } from 'pinia'
import { api, ApiError } from '../api/client'

export type Role = 'admin' | 'member' | 'viewer'

export interface AuthUser {
  id: number
  email: string
  name: string
  role: Role
}

export interface ActiveCustomer {
  id: number
  name: string
}

export interface CustomerOption {
  id: number
  name: string
  archived: boolean
}

interface MeResponse {
  user: AuthUser
  active_customer: ActiveCustomer | null
  can_write: boolean
  can_administer: boolean
}

function hasSetupRequired(error: unknown): boolean {
  if (!(error instanceof ApiError) || error.status !== 401) return false
  const details = error.details
  return (
    typeof details === 'object' &&
    details !== null &&
    'setup_required' in details &&
    (details as { setup_required?: unknown }).setup_required === true
  )
}

export const useAuthStore = defineStore('auth', {
  state: () => ({
    user: null as AuthUser | null,
    activeCustomer: null as ActiveCustomer | null,
    canWrite: false,
    canAdminister: false,
    customers: [] as CustomerOption[],
    /** Set once the first `fetchMe()` (successful or not) resolves, so the
     * router guard knows not to flash `/login` before the session check
     * has had a chance to run. */
    initialized: false,
    /** True only after a 401 from `/auth/me` that reports an empty user
     * table — see the module doc above. */
    setupRequired: false,
  }),
  actions: {
    applyMe(me: MeResponse) {
      this.user = me.user
      this.activeCustomer = me.active_customer
      this.canWrite = me.can_write
      this.canAdminister = me.can_administer
    },
    clear() {
      this.user = null
      this.activeCustomer = null
      this.canWrite = false
      this.canAdminister = false
      this.customers = []
    },
    /** Resolves the current session. Never throws: a 401 is the expected
     * "signed out" response, not a failure to surface to the caller. */
    async fetchMe() {
      try {
        const me = await api.get<MeResponse>('/auth/me')
        this.applyMe(me)
        this.setupRequired = false
      } catch (error) {
        this.clear()
        this.setupRequired = hasSetupRequired(error)
      } finally {
        this.initialized = true
      }
    },
    async login(email: string, password: string) {
      await api.post('/auth/login', { email, password })
      await this.fetchMe()
    },
    async signUp(name: string, email: string, password: string) {
      await api.post('/auth/sign-up', { name, email, password })
      await this.fetchMe()
    },
    async logout() {
      await api.post('/auth/logout')
      this.clear()
      this.initialized = true
    },
    /** Populates the workspace switcher's options. Every signed-in user
     * may see and switch into every workspace — workspaces are a label,
     * not a tenant boundary — so this is not gated on `canAdminister`. */
    async fetchCustomers() {
      this.customers = await api.get<CustomerOption[]>('/customers')
    },
    async switchCustomer(customerId: number) {
      await api.post('/auth/switch-customer', { customer_id: customerId })
      await this.fetchMe()
    },
  },
})
