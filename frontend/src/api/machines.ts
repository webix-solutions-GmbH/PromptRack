// The routes this module talks to (backend/app/api/machines.py):
//
//   GET    /api/machines                 -> Machine[]  (counts embedded, the
//                                            list page's "N/M loaded" column)
//   POST   /api/machines                  MachineInput -> Machine
//   GET    /api/machines/{id}            -> Machine
//   PUT    /api/machines/{id}             MachineInput -> Machine
//   DELETE /api/machines/{id}            -> (204)
//
// `api_key` is write-only: the response never carries it, only `has_api_key`.
// On `PUT` it is the one field that is *not* a full replacement — omit it and
// the stored key is left alone, send `null`/`""` to clear it deliberately, send
// a value to replace it. So a caller must not spread a form's empty string into
// the body, or every save wipes the key.
//   GET    /api/machines/{id}/models     -> MachineModel[]  (loaded first)
//   POST   /api/machines/{id}/models      { model_id } -> MachineModel  (manual
//                                            add; an upsert, so a model already
//                                            on the machine is answered with as
//                                            it stands rather than refused)
//   POST   /api/machines/{id}/discover   -> DiscoverModelsResult
//   POST   /api/machines/{id}/test       -> TestConnectionResult
//
// Discover/test answer 200 with a discriminated `ok` union rather than an
// HTTP error status, because an unreachable endpoint is an expected outcome
// of the probe itself, not a failure of the request to this API — same
// distinction the old Next.js buttons made.
import { api } from './client'

export interface Machine {
  id: number
  name: string
  base_url: string
  /** Whether a key is stored — the key itself never leaves the server. */
  has_api_key: boolean
  cpu: string | null
  ram: string | null
  gpu: string | null
  notes: string | null
  created_at: string
  updated_at: string
  model_count: number
  loaded_model_count: number
}

export interface MachineInput {
  name: string
  base_url: string
  /** Omit to keep the stored key, `null` to clear it, a string to replace it. */
  api_key?: string | null
  cpu?: string | null
  ram?: string | null
  gpu?: string | null
  notes?: string | null
}

export type MachineModelSource = 'discovered' | 'manual' | 'run'

export interface MachineModel {
  id: number
  machine_id: number
  model_id: string
  currently_loaded: boolean
  first_seen_at: string
  last_seen_at: string
  source: MachineModelSource
}

export type DiscoverModelsResult =
  | { ok: true; discovered: number; retired: number; models: string[] }
  | { ok: false; error: string }

export type TestConnectionResult =
  | { ok: true; status: number; latency_ms: number }
  | { ok: false; error: string; status?: number }

export const machinesApi = {
  list: () => api.get<Machine[]>('/machines'),
  get: (id: number) => api.get<Machine>(`/machines/${id}`),
  create: (input: MachineInput) => api.post<Machine>('/machines', input),
  update: (id: number, input: MachineInput) => api.put<Machine>(`/machines/${id}`, input),
  remove: (id: number) => api.delete<void>(`/machines/${id}`),
  listModels: (id: number) => api.get<MachineModel[]>(`/machines/${id}/models`),
  addModel: (id: number, modelId: string) =>
    api.post<MachineModel>(`/machines/${id}/models`, { model_id: modelId }),
  discover: (id: number) => api.post<DiscoverModelsResult>(`/machines/${id}/discover`),
  test: (id: number) => api.post<TestConnectionResult>(`/machines/${id}/test`),
}
