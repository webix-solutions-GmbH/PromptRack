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
//   GET    /api/toolsets/{id}/documents              -> Document[]  (metadata only)
//   GET    /api/toolsets/{id}/documents/{docId}      -> DocumentDetail
//   POST   /api/toolsets/{id}/documents               DocumentInput -> DocumentDetail
//   PUT    /api/toolsets/{id}/documents/{docId}       DocumentInput -> DocumentDetail
//   DELETE /api/toolsets/{id}/documents/{docId}      -> (204)
//   POST   /api/toolsets/{id}/documents/upload        multipart -> DocumentUploadResult
//   POST   /api/toolsets/{id}/documents/sync         -> DocumentToolSync
//
// There is no `GET /toolsets/{id}/tools`: a toolset's tools travel inside its
// own detail response (`ToolsetDetail.tools`), which is also what every
// mutation below answers with, so one read refreshes the whole page.
//
// Documents are the one child that *does* have its own list route, because the
// detail response carries them without their markdown (a corpus is megabytes,
// the page shows a table of paths) — so `GET .../documents/{docId}` is the only
// route that answers with `content` at all, and the list route exists so an
// upload or a delete can refresh the table without re-reading the toolset.
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

export type ToolsetKind = 'manual' | 'mcp' | 'documents'

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
  /** How many documents the corpus holds. Always present, and `0` for the kinds
   * that have no corpus — a `documents` toolset with an empty corpus is a real
   * state to show, since its three tools exist and answer "no documents". */
  document_count: number
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

/** How a tool row came to exist: authored in the UI, discovered from an MCP
 * server, or synthesized for a documents toolset. The three `documents` rows
 * are real `tools` rows — which is what makes the enabled flag, the collision
 * check and a run's `tools_snapshot` work on them untouched — but they carry no
 * `mock_response` and are never hand-authored, so the editor treats them as
 * read-only the same way it treats a discovered tool's name. */
export type ToolSource = 'manual' | 'mcp' | 'documents'

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

/** One document of a `documents` toolset's corpus, without its markdown — what
 * every list and every write's echo carries.
 *
 * `chars` and not bytes: it is the unit `read_document` windows in, so the
 * corpus table and the model measure a document in the same currency.
 */
export interface Document {
  id: number
  toolset_id: number
  title: string
  path: string
  chars: number
  created_at: string
  updated_at: string
}

/** One document *with* its markdown — the editor's read, and nothing else's. */
export interface DocumentDetail extends Document {
  content: string
}

export interface DocumentInput {
  /** The `read_document` key, e.g. `guides/refunds.md`. Normalised server-side
   * (backslashes, leading `./`, empty segments), and `..` is refused. */
  path: string
  /** Omit or leave blank to have it derived from the markdown's first heading,
   * falling back to the path's file stem. */
  title?: string | null
  content: string
}

/** One file's outcome in an upload. A rejected file is *not* a failed request:
 * the response is 200 with `ok: false` on that row, so uploading eight files
 * and having one saved as latin-1 does not discard the other seven. */
export interface DocumentUploadResult {
  filename: string
  ok: boolean
  /** Non-null exactly when `ok` — the corpus path the file landed at. */
  path: string | null
  /** Non-null exactly when `ok`. `false` means an existing document at that
   * path was replaced: re-uploading a path is a replace, not a conflict. */
  created: boolean | null
  /** Non-null exactly when `ok` is false. */
  error: string | null
}

export interface DocumentUploadResponse {
  created: number
  replaced: number
  failed: number
  results: DocumentUploadResult[]
  /** The whole corpus after the upload, so one call refreshes the table. */
  documents: Document[]
}

/** The documents counterpart of Discover: re-asserts the three synthesized tool
 * rows. Deliberately not an `ok`-discriminated union like `DiscoverToolsResult`
 * — there is no server to be unreachable, so there is no expected failure to
 * report in the body. */
export interface DocumentToolSync {
  created: number
  refreshed: number
  /** Always the three names, in the order the model is offered them. */
  tools: string[]
}

/** What every single-toolset route answers with: the toolset, its tools, and its
 * corpus as metadata only (never `content` — that is one document's own route). */
export interface ToolsetDetail extends Toolset {
  tools: Tool[]
  documents: Document[]
}

export type DiscoverToolsResult =
  | { ok: true; discovered: number; retired: number; tools: string[] }
  | { ok: false; error: string }

/** The three kinds, spelled once: the create dialog's SelectButton, the editor's
 * and the list's Tag all read from here rather than each writing `MCP` (or
 * `mcp`, or `manual`) out again. */
export const TOOLSET_KIND_OPTIONS: { label: string; value: ToolsetKind }[] = [
  { label: 'Manual', value: 'manual' },
  { label: 'MCP', value: 'mcp' },
  { label: 'Documents', value: 'documents' },
]

export function toolsetKindLabel(kind: ToolsetKind): string {
  return TOOLSET_KIND_OPTIONS.find((option) => option.value === kind)?.label ?? kind
}

/** The Tag severity each kind is shown with, spelled once beside the labels for
 * the same reason: the list and the detail heading must not drift into showing
 * the same kind in two different colours. */
export function toolsetKindSeverity(kind: ToolsetKind): 'info' | 'success' | 'secondary' {
  if (kind === 'mcp') return 'info'
  if (kind === 'documents') return 'success'
  return 'secondary'
}

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
  listDocuments: (toolsetId: number) => api.get<Document[]>(`/toolsets/${toolsetId}/documents`),
  getDocument: (toolsetId: number, documentId: number) =>
    api.get<DocumentDetail>(`/toolsets/${toolsetId}/documents/${documentId}`),
  createDocument: (toolsetId: number, input: DocumentInput) =>
    api.post<DocumentDetail>(`/toolsets/${toolsetId}/documents`, input),
  updateDocument: (toolsetId: number, documentId: number, input: DocumentInput) =>
    api.put<DocumentDetail>(`/toolsets/${toolsetId}/documents/${documentId}`, input),
  removeDocument: (toolsetId: number, documentId: number) =>
    api.delete<void>(`/toolsets/${toolsetId}/documents/${documentId}`),
  /** Multipart upload of one or more markdown files. The filename passed as the
   * third `FormData.append` argument *is* the corpus path, which is why this
   * takes the path explicitly rather than reading `file.name`: a folder picker
   * gives `webkitRelativePath`, and keeping it is how `guides/refunds.md` stays
   * `guides/refunds.md` instead of collapsing to `refunds.md`. */
  uploadDocuments: (toolsetId: number, files: { file: File; path: string }[]) => {
    const form = new FormData()
    for (const entry of files) form.append('files', entry.file, entry.path)
    return api.postForm<DocumentUploadResponse>(`/toolsets/${toolsetId}/documents/upload`, form)
  },
  syncDocumentTools: (toolsetId: number) =>
    api.post<DocumentToolSync>(`/toolsets/${toolsetId}/documents/sync`),
}
