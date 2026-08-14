// The route this module talks to (backend/app/api/version.py):
//
//   GET /api/version -> { version, commit }
//
// Unauthenticated, like /api/health — reachable before a session exists, so
// the app shell can render it without gating on auth state.
import { api } from './client'

export interface Version {
  version: string
  commit: string | null
}

export const versionApi = {
  get: () => api.get<Version>('/version'),
}
