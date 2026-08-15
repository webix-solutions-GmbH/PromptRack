// Tag severities for the two status vocabularies a run has: the run's own
// (`RunStatus`) and a single result row's (`ResultStatus`). Two maps rather
// than one because they are two vocabularies, not one with synonyms —
// `completed` is a run that reached its last row (partial errors included),
// `ok` is a row that answered — and the app deliberately never spells them the
// same way (see the note on `meh` vs `ok` in CLAUDE.md).
import type { ResultStatus, RunStatus } from '../api/runs'

type TagSeverity = 'secondary' | 'info' | 'success' | 'danger'

export const RUN_STATUS_SEVERITY: Record<RunStatus, TagSeverity> = {
  pending: 'secondary',
  running: 'info',
  completed: 'success',
  failed: 'danger',
}

export const RESULT_STATUS_SEVERITY: Record<ResultStatus, TagSeverity> = {
  pending: 'secondary',
  running: 'info',
  ok: 'success',
  error: 'danger',
}
