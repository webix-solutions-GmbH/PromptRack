// The routes this module talks to (backend/app/api/customers.py):
//
//   GET    /api/customers              -> Customer[]
//   POST   /api/customers               { name, description? } -> Customer
//   PUT    /api/customers/{id}          { name, description? } -> Customer
//   POST   /api/customers/{id}/archive  { archived } -> Customer
//   DELETE /api/customers/{id}          -> (204; RESTRICT/"holds N things"
//                                            refusal arrives as a normal
//                                            ApiError with a sentence message)
//
// `Customer` is a superset of the `CustomerOption` shape `stores/auth.ts`
// already reads off this same endpoint (`id`, `name`, `archived`) — adding
// `description`/`counts`/timestamps here does not change what that store
// depends on, so both can read `GET /customers` without disagreeing about
// its shape.
import { api } from './client'

export interface CustomerCounts {
  endpoints: number
  prompts: number
  toolsets: number
  test_groups: number
  runs: number
  total: number
}

export interface Customer {
  id: number
  name: string
  description: string | null
  archived: boolean
  /** The workspace that owns the global endpoints and toolsets. Read-only on
   * the wire — the client uses it to label Base in the switcher and to hide
   * the delete/archive controls the API would refuse anyway. */
  is_base: boolean
  created_at: string
  updated_at: string
  content: CustomerCounts
}

export interface CustomerInput {
  name: string
  description?: string | null
}

export const customersApi = {
  list: () => api.get<Customer[]>('/customers'),
  create: (input: CustomerInput) => api.post<Customer>('/customers', input),
  update: (id: number, input: CustomerInput) => api.put<Customer>(`/customers/${id}`, input),
  setArchived: (id: number, archived: boolean) =>
    api.post<Customer>(`/customers/${id}/archive`, { archived }),
  remove: (id: number) => api.delete<void>(`/customers/${id}`),
}
