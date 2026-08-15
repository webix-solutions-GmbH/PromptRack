// The routes this module talks to (backend/app/api/users.py):
//
//   GET    /api/users                     -> UserView[]
//   PUT    /api/users/{id}/role            { role } -> UserView
//   POST   /api/users/{id}/deactivate      -> UserView
//   POST   /api/users/{id}/reactivate      -> UserView
//   DELETE /api/users/{id}                 -> (204)
//
// plus one route that lives in backend/app/auth/router.py rather than in
// `app.api.users`, because it answers whether the *optional* OIDC router was
// mounted at all and so cannot live inside it:
//
//   GET    /api/auth/oidc-status          -> OidcStatusView
//
// It is read here rather than in a module of its own because it is Admin-only
// like everything above and renders as one tab of the same `/users` page.
//
// Every route is Admin-guarded. Three refusals arrive as ordinary `ApiError`s
// with a sentence to show: acting on your own account (409), a change that
// would leave no administrator who can sign in (409), and an unrecognised role
// (400 — the backend refuses rather than coercing, deliberately unlike
// `parse_role`).
import { api } from './client'
import type { Role } from '../lib/roles'

export interface UserView {
  id: number
  email: string
  name: string
  role: Role
  /** The presence of a timestamp *is* the deactivation — there is no boolean
   * flag beside it that could drift from it. */
  disabled_at: string | null
  created_at: string
  /** How the account signs in. Booleans, never the underlying hash or
   * subject claim. */
  has_password: boolean
  has_oidc: boolean
}

export interface RoleRequest {
  role: Role
}

/** `GET /api/auth/oidc-status` — the identity provider as the environment
 * configures it. Read-only: changing SSO means editing the environment and
 * redeploying, so there is no write half to this. */
export interface OidcStatusView {
  configured: boolean
  issuer: string | null
  client_id: string | null
  scopes: string[]
  /** Reported through `parse_role`, so this is the role a provisioned account
   * actually lands at rather than the raw env string. */
  default_role: Role
  /** The secret itself is never serialized — this is all that is said of it. */
  secret_set: boolean
}

export const usersApi = {
  list: () => api.get<UserView[]>('/users'),
  setRole: (id: number, role: Role) => api.put<UserView>(`/users/${id}/role`, { role }),
  deactivate: (id: number) => api.post<UserView>(`/users/${id}/deactivate`),
  reactivate: (id: number) => api.post<UserView>(`/users/${id}/reactivate`),
  remove: (id: number) => api.delete<void>(`/users/${id}`),
  oidcStatus: () => api.get<OidcStatusView>('/auth/oidc-status'),
}
