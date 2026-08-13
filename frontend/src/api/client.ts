// Thin fetch wrapper shared by every frontend/src/api/*.ts module. Requests
// are same-origin under /api — in dev, vite.config.ts proxies that prefix to
// the FastAPI backend; in production the backend serves the SPA and the API
// from the same origin, so no base URL configuration is needed here.

export class ApiError extends Error {
  readonly status: number
  /** The parsed error body, if any — callers that need more than `message`
   * (e.g. the auth store reading `setup_required` off a 401) read it here
   * rather than every error shape growing its own constructor field. */
  readonly details: unknown

  constructor(status: number, message: string, details?: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.details = details
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const response = await fetch(`/api${path}`, {
    method,
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
    credentials: 'same-origin',
  })

  // 204 No Content and other empty bodies have nothing to parse.
  const text = await response.text()
  const data = text.length > 0 ? JSON.parse(text) : undefined

  if (!response.ok) {
    const message =
      (data && typeof data === 'object' && 'message' in data && typeof data.message === 'string'
        ? data.message
        : undefined) ?? response.statusText
    throw new ApiError(response.status, message, data)
  }

  return data as T
}

export const api = {
  get: <T>(path: string) => request<T>('GET', path),
  post: <T>(path: string, body?: unknown) => request<T>('POST', path, body),
  patch: <T>(path: string, body?: unknown) => request<T>('PATCH', path, body),
  delete: <T>(path: string) => request<T>('DELETE', path),
}
