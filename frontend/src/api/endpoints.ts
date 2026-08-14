// The routes this module talks to (backend/app/api/endpoints.py):
//
//   GET    /api/endpoints                 -> Endpoint[]  (counts embedded, the
//                                            list page's "N/M loaded" column)
//   POST   /api/endpoints                  EndpointInput -> Endpoint
//   GET    /api/endpoints/{id}            -> Endpoint
//   PUT    /api/endpoints/{id}             EndpointInput -> Endpoint
//   DELETE /api/endpoints/{id}            -> (204)
//
// `api_key` is write-only: the response never carries it, only `has_api_key`.
// On `PUT` it is the one field that is *not* a full replacement — omit it and
// the stored key is left alone, send `null`/`""` to clear it deliberately, send
// a value to replace it. So a caller must not spread a form's empty string into
// the body, or every save wipes the key.
//   GET    /api/endpoints/{id}/models     -> EndpointModel[]  (loaded first)
//   POST   /api/endpoints/{id}/models      { model_id } -> EndpointModel  (manual
//                                            add; an upsert, so a model already
//                                            on the endpoint is answered with as
//                                            it stands rather than refused)
//   POST   /api/endpoints/{id}/discover   -> DiscoverModelsResult
//   POST   /api/endpoints/{id}/test       -> TestConnectionResult
//
// Discover/test answer 200 with a discriminated `ok` union rather than an
// HTTP error status, because an unreachable endpoint is an expected outcome
// of the probe itself, not a failure of the request to this API — same
// distinction the old Next.js buttons made.
import { api } from './client'

export interface Endpoint {
  id: number
  name: string
  base_url: string
  /** Whether a key is stored — the key itself never leaves the server. */
  has_api_key: boolean
  cpu: string | null
  ram: string | null
  gpu: string | null
  notes: string | null
  /** Shared with every workspace by the Base workspace that owns it. */
  is_global: boolean
  /** Whether *this* workspace owns the row — false only for a global endpoint
   * seen from elsewhere, which is exactly when edit/delete/API-key controls
   * are hidden rather than rendered disabled. */
  editable: boolean
  created_at: string
  updated_at: string
  model_count: number
  loaded_model_count: number
}

export interface EndpointInput {
  name: string
  base_url: string
  /** Omit to keep the stored key, `null` to clear it, a string to replace it. */
  api_key?: string | null
  cpu?: string | null
  ram?: string | null
  gpu?: string | null
  notes?: string | null
  /** Refused by the API outside the Base workspace. Patch-like on `PUT`: omit
   * to leave the stored flag alone, since it defaults to `false` and a caller
   * that does not know about sharing must not un-share the row on every save. */
  is_global?: boolean
}

export type EndpointModelSource = 'discovered' | 'manual' | 'run'

export interface EndpointModel {
  id: number
  endpoint_id: number
  model_id: string
  currently_loaded: boolean
  first_seen_at: string
  last_seen_at: string
  source: EndpointModelSource
}

export type DiscoverModelsResult =
  | { ok: true; discovered: number; retired: number; models: string[] }
  | { ok: false; error: string }

export type TestConnectionResult =
  | { ok: true; status: number; latency_ms: number }
  | { ok: false; error: string; status?: number }

export const endpointsApi = {
  list: () => api.get<Endpoint[]>('/endpoints'),
  get: (id: number) => api.get<Endpoint>(`/endpoints/${id}`),
  create: (input: EndpointInput) => api.post<Endpoint>('/endpoints', input),
  update: (id: number, input: EndpointInput) => api.put<Endpoint>(`/endpoints/${id}`, input),
  remove: (id: number) => api.delete<void>(`/endpoints/${id}`),
  listModels: (id: number) => api.get<EndpointModel[]>(`/endpoints/${id}/models`),
  addModel: (id: number, modelId: string) =>
    api.post<EndpointModel>(`/endpoints/${id}/models`, { model_id: modelId }),
  discover: (id: number) => api.post<DiscoverModelsResult>(`/endpoints/${id}/discover`),
  test: (id: number) => api.post<TestConnectionResult>(`/endpoints/${id}/test`),
}
