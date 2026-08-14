// The routes this module talks to (backend/app/api/toolsets.py):
//
//   GET    /api/toolsets                             -> Toolset[]  (counts embedded)
//   POST   /api/toolsets                              ToolsetInput -> ToolsetDetail
//   GET    /api/toolsets/{id}                        -> ToolsetDetail
//   PUT    /api/toolsets/{id}                         ToolsetInput -> ToolsetDetail
//   DELETE /api/toolsets/{id}                        -> (204; cascades its tools)
//   POST   /api/toolsets/{id}/tools                   ToolInput -> Tool  (manual toolsets)
//   PUT    /api/toolsets/{id}/tools/{toolId}          ToolInput -> Tool
//   PUT    /api/toolsets/{id}/tools/{toolId}/enabled  { enabled } -> Tool
//   DELETE /api/toolsets/{id}/tools/{toolId}         -> (204)
//   POST   /api/toolsets/{id}/discover               -> DiscoverToolsResult
//
// There is no `GET /toolsets/{id}/tools`: a toolset's tools travel inside its
// own detail response (`ToolsetDetail.tools`), which is also what every
// mutation below answers with, so one read refreshes the whole page.
//
// A tool is addressed through its toolset rather than by bare id: that is what
// scopes it (`tools` carries no `customer_id`, only `toolset_id`), so the
// toolset is not decoration in the path. Enabling/disabling is its own route
// because the editor's route replaces the whole tool and `ToolInput` carries no
// `enabled` field at all.
//
// `mcp_headers` is write-only, exactly like an endpoint's `api_key`: the response
// carries only `has_mcp_headers`, and on `PUT` an omitted field leaves the
// stored headers alone while `null`/`""` clears them.
//
// Discover answers 200 with a discriminated `ok` union rather than an HTTP
// error status — an unreachable MCP server is an expected probe outcome, not
// a failure of the request to this API. Same reasoning as `endpoints.ts`.
import { api } from './client'

export type ToolsetKind = 'manual' | 'mcp'

export interface Toolset {
  id: number
  name: string
  description: string | null
  kind: ToolsetKind
  mcp_url: string | null
  /** Whether headers are stored — the headers themselves never leave the server. */
  has_mcp_headers: boolean
  /** Shared with every workspace by the Base workspace that owns it. */
  is_global: boolean
  /** Whether *this* workspace owns the row — false only for a global toolset
   * seen from elsewhere, which is exactly when edit/delete/discover controls
   * are hidden rather than rendered disabled. */
  editable: boolean
  created_at: string
  updated_at: string
  tool_count: number
  /** Discovery disables a vanished tool rather than deleting it, so the two
   * counts differ and "3/5 enabled" is the honest summary. */
  enabled_tool_count: number
}

export interface ToolsetInput {
  name: string
  description?: string | null
  kind: ToolsetKind
  mcp_url?: string | null
  /** Omit to keep the stored headers, `null` to clear them, a JSON object
   * string to replace them. */
  mcp_headers?: string | null
  /** Refused by the API outside the Base workspace. Patch-like on `PUT`: omit
   * to leave the stored flag alone, since it defaults to `false` and a caller
   * that does not know about sharing must not un-share the row on every save. */
  is_global?: boolean
}

export type ToolSource = 'manual' | 'mcp'

export interface Tool {
  id: number
  toolset_id: number
  name: string
  description: string | null
  parameters_json: string
  mock_response: string | null
  enabled: boolean
  source: ToolSource
  first_seen_at: string
  last_seen_at: string
}

export interface ToolInput {
  name: string
  description?: string | null
  parameters_json?: string
  mock_response?: string | null
}

/** What every single-toolset route answers with: the toolset plus its tools. */
export interface ToolsetDetail extends Toolset {
  tools: Tool[]
}

export type DiscoverToolsResult =
  | { ok: true; discovered: number; retired: number; tools: string[] }
  | { ok: false; error: string }

export const toolsetsApi = {
  list: () => api.get<Toolset[]>('/toolsets'),
  get: (id: number) => api.get<ToolsetDetail>(`/toolsets/${id}`),
  create: (input: ToolsetInput) => api.post<ToolsetDetail>('/toolsets', input),
  update: (id: number, input: ToolsetInput) => api.put<ToolsetDetail>(`/toolsets/${id}`, input),
  remove: (id: number) => api.delete<void>(`/toolsets/${id}`),
  createTool: (toolsetId: number, input: ToolInput) =>
    api.post<Tool>(`/toolsets/${toolsetId}/tools`, input),
  updateTool: (toolsetId: number, toolId: number, input: ToolInput) =>
    api.put<Tool>(`/toolsets/${toolsetId}/tools/${toolId}`, input),
  setToolEnabled: (toolsetId: number, toolId: number, enabled: boolean) =>
    api.put<Tool>(`/toolsets/${toolsetId}/tools/${toolId}/enabled`, { enabled }),
  removeTool: (toolsetId: number, toolId: number) =>
    api.delete<void>(`/toolsets/${toolsetId}/tools/${toolId}`),
  discover: (id: number) => api.post<DiscoverToolsResult>(`/toolsets/${id}/discover`),
}
