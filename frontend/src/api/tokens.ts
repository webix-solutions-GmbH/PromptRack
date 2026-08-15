// The routes this module talks to (backend/app/api/tokens.py):
//
//   GET    /api/tokens          -> TokenView[]
//   POST   /api/tokens           CreateTokenRequest -> CreatedTokenView  (201)
//   DELETE /api/tokens/{id}      -> (204; 404 if not found)
//
// Ownership is baked into every query on the backend (`user_id = actor.user_id`),
// so this list is always "my tokens" — there is no admin view of another
// user's tokens and no endpoint that would need one.
import { api } from './client'

export interface TokenView {
  id: number
  name: string
  display_prefix: string
  created_at: string
  last_used_at: string | null
  expires_at: string | null
  revoked_at: string | null
}

/** Only the `POST` response carries the raw value — never stored, never
 * returned again by `GET`. Shown to the user exactly once. */
export interface CreatedTokenView extends TokenView {
  token: string
}

export interface CreateTokenRequest {
  name: string
  /** 1..3650. Omit or `null` for a token that never expires. */
  expires_in_days?: number | null
}

export const tokensApi = {
  list: () => api.get<TokenView[]>('/tokens'),
  create: (input: CreateTokenRequest) => api.post<CreatedTokenView>('/tokens', input),
  remove: (id: number) => api.delete<void>(`/tokens/${id}`),
}
