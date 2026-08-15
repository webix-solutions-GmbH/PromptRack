"""A live probe of an endpoint's OpenAI-compatible `/models` route.

Powers two routes in `app.api.endpoints` that differ only in what they do
with the result: `POST /{id}/discover` (member) upserts what it finds into
`endpoint_models`, `POST /{id}/test` (admin — it exercises the stored API key)
just reports whether the endpoint answered. Both read credentials live, never
frozen into anything, per the content-vs-credentials split endpoints sit on.

Kept free of the database and of `app.repos` on purpose: this is a pure
network call plus response parsing, testable with `httpx.MockTransport` and
no Postgres (see `tests/test_discovery.py`), the same split `app.services
.attribution` makes for the version-matching rule.
"""

from dataclasses import dataclass
from time import monotonic

import httpx

#: `POST /discover` waits longer: it is a deliberate user action expected to
#: enumerate a possibly slow-to-answer server. `POST /test` is a quick health
#: check and should fail fast. Mirrors the old Next.js routes' 10s/5s split.
DISCOVER_TIMEOUT_S = 10.0
TEST_TIMEOUT_S = 5.0


@dataclass(frozen=True)
class ProbeResult:
    """What one live `GET {base_url}/models` attempt found out.

    `model_ids` is populated only when `ok` is true and the payload had the
    expected `{"data": [{"id": ...}, ...]}` shape — a 200 with something else
    is reported as a shape error, not silently treated as zero models.
    """

    ok: bool
    status: int | None
    latency_ms: int
    model_ids: list[str] | None
    error: str | None


def describe_transport_error(exc: httpx.HTTPError) -> str:
    """A short, human-readable reason for a probe that never got a response.

    httpx raises a distinct exception type per failure mode, so this is a
    straight lookup.
    """
    if isinstance(exc, httpx.TimeoutException):
        return "Connection timed out."
    if isinstance(exc, httpx.ConnectError):
        reason = str(exc.__cause__) if exc.__cause__ else str(exc)
        if reason:
            return f"Connection failed: {reason}"
        return "Connection refused — is the server running?"
    return str(exc) or "Connection failed."


async def probe_models(
    base_url: str,
    api_key: str | None,
    *,
    timeout: float,
    transport: httpx.AsyncBaseTransport | None = None,
) -> ProbeResult:
    """`GET {base_url}/models` — the OpenAI-compatible model list.

    `transport` is a test seam only (`httpx.MockTransport`); production
    callers never pass it and get a real connection.
    """
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else None
    started = monotonic()
    try:
        async with httpx.AsyncClient(timeout=timeout, transport=transport) as client:
            response = await client.get(f"{base_url}/models", headers=headers)
    except httpx.HTTPError as exc:
        return ProbeResult(
            ok=False,
            status=None,
            latency_ms=_elapsed_ms(started),
            model_ids=None,
            error=describe_transport_error(exc),
        )

    latency_ms = _elapsed_ms(started)
    if response.status_code >= 400:
        suffix = (
            " (unauthorized — check the API key)"
            if response.status_code in (401, 403)
            else ""
        )
        return ProbeResult(
            ok=False,
            status=response.status_code,
            latency_ms=latency_ms,
            model_ids=None,
            error=f"Request failed with status {response.status_code}{suffix}",
        )

    try:
        payload = response.json()
    except ValueError:
        return ProbeResult(
            ok=False,
            status=response.status_code,
            latency_ms=latency_ms,
            model_ids=None,
            error="Invalid JSON response from server.",
        )

    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        return ProbeResult(
            ok=False,
            status=response.status_code,
            latency_ms=latency_ms,
            model_ids=None,
            error='Unexpected response shape (expected {"data": [{"id": ...}, ...]}).',
        )

    model_ids = [
        item["id"]
        for item in data
        if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"]
    ]
    return ProbeResult(
        ok=True, status=response.status_code, latency_ms=latency_ms, model_ids=model_ids, error=None
    )


def _elapsed_ms(started: float) -> int:
    return round((monotonic() - started) * 1000)
