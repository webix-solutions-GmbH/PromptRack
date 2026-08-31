// `/api/param-groups` — named, reusable request-param presets. Mirrors
// `backend/app/api/param_groups.py`. A group is a *patch* merged between an
// endpoint's `default_params` and a run's own overrides, so a `null` value is
// meaningful: it unsets an endpoint default (the merge drops it afterwards, so
// a null never reaches the wire).
import { api } from './client'

export interface ParamGroup {
  id: number
  name: string
  description: string | null
  params: Record<string, unknown>
  created_at: string
  updated_at: string
}

export interface ParamGroupInput {
  name: string
  description?: string | null
  params: Record<string, unknown>
}

export const paramGroupsApi = {
  list: () => api.get<ParamGroup[]>('/param-groups'),
  get: (id: number) => api.get<ParamGroup>(`/param-groups/${id}`),
  create: (input: ParamGroupInput) => api.post<ParamGroup>('/param-groups', input),
  update: (id: number, input: ParamGroupInput) =>
    api.put<ParamGroup>(`/param-groups/${id}`, input),
  remove: (id: number) => api.delete<void>(`/param-groups/${id}`),
}
