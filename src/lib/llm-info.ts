/**
 * Best-effort probing of an OpenAI-compatible endpoint for information about
 * the server and the model that is about to be evaluated.
 *
 * The OpenAI surface itself is deliberately thin, so besides the model's entry
 * in `/v1/models` we knock on the provider-specific side doors of the servers
 * we care about (vLLM, Ollama, LM Studio). Every probe is optional: wrong
 * server → fast 404, unreachable server → short timeout. Whatever answers is
 * merged into one flat, display-ready key/value map and snapshotted onto the
 * run — same invariant as the machine snapshot, a past run never changes.
 */

export interface LlmInfo {
  /** Detected server software, e.g. "vLLM" — null when nothing identified itself. */
  server: string | null;
  version: string | null;
  /** Flat display-ready metadata (context length, quantization, ...). */
  details: Record<string, string>;
}

export interface ProbeOptions {
  baseUrl: string;
  apiKey?: string | null;
  modelId: string;
  /** Per-probe timeout; probes run in parallel. */
  timeoutMs?: number;
}

const DEFAULT_PROBE_TIMEOUT_MS = 4_000;

/** `http://host:8000/v1` → `http://host:8000` (provider APIs live next to /v1). */
export function apiRoot(baseUrl: string): string {
  return baseUrl.replace(/\/+$/, '').replace(/\/v1$/i, '');
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/** Scalars become strings; everything else (arrays, objects, null) is dropped. */
function scalar(value: unknown): string | null {
  if (typeof value === 'string') return value.length > 0 ? value : null;
  if (typeof value === 'number' && Number.isFinite(value)) return String(value);
  if (typeof value === 'boolean') return String(value);
  return null;
}

function addScalars(
  target: Record<string, string>,
  source: Record<string, unknown>,
  skip: ReadonlySet<string>,
): void {
  for (const [key, value] of Object.entries(source)) {
    if (skip.has(key)) continue;
    const text = scalar(value);
    if (text !== null) target[key] = text;
  }
}

// ---------------------------------------------------------------------------
// Pure extractors (unit-testable)
// ---------------------------------------------------------------------------

const MODEL_ENTRY_SKIP = new Set(['id', 'object', 'created', 'permission', 'parent']);

/**
 * Pulls the scalar fields of this model's entry out of a `/v1/models` payload.
 * vLLM enriches entries with e.g. `max_model_len`; others add little or nothing.
 */
export function extractModelEntryDetails(
  payload: unknown,
  modelId: string,
): Record<string, string> {
  const details: Record<string, string> = {};
  const list = isRecord(payload) ? payload.data : null;
  if (!Array.isArray(list)) return details;

  const entry = list.find((item) => isRecord(item) && item.id === modelId);
  if (!isRecord(entry)) return details;

  addScalars(details, entry, MODEL_ENTRY_SKIP);
  return details;
}

/**
 * Flattens an Ollama `POST /api/show` payload: the `details` block (family,
 * parameter size, quantization), the interesting `model_info` entries and the
 * capability list.
 */
export function extractOllamaShowDetails(payload: unknown): Record<string, string> {
  const details: Record<string, string> = {};
  if (!isRecord(payload)) return details;

  if (isRecord(payload.details)) {
    addScalars(details, payload.details, new Set());
  }

  if (isRecord(payload.model_info)) {
    for (const [key, value] of Object.entries(payload.model_info)) {
      // model_info keys are namespaced (`general.architecture`,
      // `llama.context_length`, ...); keep the well-known interesting ones.
      const short = key.split('.').pop() ?? key;
      if (!['architecture', 'context_length', 'parameter_count', 'embedding_length'].includes(short)) {
        continue;
      }
      const text = scalar(value);
      if (text !== null) details[short] = text;
    }
  }

  if (Array.isArray(payload.capabilities)) {
    const capabilities = payload.capabilities.filter(
      (item): item is string => typeof item === 'string',
    );
    if (capabilities.length > 0) details.capabilities = capabilities.join(', ');
  }

  return details;
}

const LMSTUDIO_SKIP = new Set(['id', 'object', 'loaded_context_length']);

/** Flattens an LM Studio `GET /api/v0/models/{id}` payload (arch, quant, ...). */
export function extractLmStudioModelDetails(payload: unknown): Record<string, string> {
  const details: Record<string, string> = {};
  if (!isRecord(payload)) return details;
  addScalars(details, payload, LMSTUDIO_SKIP);
  return details;
}

// ---------------------------------------------------------------------------
// Probing
// ---------------------------------------------------------------------------

async function fetchJson(
  url: string,
  init: { method?: string; headers?: Record<string, string>; body?: string },
  timeoutMs: number,
): Promise<unknown | null> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(url, { ...init, signal: controller.signal });
    if (!response.ok) return null;
    return (await response.json()) as unknown;
  } catch {
    return null;
  } finally {
    clearTimeout(timeout);
  }
}

/**
 * Probes the endpoint and returns whatever it was willing to reveal, or null
 * when no probe yielded anything. Never throws — failing to gather metadata
 * must never fail run creation.
 */
export async function probeLlmInfo(options: ProbeOptions): Promise<LlmInfo | null> {
  const { baseUrl, apiKey, modelId } = options;
  const timeoutMs = options.timeoutMs ?? DEFAULT_PROBE_TIMEOUT_MS;
  const root = apiRoot(baseUrl);
  const base = baseUrl.replace(/\/+$/, '');
  const authHeaders: Record<string, string> = apiKey
    ? { Authorization: `Bearer ${apiKey}` }
    : {};

  const [modelsPayload, vllmVersion, ollamaVersion, ollamaShow, lmStudioModel] =
    await Promise.all([
      fetchJson(`${base}/models`, { headers: authHeaders }, timeoutMs),
      fetchJson(`${root}/version`, { headers: authHeaders }, timeoutMs),
      fetchJson(`${root}/api/version`, { headers: authHeaders }, timeoutMs),
      fetchJson(
        `${root}/api/show`,
        {
          method: 'POST',
          headers: { ...authHeaders, 'Content-Type': 'application/json' },
          body: JSON.stringify({ model: modelId }),
        },
        timeoutMs,
      ),
      fetchJson(
        `${root}/api/v0/models/${encodeURIComponent(modelId)}`,
        { headers: authHeaders },
        timeoutMs,
      ),
    ]);

  const details = extractModelEntryDetails(modelsPayload, modelId);

  let server: string | null = null;
  let version: string | null = null;

  if (isRecord(vllmVersion) && typeof vllmVersion.version === 'string') {
    server = 'vLLM';
    version = vllmVersion.version;
  }

  if (ollamaShow !== null || (isRecord(ollamaVersion) && typeof ollamaVersion.version === 'string')) {
    server = 'Ollama';
    version = isRecord(ollamaVersion) && typeof ollamaVersion.version === 'string'
      ? ollamaVersion.version
      : version;
    Object.assign(details, extractOllamaShowDetails(ollamaShow));
  }

  if (lmStudioModel !== null) {
    server = 'LM Studio';
    Object.assign(details, extractLmStudioModelDetails(lmStudioModel));
  }

  if (server === null && Object.keys(details).length === 0) return null;
  return { server, version, details };
}

// ---------------------------------------------------------------------------
// Snapshot parsing (for display)
// ---------------------------------------------------------------------------

/** Parses a run's frozen `llm_info` JSON back into an LlmInfo, null when absent/invalid. */
export function parseLlmInfo(raw: string | null): LlmInfo | null {
  if (!raw) return null;
  try {
    const parsed: unknown = JSON.parse(raw);
    if (!isRecord(parsed)) return null;

    const details: Record<string, string> = {};
    if (isRecord(parsed.details)) {
      for (const [key, value] of Object.entries(parsed.details)) {
        if (typeof value === 'string') details[key] = value;
      }
    }

    return {
      server: typeof parsed.server === 'string' ? parsed.server : null,
      version: typeof parsed.version === 'string' ? parsed.version : null,
      details,
    };
  } catch {
    return null;
  }
}
