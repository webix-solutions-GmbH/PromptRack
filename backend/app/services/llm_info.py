"""Best-effort probing of an OpenAI-compatible endpoint for metadata about the
server and the model a run is about to measure.

Called from `app.services.run_create` **outside** the transaction (it is a
network call, and a transaction must never wait on one) and frozen onto
`runs.llm_info` — same invariant as the endpoint snapshot: a past run never
changes.

The OpenAI surface itself is thin, so besides the model's entry in `/models`
this knocks on the provider-specific side doors of the servers this app cares
about (vLLM, Ollama, LM Studio). Every probe is optional: a wrong server
answers 404, an unreachable one times out, and either way the probe returns
whatever the rest was willing to reveal. It never raises — failing to gather
metadata must never fail run creation.

Kept free of the database and of `app.repos`, the same split
`app.services.discovery` draws: a network call plus response parsing,
testable with `httpx.MockTransport` and no Postgres.
"""

import asyncio
import json
import math
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

#: Per-probe timeout; the five probes run in parallel, so this is also roughly
#: the worst case for the whole call.
DEFAULT_PROBE_TIMEOUT_S = 4.0


@dataclass(frozen=True)
class LlmInfo:
    """What an endpoint was willing to say about itself and one model."""

    #: Detected server software, e.g. "vLLM" — None when nothing identified itself.
    server: str | None
    version: str | None
    #: Flat, display-ready metadata (context length, quantization, ...).
    details: dict[str, str]

    def to_json(self) -> dict[str, Any]:
        return {"server": self.server, "version": self.version, "details": self.details}


def serialize_llm_info(info: LlmInfo | None) -> str | None:
    """The `runs.llm_info` column, or None when nothing was learned."""
    return None if info is None else json.dumps(info.to_json())


def parse_llm_info(raw: str | None) -> LlmInfo | None:
    """Reads a frozen `llm_info` column back for display.

    Degrades to None rather than raising: a malformed snapshot must not keep a
    past run from rendering.
    """
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(parsed, dict):
        return None

    raw_details = parsed.get("details")
    details = (
        {key: value for key, value in raw_details.items() if isinstance(value, str)}
        if isinstance(raw_details, dict)
        else {}
    )
    server = parsed.get("server")
    version = parsed.get("version")
    return LlmInfo(
        server=server if isinstance(server, str) else None,
        version=version if isinstance(version, str) else None,
        details=details,
    )


def api_root(base_url: str) -> str:
    """`http://host:8000/v1` -> `http://host:8000`.

    The provider-specific APIs live *next to* `/v1`, not under it.
    """
    trimmed = base_url.rstrip("/")
    if trimmed.lower().endswith("/v1"):
        return trimmed[: -len("/v1")]
    return trimmed


# ---------------------------------------------------------------------------
# Pure extractors
# ---------------------------------------------------------------------------


def _scalar(value: Any) -> str | None:
    """Scalars become strings; everything else (arrays, objects, null) is dropped.

    Booleans are rendered `true`/`false` rather than Python's `True`/`False`:
    the value came out of JSON and is going back into a JSON snapshot that the
    UI shows verbatim.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return value or None
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return str(value) if math.isfinite(value) else None
    return None


def _add_scalars(
    target: dict[str, str], source: dict[str, Any], skip: frozenset[str]
) -> None:
    for key, value in source.items():
        if key in skip:
            continue
        text = _scalar(value)
        if text is not None:
            target[key] = text


_MODEL_ENTRY_SKIP = frozenset({"id", "object", "created", "permission", "parent"})


def extract_model_entry_details(payload: Any, model_id: str) -> dict[str, str]:
    """The scalar fields of this model's entry in a `/models` payload.

    vLLM enriches entries with e.g. `max_model_len`; other servers add little
    or nothing.
    """
    details: dict[str, str] = {}
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        return details

    entry = next(
        (item for item in data if isinstance(item, dict) and item.get("id") == model_id),
        None,
    )
    if not isinstance(entry, dict):
        return details

    _add_scalars(details, entry, _MODEL_ENTRY_SKIP)
    return details


#: `model_info` keys are namespaced (`general.architecture`,
#: `llama.context_length`, ...); only these well-known tails are kept.
_OLLAMA_MODEL_INFO_KEYS = frozenset(
    {"architecture", "context_length", "parameter_count", "embedding_length"}
)


def extract_ollama_show_details(payload: Any) -> dict[str, str]:
    """Flattens an Ollama `POST /api/show` payload: the `details` block (family,
    parameter size, quantization), the interesting `model_info` entries and the
    capability list.
    """
    details: dict[str, str] = {}
    if not isinstance(payload, dict):
        return details

    block = payload.get("details")
    if isinstance(block, dict):
        _add_scalars(details, block, frozenset())

    model_info = payload.get("model_info")
    if isinstance(model_info, dict):
        for key, value in model_info.items():
            short = key.split(".")[-1] if key else key
            if short not in _OLLAMA_MODEL_INFO_KEYS:
                continue
            text = _scalar(value)
            if text is not None:
                details[short] = text

    capabilities = payload.get("capabilities")
    if isinstance(capabilities, list):
        named = [item for item in capabilities if isinstance(item, str)]
        if named:
            details["capabilities"] = ", ".join(named)

    return details


_LMSTUDIO_SKIP = frozenset({"id", "object", "loaded_context_length"})


def extract_lmstudio_model_details(payload: Any) -> dict[str, str]:
    """Flattens an LM Studio `GET /api/v0/models/{id}` payload (arch, quant, ...)."""
    details: dict[str, str] = {}
    if not isinstance(payload, dict):
        return details
    _add_scalars(details, payload, _LMSTUDIO_SKIP)
    return details


# ---------------------------------------------------------------------------
# Probing
# ---------------------------------------------------------------------------


async def _fetch_json(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    json_body: dict[str, Any] | None = None,
) -> Any | None:
    """One probe. Any failure — transport, non-2xx, unparsable body — is None."""
    try:
        response = await client.request(method, url, json=json_body)
    except httpx.HTTPError:
        return None
    if not response.is_success:
        return None
    try:
        return response.json()
    except ValueError:
        return None


async def probe_llm_info(
    base_url: str,
    api_key: str | None,
    model_id: str,
    *,
    timeout: float = DEFAULT_PROBE_TIMEOUT_S,
    transport: httpx.AsyncBaseTransport | None = None,
) -> LlmInfo | None:
    """Probes the endpoint and returns whatever it revealed, or None when no
    probe yielded anything.

    `transport` is a test seam only (`httpx.MockTransport`); production callers
    never pass it and get a real connection.
    """
    root = api_root(base_url)
    base = base_url.rstrip("/")
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    async with httpx.AsyncClient(
        timeout=timeout, transport=transport, headers=headers
    ) as client:
        models_payload, vllm_version, ollama_version, ollama_show, lmstudio_model = (
            await asyncio.gather(
                _fetch_json(client, "GET", f"{base}/models"),
                _fetch_json(client, "GET", f"{root}/version"),
                _fetch_json(client, "GET", f"{root}/api/version"),
                _fetch_json(client, "POST", f"{root}/api/show", json_body={"model": model_id}),
                _fetch_json(
                    client, "GET", f"{root}/api/v0/models/{quote(model_id, safe='')}"
                ),
            )
        )

    details = extract_model_entry_details(models_payload, model_id)
    server: str | None = None
    version: str | None = None

    if isinstance(vllm_version, dict) and isinstance(vllm_version.get("version"), str):
        server = "vLLM"
        version = vllm_version["version"]

    ollama_reported_version = isinstance(ollama_version, dict) and isinstance(
        ollama_version.get("version"), str
    )
    if ollama_show is not None or ollama_reported_version:
        server = "Ollama"
        if ollama_reported_version:
            version = ollama_version["version"]
        details.update(extract_ollama_show_details(ollama_show))

    if lmstudio_model is not None:
        server = "LM Studio"
        details.update(extract_lmstudio_model_details(lmstudio_model))

    if server is None and not details:
        return None
    return LlmInfo(server=server, version=version, details=details)
