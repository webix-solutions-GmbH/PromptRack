"""`app.services.discovery.probe_models` — parsing and error-mapping only.

No database, no real network: `httpx.MockTransport` stands in for the
endpoint's OpenAI-compatible endpoint, keeping this in the fast suite next to
`test_attribution.py` and `test_llm.py`.
"""

from __future__ import annotations

import httpx

from app.services.discovery import probe_models


def _client_for(handler):
    return httpx.MockTransport(handler)


async def test_a_successful_response_lists_model_ids():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/models"
        return httpx.Response(200, json={"data": [{"id": "qwen3-32b"}, {"id": "llama3"}]})

    result = await probe_models("http://x/v1", None, timeout=1, transport=_client_for(handler))

    assert result.ok is True
    assert result.status == 200
    assert result.model_ids == ["qwen3-32b", "llama3"]
    assert result.error is None
    assert result.latency_ms >= 0


async def test_the_api_key_is_sent_as_a_bearer_token():
    seen: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers.get("authorization")
        return httpx.Response(200, json={"data": []})

    await probe_models("http://x/v1", "s3cret", timeout=1, transport=_client_for(handler))

    assert seen["authorization"] == "Bearer s3cret"


async def test_no_api_key_sends_no_authorization_header():
    seen: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers.get("authorization")
        return httpx.Response(200, json={"data": []})

    await probe_models("http://x/v1", None, timeout=1, transport=_client_for(handler))

    assert seen["authorization"] is None


async def test_an_empty_model_list_is_still_ok():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": []})

    result = await probe_models("http://x/v1", None, timeout=1, transport=_client_for(handler))

    assert result.ok is True
    assert result.model_ids == []


async def test_non_string_or_blank_ids_are_dropped():
    def handler(request: httpx.Request) -> httpx.Response:
        items = [{"id": "good"}, {"id": ""}, {"id": 7}, {"not_id": "x"}, "not-an-object"]
        return httpx.Response(200, json={"data": items})

    result = await probe_models("http://x/v1", None, timeout=1, transport=_client_for(handler))

    assert result.model_ids == ["good"]


async def test_unauthorized_is_reported_with_a_hint():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401)

    result = await probe_models("http://x/v1", "wrong", timeout=1, transport=_client_for(handler))

    assert result.ok is False
    assert result.status == 401
    assert result.model_ids is None
    assert "unauthorized" in result.error


async def test_forbidden_also_gets_the_api_key_hint():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403)

    result = await probe_models("http://x/v1", "wrong", timeout=1, transport=_client_for(handler))

    assert "unauthorized" in result.error


async def test_a_server_error_is_reported_without_the_api_key_hint():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    result = await probe_models("http://x/v1", None, timeout=1, transport=_client_for(handler))

    assert result.ok is False
    assert result.status == 500
    assert "unauthorized" not in result.error


async def test_invalid_json_is_reported():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json")

    result = await probe_models("http://x/v1", None, timeout=1, transport=_client_for(handler))

    assert result.ok is False
    assert "JSON" in result.error


async def test_an_unexpected_shape_is_reported():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": "not-a-list"})

    result = await probe_models("http://x/v1", None, timeout=1, transport=_client_for(handler))

    assert result.ok is False
    assert "shape" in result.error


async def test_a_missing_data_key_is_also_a_shape_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"models": []})

    result = await probe_models("http://x/v1", None, timeout=1, transport=_client_for(handler))

    assert result.ok is False
    assert "shape" in result.error


async def test_a_connection_error_is_reported_without_a_status():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    result = await probe_models("http://x/v1", None, timeout=1, transport=_client_for(handler))

    assert result.ok is False
    assert result.status is None
    assert result.model_ids is None
    assert result.error


async def test_a_timeout_is_reported_as_timed_out():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("slow", request=request)

    result = await probe_models("http://x/v1", None, timeout=1, transport=_client_for(handler))

    assert result.ok is False
    assert result.error == "Connection timed out."
