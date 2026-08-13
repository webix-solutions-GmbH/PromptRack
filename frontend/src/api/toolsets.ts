// Contract this is built against (Task 3.2, backend/app/api/toolsets.py —
// not yet landed alongside this task; see the plan's Task 3.2 section and
// backend/app/repos/toolsets.py, which this mirrors field-for-field).
// Assumed shape:
//
//   GET    /api/toolsets                 -> Toolset[]  (counts embedded)
//   POST   /api/toolsets                  ToolsetInput -> Toolset
//   GET    /api/toolsets/{id}            -> Toolset
//   PATCH  /api/toolsets/{id}             ToolsetInput -> Toolset
//   DELETE /api/toolsets/{id}            -> (204; cascades its tools)
//   GET    /api/toolsets/{id}/tools      -> Tool[]
//   POST   /api/toolsets/{id}/tools       ToolInput -> Tool  (manual toolsets)
//   PATCH  /api/tools/{id}                Partial<ToolInput & {enabled}> -> Tool
//   DELETE /api/tools/{id}               -> (204)
//   POST   /api/toolsets/{id}/discover   -> DiscoverToolsResult
//
// Discover answers 200 with a discriminated `ok` union rather than an HTTP
// error status — an unreachable MCP server is an expected probe outcome, not
// a failure of the request to this API. Same reasoning as `machines.ts`.
import { api } from './client'

export type ToolsetKind = 'manual' | 'mcp'

export interface Toolset {
  id: number
  name: string
  description: string | null
  kind: ToolsetKind
  mcp_url: string | null
  mcp_headers: string | null
  created_at: string
  updated_at: string
  tool_count: number
  enabled_tool_count: number
}

export interface ToolsetInput {
  name: string
  description?: string | null
  kind: ToolsetKind
  mcp_url?: string | null
  mcp_headers?: string | null
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

export type DiscoverToolsResult =
  | { ok: true; discovered: number; retired: number; tools: string[] }
  | { ok: false; error: string }

export const toolsetsApi = {
  list: () => api.get<Toolset[]>('/toolsets'),
  get: (id: number) => api.get<Toolset>(`/toolsets/${id}`),
  create: (input: ToolsetInput) => api.post<Toolset>('/toolsets', input),
  update: (id: number, input: ToolsetInput) => api.patch<Toolset>(`/toolsets/${id}`, input),
  remove: (id: number) => api.delete<void>(`/toolsets/${id}`),
  listTools: (toolsetId: number) => api.get<Tool[]>(`/toolsets/${toolsetId}/tools`),
  createTool: (toolsetId: number, input: ToolInput) =>
    api.post<Tool>(`/toolsets/${toolsetId}/tools`, input),
  updateTool: (toolId: number, input: Partial<ToolInput & { enabled: boolean }>) =>
    api.patch<Tool>(`/tools/${toolId}`, input),
  removeTool: (toolId: number) => api.delete<void>(`/tools/${toolId}`),
  discover: (id: number) => api.post<DiscoverToolsResult>(`/toolsets/${id}/discover`),
}
