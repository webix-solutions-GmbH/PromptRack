// Session state for the whole SPA: who is signed in, their role's two
// booleans, and the active customer workspace. Everything else (route
// guards, the workspace switcher, role-gated controls) reads this store
// rather than re-deriving auth state of its own.
//
// Contract this is built against (backend/app/auth/router.py): `GET
// /api/auth/me` depends on the `current_user` guard and so answers 401 when
// signed out, with a body shaped `{ user, active_customer, can_write,
// can_administer }` when signed in. `POST /api/auth/{sign-up,login,logout,
// switch-customer}` exist but their response shapes are not specified —
// this store never depends on them beyond success/failure, and always
// re-reads `/auth/me` afterward for the canonical state, so a shape
// mismatch there cannot desync the store.
//
// Whether an install still needs its first account is a separate question
// from who is signed in, and it has its own endpoint: `GET /api/auth/status`
// answers `{ signup_open }` to anyone, open exactly while the `users` table
// is empty. It is only asked after a 401, so a signed-in session costs one
// request as before.
import { defineStore } from 'pinia'
import { api } from '../api/client'
import type { Role } from '../lib/roles'

// Re-exported rather than restated: `lib/roles.ts` owns the role vocabulary,
// mirroring `backend/app/auth/policy.py`. Importers that had this from the
// store keep working, and there is still only one list to correct when a role
// is added to the column.
export type { Role } from '../lib/roles'

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
  /** The workspace that owns the global endpoints and toolsets. Only one
   * `CustomerOption` in the list ever carries this true. */
  is_base: boolean
}

/** Exported so `api/invites.ts` can type redemption's response as the thing
 * `applyMe` takes, rather than describing this shape a second time — the
 * invite-accept route answers with exactly what `/auth/me` does. */
export interface MeResponse {
  user: AuthUser
  active_customer: ActiveCustomer | null
  can_write: boolean
  can_administer: boolean
}

interface StatusResponse {
  signup_open: boolean
}

/** Never throws: an unreachable status endpoint means "not a fresh install",
 * which lands the visitor on `/login` rather than an unusable setup form. */
async function signupOpen(): Promise<boolean> {
  try {
    return (await api.get<StatusResponse>('/auth/status')).signup_open
  } catch {
    return false
  }
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
    /** True while the install has no account yet — signed out and
     * `/auth/status` reports sign-up open. */
    setupRequired: false,
  }),
  getters: {
    /** Whether the active workspace is Base — the one workspace whose
     * endpoints and toolsets are shared globally, and the only place a
     * global row's "Global" checkbox is offered at all.
     *
     * Derived from `customers` (`GET /customers`, `app.api.customers`'s
     * `CustomerView`, which carries `is_base`) rather than from
     * `activeCustomer` (`GET /auth/me`'s `active_customer`): that response
     * is built from a *different*, narrower `CustomerView` defined locally
     * in `app.auth.router` — `{id, name, archived}` only — that does not
     * carry `is_base` at all. Same name, different shape; see this task's
     * report for the drift this papers over. */
    isBaseWorkspace(state): boolean {
      return state.customers.find((c) => c.id === state.activeCustomer?.id)?.is_base ?? false
    },
  },
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
      } catch {
        this.clear()
        this.setupRequired = await signupOpen()
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
