// The admin half (backend/app/api/invites.py, Admin-guarded):
//
//   GET    /api/invites          -> InviteView[]   (pending first, then spent)
//   POST   /api/invites           CreateInviteRequest -> CreatedInviteView (201)
//   DELETE /api/invites/{id}      -> (204; 409 if the invite was already used)
//
// and the public redemption half, which lives in backend/app/auth/router.py
// for the obvious reason that whoever opens a link has no account yet:
//
//   GET    /api/auth/invite/{token}          -> InviteOfferView
//                                               (404 unknown, 410 spent/expired)
//   POST   /api/auth/invite/{token}/accept    AcceptInviteRequest -> MeResponse
//                                               (201, sets the session cookie;
//                                                409 on a duplicate email)
//
// An invite names a **role, not a person**: the admin does not have to know
// the address in advance, and whoever opens the link first supplies their own.
// The raw token comes back exactly once, on the `POST` that mints it — the
// same one-time reveal an API token gets — so `GET` can only ever show the
// display prefix.
//
// Redemption is called from `InviteAcceptView` through this module rather than
// through the auth store, since it runs before there is a user; the store's
// `applyMe` takes the `MeResponse` it returns, which is why that type is
// imported here rather than described a second time.
import { api } from './client'
import type { Role } from '../lib/roles'
import type { MeResponse } from '../stores/auth'

export type InviteStatus = 'pending' | 'redeemed' | 'revoked' | 'expired'

export interface InviteView {
  id: number
  role: Role
  display_prefix: string
  /** Derived server-side: "expired" is a comparison against *now*, and the
   * same rule decides whether a redemption is allowed. */
  status: InviteStatus
  expires_at: string
  created_at: string
  created_by_name: string | null
  redeemed_at: string | null
  redeemed_by_name: string | null
}

/** Only the `POST` response carries the link — never stored (only its hash
 * is), never returned again by `GET`. Shown to the admin exactly once. */
export interface CreatedInviteView extends InviteView {
  url: string
}

export interface CreateInviteRequest {
  role: Role
  /** 1..90, defaulting to 7 server-side. Not nullable, unlike an API token's
   * expiry — a link that lets a stranger create an account never lives
   * forever — so omit the key rather than sending `null`. */
  expires_in_days?: number
}

/** What the invite page may know before anyone is signed in: the role the link
 * grants and how long it lasts. Never who created it, and never an address. */
export interface InviteOfferView {
  role: Role
  expires_at: string
}

export interface AcceptInviteRequest {
  email: string
  password: string
  /** Falls back to the address server-side when blank. */
  name?: string | null
}

export const invitesApi = {
  list: () => api.get<InviteView[]>('/invites'),
  create: (input: CreateInviteRequest) => api.post<CreatedInviteView>('/invites', input),
  revoke: (id: number) => api.delete<void>(`/invites/${id}`),
  read: (token: string) =>
    api.get<InviteOfferView>(`/auth/invite/${encodeURIComponent(token)}`),
  accept: (token: string, input: AcceptInviteRequest) =>
    api.post<MeResponse>(`/auth/invite/${encodeURIComponent(token)}/accept`, input),
}
