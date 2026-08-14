"""Mock LLM + mock MCP endpoints — dev-only test doubles for exercising the
run executor and the MCP tool loop without real hardware.

Port of `git show master:src/app/api/mock-llm/chat/completions/route.ts`,
`git show master:src/app/api/mock-llm/models/route.ts` and
`git show master:src/app/api/mock-mcp/route.ts`. Nothing in this module is
imported by production code — it exists purely so `app.services.llm` and
`app.services.tool_loop` can be driven end-to-end from a browser or a script,
the same role the old app's mocks played (see its CLAUDE.md "Testing"
section: "verified against the dev server + the mocks", outside the pytest
suites).

Every route is gated by :func:`mocks_enabled`, mirroring
`git show master:src/lib/dev-only.ts`: on in development, off in production
unless `ENABLE_MOCKS=true` — a 404, not a 403, because in production these
routes should not appear to exist at all. The gate is checked per request
(not at router-registration time) so a test can flip it with a plain
monkeypatch of `get_settings`.
"""

from __future__ import annotations

import asyncio
import json
import math
import random
import re
import time
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Request
from starlette.responses import JSONResponse, Response, StreamingResponse

from app.config import Settings, get_settings

router = APIRouter(tags=["mocks"])


def mocks_enabled(settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    return settings.environment != "production" or settings.enable_mocks


def _disabled() -> Response:
    return Response("Not found", status_code=404)


# ---------------------------------------------------------------------------
# Mock LLM — a minimal OpenAI-compatible endpoint
# ---------------------------------------------------------------------------

_CHUNK_COUNT = 10
_MIN_CHUNK_DELAY_MS = 100
_MAX_CHUNK_DELAY_MS = 200
_ERROR_DELAY_MS = 300
_SLOW_PREFILL_MS = 2000
#: How many pieces a tool call's arguments are split into.
_TOOL_ARG_CHUNKS = 3

#: Magic strings a prompt can contain to steer the mock.
TRIGGER_ERROR = "TRIGGER_ERROR"
TRIGGER_SLOW = "TRIGGER_SLOW"
#: Never stop calling tools — used to verify the loop's turn budget.
TRIGGER_TOOL_LOOP = "TRIGGER_TOOL_LOOP"

_QUERY_LIKE_RE = re.compile(r"query|q|search|text|prompt|question", re.IGNORECASE)


@dataclass(frozen=True)
class _OfferedTool:
    name: str
    parameters: dict[str, Any]


def _messages_of(payload: Any) -> list[Any]:
    messages = payload.get("messages") if isinstance(payload, dict) else None
    return messages if isinstance(messages, list) else []


def _last_user_message(payload: Any) -> str:
    for message in reversed(_messages_of(payload)):
        if (
            isinstance(message, dict)
            and message.get("role") == "user"
            and isinstance(message.get("content"), str)
        ):
            return message["content"]
    return ""


def _last_tool_result(payload: Any) -> str | None:
    """Text of the most recent tool result, or None when none has come back yet."""
    for message in reversed(_messages_of(payload)):
        if (
            isinstance(message, dict)
            and message.get("role") == "tool"
            and isinstance(message.get("content"), str)
        ):
            return message["content"]
    return None


def _read_tools(payload: Any) -> list[_OfferedTool]:
    raw = payload.get("tools") if isinstance(payload, dict) else None
    if not isinstance(raw, list):
        return []

    tools: list[_OfferedTool] = []
    for entry in raw:
        function = entry.get("function") if isinstance(entry, dict) else None
        if not isinstance(function, dict):
            continue
        name = function.get("name")
        if not isinstance(name, str) or not name:
            continue
        parameters = function.get("parameters")
        tools.append(
            _OfferedTool(name=name, parameters=parameters if isinstance(parameters, dict) else {})
        )
    return tools


def _sample_value(schema: Any, key: str, user_message: str) -> Any:
    """Invents a plausible value for one JSON-Schema property.

    The point is not to be clever but to produce arguments that parse and
    that a canned tool response can be checked against.
    """
    schema = schema if isinstance(schema, dict) else {}
    enum_values = schema.get("enum")
    if isinstance(enum_values, list) and enum_values:
        return enum_values[0]

    kind = schema.get("type")
    if kind in ("number", "integer"):
        return 42
    if kind == "boolean":
        return True
    if kind == "array":
        return []
    if kind == "object":
        return {}

    # A query-ish string is far more useful carrying the prompt's own words.
    if _QUERY_LIKE_RE.search(key):
        words = user_message.strip().split()[:8]
        return " ".join(words) if words else "mock query"
    return f"mock {key}"


def _synthesize_arguments(parameters: Mapping[str, Any], user_message: str) -> str:
    properties = parameters.get("properties")
    if not isinstance(properties, dict):
        return "{}"

    required = parameters.get("required")
    required_keys = (
        [key for key in required if isinstance(key, str)] if isinstance(required, list) else []
    )

    entries = list(properties.items())
    # Fill the required properties, or everything when nothing is required.
    chosen = (
        [entry for entry in entries if entry[0] in required_keys] if required_keys else entries
    )

    args = {key: _sample_value(schema, key, user_message) for key, schema in chosen}
    return json.dumps(args)


def _split_evenly(text: str, parts: int) -> list[str]:
    if not text:
        return [""]
    size = math.ceil(len(text) / parts)
    return [text[i : i + size] for i in range(0, len(text), size)]


def _build_chunks(user_message: str, tool_result: str | None) -> list[str]:
    """Deterministic-ish echo: a short acknowledgement plus the prompt's own words."""
    words = [word for word in user_message.strip().split() if word][:24]
    echo = " ".join(words) if words else "(empty prompt)"

    # Quoting the tool output proves the result actually made it back into the
    # conversation, which is the whole point of the execute-mode loop.
    if tool_result is None:
        source = (
            f'Mock response. You said: "{echo}". This text is generated locally by the mock '
            "endpoint so run metrics have something to measure."
        )
    else:
        source = (
            f'Mock response. The tool returned: {tool_result.strip()[:300]} — answering '
            f'"{echo}" on that basis.'
        )
    return _split_evenly(source, _CHUNK_COUNT)


def _sse(payload: Any) -> bytes:
    return f"data: {json.dumps(payload)}\n\n".encode()


@router.get("/mock-llm/models")
async def mock_llm_models() -> Response:
    """Model list for the built-in mock endpoint.

    Point an endpoint at `http://localhost:8077/api/mock-llm` to exercise runs
    without real hardware.
    """
    if not mocks_enabled():
        return _disabled()
    created = int(time.time())
    return JSONResponse(
        {
            "object": "list",
            "data": [
                {"id": "mock-fast-7b", "object": "model", "created": created, "owned_by": "mock"},
                {"id": "mock-slow-70b", "object": "model", "created": created, "owned_by": "mock"},
            ],
        }
    )


@router.post("/mock-llm/chat/completions")
async def mock_llm_chat_completions(request: Request) -> Response:
    """Minimal OpenAI-compatible streaming chat-completions endpoint used for
    end-to-end testing of the run executor.
    """
    if not mocks_enabled():
        return _disabled()

    try:
        payload = await request.json()
    except ValueError:
        return JSONResponse({"error": {"message": "Invalid JSON body."}}, status_code=400)

    user_message = _last_user_message(payload)
    model = payload.get("model") if isinstance(payload, dict) else None
    model = model if isinstance(model, str) else "mock-fast-7b"

    if TRIGGER_ERROR in user_message:
        await asyncio.sleep(_ERROR_DELAY_MS / 1000)
        return JSONResponse(
            {
                "error": {
                    "message": "Mock failure requested via TRIGGER_ERROR.",
                    "type": "mock_error",
                }
            },
            status_code=500,
        )

    slow = TRIGGER_SLOW in user_message
    tool_result = _last_tool_result(payload)
    offered_tools = _read_tools(payload)

    # Call a tool on the first turn, then answer using what came back. With
    # TRIGGER_TOOL_LOOP the mock never settles, so the loop's turn budget —
    # and the `max_turns` stop reason — can be exercised.
    call_tool = bool(offered_tools) and (TRIGGER_TOOL_LOOP in user_message or tool_result is None)

    chunks = [] if call_tool else _build_chunks(user_message, tool_result)
    tool_call_name = offered_tools[0].name if call_tool else None
    tool_call_args = (
        _synthesize_arguments(offered_tools[0].parameters, user_message) if call_tool else ""
    )

    completion_id = f"chatcmpl-mock-{int(time.time() * 1000)}"
    created = int(time.time())

    async def generate() -> AsyncIterator[bytes]:
        async def pause() -> None:
            delay_ms = _MIN_CHUNK_DELAY_MS + random.randint(
                0, _MAX_CHUNK_DELAY_MS - _MIN_CHUNK_DELAY_MS
            )
            await asyncio.sleep(delay_ms / 1000)

        def chunk_frame(**choice: Any) -> bytes:
            return _sse(
                {
                    "id": completion_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model,
                    "choices": [{"index": 0, **choice}],
                }
            )

        yield chunk_frame(delta={"role": "assistant"}, finish_reason=None)

        if slow:
            await asyncio.sleep(_SLOW_PREFILL_MS / 1000)

        if tool_call_name is not None:
            # The opening fragment carries the id and name; the arguments
            # follow in pieces, which is how vLLM streams them and what the
            # client's accumulator has to cope with.
            await pause()
            yield chunk_frame(
                delta={
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": f"call_mock_{created}",
                            "type": "function",
                            "function": {"name": tool_call_name, "arguments": ""},
                        }
                    ]
                },
                finish_reason=None,
            )

            for fragment in _split_evenly(tool_call_args, _TOOL_ARG_CHUNKS):
                await pause()
                yield chunk_frame(
                    delta={"tool_calls": [{"index": 0, "function": {"arguments": fragment}}]},
                    finish_reason=None,
                )

        for chunk in chunks:
            await pause()
            yield chunk_frame(delta={"content": chunk}, finish_reason=None)

        yield chunk_frame(
            delta={}, finish_reason="tool_calls" if tool_call_name is not None else "stop"
        )

        completion_tokens = sum(max(1, round(len(chunk) / 4)) for chunk in chunks)
        if tool_call_name is not None:
            completion_tokens += max(1, round((len(tool_call_name) + len(tool_call_args)) / 4))
        prompt_tokens = max(1, round(len(user_message) / 4))
        yield _sse(
            {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [],
                "usage": {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": prompt_tokens + completion_tokens,
                },
            }
        )

        yield b"data: [DONE]\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream; charset=utf-8",
        headers={
            "Cache-Control": "no-store, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# Mock MCP — a tiny JSON-RPC server over streamable HTTP
# ---------------------------------------------------------------------------

#: Within `HANDSHAKE_PROTOCOL_VERSIONS` of the `mcp` client SDK this backend
#: uses, so a real `Client` (see `app.services.mcp_client`) negotiates
#: successfully against it.
_PROTOCOL_VERSION = "2025-06-18"

_TOOLS: list[dict[str, Any]] = [
    {
        "name": "echo_upper",
        "description": "Uppercases the given text.",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string", "description": "Text to uppercase"}},
            "required": ["text"],
        },
    },
    {
        "name": "add_numbers",
        "description": "Adds two numbers and returns the sum.",
        "inputSchema": {
            "type": "object",
            "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
            "required": ["a", "b"],
        },
    },
]


def _rpc_ok(id_: Any, result: Any) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": id_, "result": result})


def _rpc_fail(id_: Any, code: int, message: str) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}})


def _text_result(text: str, is_error: bool = False) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _call_tool(name: str, arguments: Mapping[str, Any], force_fail: bool) -> dict[str, Any]:
    if force_fail:
        return _text_result(f'Mock MCP failure for "{name}" (?fail=1).', True)

    if name == "echo_upper":
        text = arguments.get("text")
        return _text_result((text if isinstance(text, str) else "").upper())

    if name == "add_numbers":
        a, b = arguments.get("a"), arguments.get("b")
        if not _is_number(a) or not _is_number(b):
            return _text_result("add_numbers needs two numeric arguments, a and b.", True)
        return _text_result(json.dumps({"sum": a + b}))

    return _text_result(f'Unknown tool "{name}".', True)


@router.post("/mock-mcp")
async def mock_mcp(request: Request) -> Response:
    """A tiny MCP server over streamable HTTP, for end-to-end testing of the
    MCP path without running a real Odoo or websearch server.

    Implements only what a client actually exercises: `initialize`,
    `notifications/initialized`, `ping`, `tools/list` and `tools/call`,
    answering with plain JSON rather than an SSE stream (which the spec
    allows for a single response). Register a toolset with URL
    `http://localhost:8077/api/mock-mcp`, hit Discover, and the tools below
    show up.

    Query parameters steer it: `?hide=echo_upper` drops a tool from
    `tools/list` so the discovery retire path can be verified, and `?fail=1`
    makes every call answer with `isError`.
    """
    if not mocks_enabled():
        return _disabled()

    hidden = set(request.query_params.getlist("hide"))
    force_fail = request.query_params.get("fail") == "1"

    try:
        payload = await request.json()
    except ValueError:
        return _rpc_fail(None, -32700, "Parse error.")

    id_ = payload.get("id") if isinstance(payload, dict) else None
    method = payload.get("method") if isinstance(payload, dict) else None

    if method == "initialize":
        return _rpc_ok(
            id_,
            {
                "protocolVersion": _PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "mock-mcp", "version": "0.1.0"},
            },
        )

    # Notifications carry no id and expect no result, only an acknowledgement.
    if method == "notifications/initialized":
        return Response(status_code=202)

    if method == "ping":
        return _rpc_ok(id_, {})

    if method == "tools/list":
        return _rpc_ok(id_, {"tools": [tool for tool in _TOOLS if tool["name"] not in hidden]})

    if method == "tools/call":
        params = payload.get("params") if isinstance(payload, dict) else None
        params = params if isinstance(params, dict) else {}
        name = params.get("name")
        if not isinstance(name, str):
            return _rpc_fail(id_, -32602, "tools/call requires a tool name.")
        arguments = params.get("arguments")
        arguments = arguments if isinstance(arguments, dict) else {}
        return _rpc_ok(id_, _call_tool(name, arguments, force_fail))

    return _rpc_fail(id_, -32601, f'Method "{method}" is not implemented by the mock.')
