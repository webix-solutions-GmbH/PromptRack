"""`app.api.mocks` — the mock LLM and mock MCP routes.

Exercised through `TestClient` against the real app (like `test_health.py`),
not `httpx.MockTransport`: these routes *are* the endpoint under test, not a
stand-in for one. `mocks_enabled()` reads `app.config.get_settings()`, so the
gating tests monkeypatch `app.api.mocks.get_settings` directly rather than
touching environment variables and the `lru_cache`d singleton.
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.api import mocks as mocks_module
from app.config import Settings
from app.main import app


def _client() -> TestClient:
    return TestClient(app)


def _settings(**overrides: object) -> Settings:
    return Settings(**{"environment": "development", "enable_mocks": False, **overrides})


def _parse_sse(body: str) -> list[dict | str]:
    """Every `data:` payload of an SSE body, `[DONE]` kept as the bare string."""
    events: list[dict | str] = []
    for line in body.splitlines():
        if not line.startswith("data:"):
            continue
        raw = line[len("data:") :].strip()
        events.append(raw if raw == "[DONE]" else json.loads(raw))
    return events


def _delta_key(event: dict | str, key: str) -> object | None:
    """`choices[0].delta[key]` of one parsed SSE event, or None when absent."""
    if not isinstance(event, dict) or not event.get("choices"):
        return None
    return event["choices"][0]["delta"].get(key)


def _finish_reason(event: dict | str) -> str | None:
    if not isinstance(event, dict) or not event.get("choices"):
        return None
    return event["choices"][0].get("finish_reason")


# ---------------------------------------------------------------------------
# Gating
# ---------------------------------------------------------------------------


def test_mock_llm_models_answers_in_development():
    response = _client().get("/api/mock-llm/models")
    assert response.status_code == 200
    ids = [model["id"] for model in response.json()["data"]]
    assert ids == ["mock-fast-7b", "mock-slow-70b"]


def test_mocks_404_in_production_without_enable_mocks(monkeypatch):
    monkeypatch.setattr(mocks_module, "get_settings", lambda: _settings(environment="production"))
    response = _client().get("/api/mock-llm/models")
    assert response.status_code == 404


def test_enable_mocks_overrides_production(monkeypatch):
    monkeypatch.setattr(
        mocks_module,
        "get_settings",
        lambda: _settings(environment="production", enable_mocks=True),
    )
    response = _client().get("/api/mock-llm/models")
    assert response.status_code == 200


def test_mock_mcp_also_404s_when_disabled(monkeypatch):
    monkeypatch.setattr(mocks_module, "get_settings", lambda: _settings(environment="production"))
    response = _client().post("/api/mock-mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Mock LLM — chat completions
# ---------------------------------------------------------------------------


def test_invalid_json_body_is_a_400():
    response = _client().post(
        "/api/mock-llm/chat/completions",
        content=b"not json",
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 400
    assert "message" in response.json()["error"]


def test_trigger_error_answers_500():
    response = _client().post(
        "/api/mock-llm/chat/completions",
        json={"model": "mock-fast-7b", "messages": [{"role": "user", "content": "TRIGGER_ERROR"}]},
    )
    assert response.status_code == 500
    assert response.json()["error"]["type"] == "mock_error"


def test_a_plain_prompt_streams_content_then_usage_then_done():
    response = _client().post(
        "/api/mock-llm/chat/completions",
        json={"model": "mock-fast-7b", "messages": [{"role": "user", "content": "hello there"}]},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")

    events = _parse_sse(response.text)
    assert events[0]["choices"][0]["delta"] == {"role": "assistant"}
    assert events[-1] == "[DONE]"

    content_deltas = [_delta_key(event, "content") for event in events]
    full_text = "".join(text for text in content_deltas if text)
    assert "hello there" in full_text

    finish_reasons = [reason for event in events if (reason := _finish_reason(event))]
    assert finish_reasons[-1] == "stop"

    usage_events = [event for event in events if isinstance(event, dict) and "usage" in event]
    assert len(usage_events) == 1
    usage = usage_events[0]["usage"]
    assert usage["total_tokens"] == usage["prompt_tokens"] + usage["completion_tokens"]
    assert usage["completion_tokens"] > 0


def test_a_tool_result_is_quoted_back_in_the_answer():
    response = _client().post(
        "/api/mock-llm/chat/completions",
        json={
            "model": "mock-fast-7b",
            "messages": [
                {"role": "user", "content": "what happened"},
                {"role": "tool", "content": "42 widgets shipped"},
            ],
        },
    )
    events = _parse_sse(response.text)
    content_deltas = [text for event in events if (text := _delta_key(event, "content"))]
    assert "42 widgets shipped" in "".join(content_deltas)


def test_offering_tools_with_no_result_yet_calls_the_first_tool():
    response = _client().post(
        "/api/mock-llm/chat/completions",
        json={
            "model": "mock-fast-7b",
            "messages": [{"role": "user", "content": "please search for widgets"}],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "web_search",
                        "parameters": {
                            "type": "object",
                            "properties": {"query": {"type": "string"}},
                            "required": ["query"],
                        },
                    },
                }
            ],
        },
    )
    events = _parse_sse(response.text)

    tool_call_chunks = [chunk for event in events if (chunk := _delta_key(event, "tool_calls"))]
    assert tool_call_chunks[0][0]["function"]["name"] == "web_search"

    # Arguments arrive in fragments across the remaining tool_call chunks;
    # stitched together they must be valid JSON carrying the prompt's words.
    argument_fragments = [
        fragment["function"]["arguments"]
        for chunk in tool_call_chunks[1:]
        for fragment in chunk
        if "arguments" in fragment.get("function", {})
    ]
    arguments = json.loads("".join(argument_fragments))
    assert "widgets" in arguments["query"]

    finish_reasons = [reason for event in events if (reason := _finish_reason(event))]
    assert finish_reasons[-1] == "tool_calls"
    # No text content when the model only calls a tool.
    assert not [event for event in events if _delta_key(event, "content")]


def test_a_tool_result_already_present_stops_the_calling():
    """Once a tool result is on the transcript, the mock answers in text —
    the loop only keeps calling with TRIGGER_TOOL_LOOP."""
    response = _client().post(
        "/api/mock-llm/chat/completions",
        json={
            "model": "mock-fast-7b",
            "messages": [
                {"role": "user", "content": "go"},
                {"role": "tool", "content": "done"},
            ],
            "tools": [{"type": "function", "function": {"name": "any_tool", "parameters": {}}}],
        },
    )
    events = _parse_sse(response.text)
    assert not [event for event in events if _delta_key(event, "tool_calls")]


def test_trigger_tool_loop_keeps_calling_even_with_a_tool_result():
    response = _client().post(
        "/api/mock-llm/chat/completions",
        json={
            "model": "mock-fast-7b",
            "messages": [
                {"role": "user", "content": "TRIGGER_TOOL_LOOP"},
                {"role": "tool", "content": "already ran once"},
            ],
            "tools": [{"type": "function", "function": {"name": "any_tool", "parameters": {}}}],
        },
    )
    events = _parse_sse(response.text)
    assert [event for event in events if _delta_key(event, "tool_calls")]


# ---------------------------------------------------------------------------
# Mock MCP
# ---------------------------------------------------------------------------


def _rpc(method: str, params: dict | None = None, id_: int = 1, **query: str) -> dict:
    client = _client()
    url = "/api/mock-mcp"
    if query:
        url += "?" + "&".join(f"{key}={value}" for key, value in query.items())
    body: dict = {"jsonrpc": "2.0", "id": id_, "method": method}
    if params is not None:
        body["params"] = params
    response = client.post(url, json=body)
    return {"status": response.status_code, "body": response.json() if response.content else None}


def test_initialize_reports_a_handshake_compatible_version():
    result = _rpc("initialize")
    assert result["status"] == 200
    assert result["body"]["result"]["protocolVersion"] == "2025-06-18"
    assert result["body"]["result"]["capabilities"] == {"tools": {}}


def test_notifications_initialized_is_202_with_no_body():
    response = _client().post(
        "/api/mock-mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}
    )
    assert response.status_code == 202
    assert response.content == b""


def test_ping_answers_an_empty_result():
    assert _rpc("ping")["body"]["result"] == {}


def test_tools_list_offers_both_tools_by_default():
    names = {tool["name"] for tool in _rpc("tools/list")["body"]["result"]["tools"]}
    assert names == {"echo_upper", "add_numbers"}


def test_hide_drops_a_tool_from_the_listing():
    result = _rpc("tools/list", hide="echo_upper")
    names = {tool["name"] for tool in result["body"]["result"]["tools"]}
    assert names == {"add_numbers"}


def test_echo_upper_uppercases():
    result = _rpc("tools/call", {"name": "echo_upper", "arguments": {"text": "hi there"}})
    content = result["body"]["result"]["content"][0]["text"]
    assert content == "HI THERE"
    assert result["body"]["result"]["isError"] is False


def test_add_numbers_sums():
    result = _rpc("tools/call", {"name": "add_numbers", "arguments": {"a": 2, "b": 3}})
    payload = json.loads(result["body"]["result"]["content"][0]["text"])
    assert payload == {"sum": 5}


def test_add_numbers_rejects_non_numeric_arguments():
    result = _rpc("tools/call", {"name": "add_numbers", "arguments": {"a": "x", "b": 3}})
    assert result["body"]["result"]["isError"] is True


def test_unknown_tool_is_an_error_result_not_a_protocol_error():
    result = _rpc("tools/call", {"name": "no_such_tool", "arguments": {}})
    assert result["body"]["result"]["isError"] is True
    assert "no_such_tool" in result["body"]["result"]["content"][0]["text"]


def test_fail_query_param_fails_every_call():
    result = _rpc(
        "tools/call", {"name": "echo_upper", "arguments": {"text": "hi"}}, fail="1"
    )
    assert result["body"]["result"]["isError"] is True
    assert "?fail=1" in result["body"]["result"]["content"][0]["text"]


def test_tools_call_without_a_name_is_a_protocol_error():
    result = _rpc("tools/call", {"arguments": {}})
    assert result["body"]["error"]["code"] == -32602


def test_unknown_method_is_a_protocol_error():
    result = _rpc("not_a_real_method")
    assert result["body"]["error"]["code"] == -32601


def test_malformed_json_body_is_a_parse_error():
    response = _client().post(
        "/api/mock-mcp", content=b"not json", headers={"Content-Type": "application/json"}
    )
    assert response.json()["error"]["code"] == -32700
