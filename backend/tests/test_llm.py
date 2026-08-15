"""`app.services.llm` — SSE parsing, metric math and the HTTP client.

Every fixture here encodes a provider quirk this client exists to absorb
(where usage arrives, how tool-call fragments are keyed, where a network read
may cut a line). No database and no socket — the stream
fixtures are replayed through `consume_chat_completion_stream` with a fake
clock, and `stream_chat` is exercised through `httpx.MockTransport`, the same
seam `tests/test_discovery.py` uses.
"""

from __future__ import annotations

import asyncio
import json
import math
from collections.abc import AsyncIterator, Callable, Iterable, Sequence
from typing import Any

import httpx
import pytest

from app.services.llm import (
    LlmError,
    ToolCall,
    compute_tokens_per_sec,
    consume_chat_completion_stream,
    parse_sse_chunk,
    stream_chat,
)

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def sse(payload: Any) -> str:
    return f"data: {json.dumps(payload)}\n\n"


def content_chunk(content: str, **extra: Any) -> str:
    return sse(
        {
            "id": "chatcmpl-1",
            "object": "chat.completion.chunk",
            "model": "test-model",
            "choices": [{"index": 0, "delta": {"content": content}, "finish_reason": None}],
            **extra,
        }
    )


def tool_call_chunk(entries: Sequence[dict[str, Any]]) -> str:
    """One `delta.tool_calls` chunk, in whatever partial shape a server sends."""
    return sse(
        {
            "id": "chatcmpl-1",
            "object": "chat.completion.chunk",
            "model": "test-model",
            "choices": [
                {"index": 0, "delta": {"tool_calls": list(entries)}, "finish_reason": None}
            ],
        }
    )


DONE = "data: [DONE]\n\n"


def stream_of(
    chunks: Iterable[str], step_ms: int = 100
) -> tuple[AsyncIterator[str], Callable[[], float]]:
    """Replays a fixture, advancing a fake clock by `step_ms` per chunk."""
    clock = {"t": 0}

    async def gen() -> AsyncIterator[str]:
        for chunk in chunks:
            clock["t"] += step_ms
            yield chunk

    return gen(), lambda: clock["t"]


# ---------------------------------------------------------------------------
# parse_sse_chunk
# ---------------------------------------------------------------------------


def test_extracts_complete_data_lines_and_keeps_the_trailing_partial_line():
    result = parse_sse_chunk("", 'data: {"a":1}\n\ndata: {"b":2')

    assert result.events == ['{"a":1}']
    assert result.buffer == 'data: {"b":2'


def test_joins_a_line_that_was_split_across_two_reads():
    first = parse_sse_chunk("", 'data: {"hel')
    assert first.events == []

    second = parse_sse_chunk(first.buffer, 'lo":"world"}\n')
    assert second.events == ['{"hello":"world"}']
    assert second.buffer == ""


def test_ignores_comments_blank_lines_and_non_data_fields():
    result = parse_sse_chunk("", ': keep-alive\n\nevent: ping\nid: 7\ndata: {"x":1}\n')

    assert result.events == ['{"x":1}']


def test_handles_crlf_line_endings():
    result = parse_sse_chunk("", 'data: {"x":1}\r\n\r\n')

    assert result.events == ['{"x":1}']


def test_recognises_the_done_sentinel_as_a_normal_data_payload():
    assert parse_sse_chunk("", DONE).events == ["[DONE]"]


# ---------------------------------------------------------------------------
# consume_chat_completion_stream
# ---------------------------------------------------------------------------


async def test_handles_the_vllm_shape_content_chunks_then_a_choices_less_usage_chunk():
    fixture = [
        sse({"choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]}),
        content_chunk("Hello"),
        content_chunk(" world"),
        sse({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}),
        sse(
            {
                "choices": [],
                "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
            }
        ),
        DONE,
    ]
    chunks, now = stream_of(fixture)

    result = await consume_chat_completion_stream(chunks, started_at=0, now=now)

    assert result.text == "Hello world"
    assert result.prompt_tokens == 11
    assert result.completion_tokens == 7
    assert result.tokens_estimated is False
    # First *content* chunk is the second fixture entry → clock at 200ms.
    assert result.ttft_ms == 200
    assert result.duration_ms == 600


async def test_handles_the_ollama_shape_usage_on_the_last_content_chunk():
    fixture = [
        content_chunk("The "),
        content_chunk("answer"),
        content_chunk(
            ".", usage={"prompt_tokens": 25, "completion_tokens": 3, "total_tokens": 28}
        ),
        DONE,
    ]
    chunks, now = stream_of(fixture)

    result = await consume_chat_completion_stream(chunks, started_at=0, now=now)

    assert result.text == "The answer."
    assert result.prompt_tokens == 25
    assert result.completion_tokens == 3
    assert result.tokens_estimated is False
    assert result.ttft_ms == 100


async def test_reassembles_payloads_split_across_network_reads():
    full = content_chunk("split across reads")
    cut1 = len(full) // 3
    cut2 = len(full) * 2 // 3
    fixture = [
        full[:cut1],
        full[cut1:cut2],
        full[cut2:],
        sse({"choices": [], "usage": {"prompt_tokens": 4, "completion_tokens": 4}}),
        DONE,
    ]
    chunks, now = stream_of(fixture)

    result = await consume_chat_completion_stream(chunks, started_at=0, now=now)

    assert result.text == "split across reads"
    assert result.completion_tokens == 4
    # TTFT only counts once the whole line has arrived (third read → 300ms).
    assert result.ttft_ms == 300


async def test_estimates_completion_tokens_when_no_usage_block_is_sent():
    text = "a" * 41
    chunks, now = stream_of([content_chunk(text), DONE])

    result = await consume_chat_completion_stream(chunks, started_at=0, now=now)

    assert result.tokens_estimated is True
    assert result.completion_tokens == math.ceil(41 / 4)
    assert result.prompt_tokens is None


async def test_stops_at_done_and_ignores_anything_after_it():
    chunks, now = stream_of([content_chunk("kept"), DONE, content_chunk(" dropped")])

    result = await consume_chat_completion_stream(chunks, started_at=0, now=now)

    assert result.text == "kept"


async def test_flushes_a_final_line_that_has_no_trailing_newline():
    chunks, now = stream_of(['data: {"choices":[{"delta":{"content":"tail"}}]}'])

    result = await consume_chat_completion_stream(chunks, started_at=0, now=now)

    assert result.text == "tail"


async def test_forwards_deltas_with_the_running_text_so_far():
    seen: list[tuple[str, str]] = []
    chunks, now = stream_of([content_chunk("a"), content_chunk("b"), DONE])

    await consume_chat_completion_stream(
        chunks,
        started_at=0,
        now=now,
        on_delta=lambda delta, so_far: seen.append((delta, so_far)),
    )

    assert seen == [("a", "a"), ("b", "ab")]


async def test_skips_malformed_json_lines_instead_of_failing_the_whole_response():
    chunks, now = stream_of(["data: {not json}\n\n", content_chunk("still here"), DONE])

    result = await consume_chat_completion_stream(chunks, started_at=0, now=now)

    assert result.text == "still here"


async def test_skips_json_that_is_not_an_object():
    """`data: null` / `data: []` are junk, not a failed response.

    Deviation from the old client, which read `payload.choices` unguarded and
    would have turned this into a `stream` error. Ignoring it is what the
    surrounding "a malformed line should not throw away an otherwise good
    response" rule already said.
    """
    chunks, now = stream_of(["data: null\n\n", "data: [1,2]\n\n", content_chunk("fine"), DONE])

    result = await consume_chat_completion_stream(chunks, started_at=0, now=now)

    assert result.text == "fine"


async def test_throws_when_the_stream_carries_an_error_payload():
    chunks, now = stream_of([sse({"error": {"message": "model not loaded"}}), DONE])

    with pytest.raises(LlmError, match="model not loaded") as excinfo:
        await consume_chat_completion_stream(chunks, started_at=0, now=now)

    assert excinfo.value.kind == "stream"
    assert excinfo.value.is_connection_level is False


async def test_a_bare_string_error_payload_is_reported_verbatim():
    chunks, now = stream_of([sse({"error": "out of memory"}), DONE])

    with pytest.raises(LlmError, match="out of memory"):
        await consume_chat_completion_stream(chunks, started_at=0, now=now)


async def test_an_error_object_without_a_message_still_stops_the_stream():
    chunks, now = stream_of([sse({"error": {"code": 500}}), DONE])

    with pytest.raises(LlmError, match="reported an error mid-stream"):
        await consume_chat_completion_stream(chunks, started_at=0, now=now)


async def test_reports_no_ttft_and_empty_text_when_nothing_was_streamed():
    chunks, now = stream_of([DONE])

    result = await consume_chat_completion_stream(chunks, started_at=0, now=now)

    assert result.text == ""
    assert result.ttft_ms is None
    assert result.completion_tokens == 0
    assert result.tokens_estimated is True
    assert result.tool_calls == []
    assert result.finish_reason is None


async def test_reports_no_tool_calls_for_a_plain_text_response():
    chunks, now = stream_of([content_chunk("just prose"), DONE])

    result = await consume_chat_completion_stream(chunks, started_at=0, now=now)

    assert result.tool_calls == []


# ---------------------------------------------------------------------------
# Tool calls
# ---------------------------------------------------------------------------


async def test_stitches_the_vllm_shape_one_index_keyed_slot_arguments_in_fragments():
    fixture = [
        sse({"choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]}),
        tool_call_chunk(
            [
                {
                    "index": 0,
                    "id": "call_abc",
                    "type": "function",
                    "function": {"name": "search", "arguments": ""},
                }
            ]
        ),
        tool_call_chunk([{"index": 0, "function": {"arguments": '{"q":'}}]),
        tool_call_chunk([{"index": 0, "function": {"arguments": '"laptops"}'}}]),
        sse({"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]}),
        sse({"choices": [], "usage": {"prompt_tokens": 40, "completion_tokens": 12}}),
        DONE,
    ]
    chunks, now = stream_of(fixture)

    result = await consume_chat_completion_stream(chunks, started_at=0, now=now)

    assert result.tool_calls == [
        ToolCall(id="call_abc", name="search", arguments='{"q":"laptops"}')
    ]
    assert result.text == ""
    assert result.finish_reason == "tool_calls"
    assert result.completion_tokens == 12


async def test_measures_ttft_from_the_first_tool_call_fragment_when_no_content_is_streamed():
    fixture = [
        sse({"choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]}),
        tool_call_chunk(
            [{"index": 0, "id": "call_1", "function": {"name": "ping", "arguments": "{}"}}]
        ),
        DONE,
    ]
    chunks, now = stream_of(fixture)

    result = await consume_chat_completion_stream(chunks, started_at=0, now=now)

    # The role-only chunk is not output; the tool-call chunk at 200ms is.
    assert result.ttft_ms == 200


async def test_estimates_tokens_from_the_tool_call_when_there_is_no_text_and_no_usage():
    args = '{"query":"a"}'
    chunks, now = stream_of(
        [
            tool_call_chunk(
                [{"index": 0, "id": "c1", "function": {"name": "search", "arguments": args}}]
            ),
            DONE,
        ]
    )

    result = await consume_chat_completion_stream(chunks, started_at=0, now=now)

    assert result.tokens_estimated is True
    assert result.completion_tokens == math.ceil((len("search") + len(args)) / 4)


async def test_accepts_a_whole_call_delivered_in_a_single_chunk():
    chunks, now = stream_of(
        [
            tool_call_chunk(
                [
                    {
                        "index": 0,
                        "id": "call_one_shot",
                        "type": "function",
                        "function": {"name": "get_time", "arguments": "{}"},
                    }
                ]
            ),
            DONE,
        ]
    )

    result = await consume_chat_completion_stream(chunks, started_at=0, now=now)

    assert result.tool_calls == [ToolCall(id="call_one_shot", name="get_time", arguments="{}")]


async def test_keeps_parallel_calls_apart_and_returns_them_in_index_order():
    chunks, now = stream_of(
        [
            tool_call_chunk(
                [
                    {"index": 0, "id": "a", "function": {"name": "first", "arguments": '{"x"'}},
                    {"index": 1, "id": "b", "function": {"name": "second", "arguments": '{"y"'}},
                ]
            ),
            tool_call_chunk([{"index": 1, "function": {"arguments": ":2}"}}]),
            tool_call_chunk([{"index": 0, "function": {"arguments": ":1}"}}]),
            DONE,
        ]
    )

    result = await consume_chat_completion_stream(chunks, started_at=0, now=now)

    assert result.tool_calls == [
        ToolCall(id="a", name="first", arguments='{"x":1}'),
        ToolCall(id="b", name="second", arguments='{"y":2}'),
    ]


async def test_falls_back_to_the_call_id_when_the_endpoint_omits_index():
    chunks, now = stream_of(
        [
            tool_call_chunk([{"id": "x1", "function": {"name": "alpha", "arguments": '{"a"'}}]),
            tool_call_chunk([{"id": "x2", "function": {"name": "beta", "arguments": '{"b"'}}]),
            tool_call_chunk([{"id": "x1", "function": {"arguments": ":1}"}}]),
            tool_call_chunk([{"id": "x2", "function": {"arguments": ":2}"}}]),
            DONE,
        ]
    )

    result = await consume_chat_completion_stream(chunks, started_at=0, now=now)

    assert result.tool_calls == [
        ToolCall(id="x1", name="alpha", arguments='{"a":1}'),
        ToolCall(id="x2", name="beta", arguments='{"b":2}'),
    ]


async def test_appends_to_the_call_in_flight_when_neither_index_nor_id_is_sent():
    chunks, now = stream_of(
        [
            tool_call_chunk([{"function": {"name": "lonely", "arguments": '{"k"'}}]),
            tool_call_chunk([{"function": {"arguments": ":true}"}}]),
            DONE,
        ]
    )

    result = await consume_chat_completion_stream(chunks, started_at=0, now=now)

    assert result.tool_calls == [ToolCall(id="call_0", name="lonely", arguments='{"k":true}')]


async def test_synthesizes_an_id_when_the_endpoint_never_sends_one():
    chunks, now = stream_of(
        [
            tool_call_chunk([{"index": 3, "function": {"name": "nameless_id", "arguments": "{}"}}]),
            DONE,
        ]
    )

    result = await consume_chat_completion_stream(chunks, started_at=0, now=now)

    assert result.tool_calls[0].id == "call_3"


async def test_stitches_a_tool_call_payload_split_across_network_reads():
    full = tool_call_chunk(
        [
            {
                "index": 0,
                "id": "split",
                "function": {"name": "search", "arguments": '{"q":"mid-json"}'},
            }
        ]
    )
    cut = len(full) // 2
    chunks, now = stream_of([full[:cut], full[cut:], DONE])

    result = await consume_chat_completion_stream(chunks, started_at=0, now=now)

    assert result.tool_calls[0].arguments == '{"q":"mid-json"}'


async def test_keeps_malformed_arguments_verbatim_parsing_them_is_the_callers_job():
    chunks, now = stream_of(
        [
            tool_call_chunk(
                [{"index": 0, "id": "bad", "function": {"name": "oops", "arguments": '{"q": '}}]
            ),
            DONE,
        ]
    )

    result = await consume_chat_completion_stream(chunks, started_at=0, now=now)

    assert result.tool_calls[0].arguments == '{"q": '


async def test_drops_a_slot_that_never_received_a_function_name():
    chunks, now = stream_of(
        [tool_call_chunk([{"index": 0, "id": "no_name", "type": "function"}]), DONE]
    )

    result = await consume_chat_completion_stream(chunks, started_at=0, now=now)

    assert result.tool_calls == []


async def test_reassembles_a_name_that_itself_arrived_in_fragments():
    chunks, now = stream_of(
        [
            tool_call_chunk([{"index": 0, "id": "n", "function": {"name": "sea"}}]),
            tool_call_chunk([{"index": 0, "function": {"name": "rch", "arguments": "{}"}}]),
            DONE,
        ]
    )

    result = await consume_chat_completion_stream(chunks, started_at=0, now=now)

    assert result.tool_calls[0].name == "search"


async def test_captures_content_and_tool_calls_together():
    chunks, now = stream_of(
        [
            content_chunk("Let me look that up. "),
            tool_call_chunk(
                [{"index": 0, "id": "both", "function": {"name": "search", "arguments": "{}"}}]
            ),
            sse({"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]}),
            DONE,
        ]
    )

    result = await consume_chat_completion_stream(chunks, started_at=0, now=now)

    assert result.text == "Let me look that up. "
    assert len(result.tool_calls) == 1
    assert result.ttft_ms == 100


def test_a_tool_call_goes_back_on_the_wire_in_the_openai_shape():
    call = ToolCall(id="call_1", name="search", arguments='{"q":"x"}')

    assert call.to_wire() == {
        "id": "call_1",
        "type": "function",
        "function": {"name": "search", "arguments": '{"q":"x"}'},
    }


# ---------------------------------------------------------------------------
# compute_tokens_per_sec
# ---------------------------------------------------------------------------


def test_divides_completion_tokens_by_the_generation_window():
    # 100 tokens generated in 2000ms - 500ms = 1500ms → 66.67 tok/s
    assert compute_tokens_per_sec(100, 2000, 500) == pytest.approx(66.6667, abs=1e-3)


def test_uses_the_full_duration_when_ttft_is_unknown():
    assert compute_tokens_per_sec(50, 1000, None) == pytest.approx(50)


def test_returns_none_when_the_generation_window_is_zero():
    assert compute_tokens_per_sec(10, 500, 500) is None


def test_returns_none_when_ttft_exceeds_the_duration():
    assert compute_tokens_per_sec(10, 400, 900) is None


def test_returns_none_when_there_are_no_completion_tokens():
    assert compute_tokens_per_sec(0, 1000, 100) is None
    assert compute_tokens_per_sec(None, 1000, 100) is None


def test_returns_none_for_a_missing_or_non_finite_duration():
    assert compute_tokens_per_sec(10, None, 0) is None
    assert compute_tokens_per_sec(10, math.nan, 0) is None


async def test_matches_the_metrics_produced_by_a_consumed_stream():
    chunks, now = stream_of(
        [
            content_chunk("one "),
            content_chunk("two "),
            content_chunk("three"),
            sse({"choices": [], "usage": {"prompt_tokens": 5, "completion_tokens": 30}}),
            DONE,
        ]
    )

    result = await consume_chat_completion_stream(chunks, started_at=0, now=now)

    # ttft 100ms, duration 500ms → 400ms of generation for 30 tokens = 75 tok/s
    assert compute_tokens_per_sec(
        result.completion_tokens, result.duration_ms, result.ttft_ms
    ) == pytest.approx(75)


# ---------------------------------------------------------------------------
# stream_chat — request shape and error mapping
# ---------------------------------------------------------------------------


def _transport(handler) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


def _ok_stream(*chunks: str):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content="".join(chunks).encode())

    return handler


async def test_stream_chat_posts_to_chat_completions_under_the_base_url():
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["accept"] = request.headers.get("accept")
        seen["authorization"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, content=(content_chunk("hi") + DONE).encode())

    result = await stream_chat(
        "http://box:8000/v1/",
        "s3cret",
        "qwen3-32b",
        [{"role": "user", "content": "hello"}],
        transport=_transport(handler),
    )

    assert seen["url"] == "http://box:8000/v1/chat/completions"
    assert seen["accept"] == "text/event-stream"
    assert seen["authorization"] == "Bearer s3cret"
    assert seen["body"] == {
        "model": "qwen3-32b",
        "messages": [{"role": "user", "content": "hello"}],
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    assert result.text == "hi"
    assert result.tokens_estimated is True


async def test_stream_chat_sends_no_authorization_header_without_an_api_key():
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers.get("authorization")
        return httpx.Response(200, content=DONE.encode())

    await stream_chat(
        "http://box/v1", None, "m", [], transport=_transport(handler)
    )

    assert seen["authorization"] is None


async def test_stream_chat_omits_tools_and_tool_choice_when_no_tools_are_offered():
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, content=DONE.encode())

    await stream_chat(
        "http://box/v1",
        None,
        "m",
        [],
        tools=[],
        tool_choice="required",
        transport=_transport(handler),
    )

    assert "tools" not in seen["body"]
    assert "tool_choice" not in seen["body"]


async def test_stream_chat_sends_tools_and_tool_choice_together():
    seen: dict[str, Any] = {}
    tool = {"type": "function", "function": {"name": "search", "parameters": {}}}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, content=DONE.encode())

    await stream_chat(
        "http://box/v1",
        None,
        "m",
        [],
        tools=[tool],
        tool_choice="required",
        transport=_transport(handler),
    )

    assert seen["body"]["tools"] == [tool]
    assert seen["body"]["tool_choice"] == "required"


async def test_stream_chat_merges_params_last_so_a_test_case_can_override():
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, content=DONE.encode())

    await stream_chat(
        "http://box/v1",
        None,
        "m",
        [],
        params={"temperature": 0.2, "stream_options": {"include_usage": False}},
        transport=_transport(handler),
    )

    assert seen["body"]["temperature"] == 0.2
    assert seen["body"]["stream_options"] == {"include_usage": False}


async def test_stream_chat_streams_deltas_and_measures_the_response():
    handler = _ok_stream(
        content_chunk("Hel"),
        content_chunk("lo"),
        sse({"choices": [], "usage": {"prompt_tokens": 3, "completion_tokens": 2}}),
        DONE,
    )
    seen: list[str] = []

    result = await stream_chat(
        "http://box/v1",
        None,
        "m",
        [{"role": "user", "content": "x"}],
        on_delta=lambda delta, _so_far: seen.append(delta),
        transport=_transport(handler),
    )

    assert seen == ["Hel", "lo"]
    assert result.text == "Hello"
    assert result.prompt_tokens == 3
    assert result.completion_tokens == 2
    assert result.tokens_estimated is False
    assert result.ttft_ms is not None
    assert result.duration_ms >= 0


async def test_stream_chat_reports_a_non_2xx_status_with_an_excerpt_of_the_body():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, content=b'{"error":  "no such model"}\n')

    with pytest.raises(LlmError) as excinfo:
        await stream_chat("http://box/v1", None, "m", [], transport=_transport(handler))

    assert excinfo.value.kind == "http"
    assert excinfo.value.status == 400
    assert excinfo.value.is_connection_level is False
    assert '{"error": "no such model"}' in str(excinfo.value)


async def test_stream_chat_hints_at_the_api_key_on_401_and_403():
    for status in (401, 403):

        def handler(request: httpx.Request, status: int = status) -> httpx.Response:
            return httpx.Response(status)

        with pytest.raises(LlmError) as excinfo:
            await stream_chat("http://box/v1", "bad", "m", [], transport=_transport(handler))

        assert "unauthorized" in str(excinfo.value)
        assert "(empty response body)" in str(excinfo.value)


async def test_stream_chat_reports_a_refused_connection_as_connection_level():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    with pytest.raises(LlmError) as excinfo:
        await stream_chat("http://box/v1", None, "m", [], transport=_transport(handler))

    assert excinfo.value.kind == "connection"
    assert excinfo.value.is_connection_level is True


async def test_stream_chat_reports_a_timeout_as_connection_level_too():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(LlmError) as excinfo:
        await stream_chat("http://box/v1", None, "m", [], transport=_transport(handler))

    assert excinfo.value.kind == "timeout"
    assert excinfo.value.is_connection_level is True


async def test_stream_chat_enforces_a_total_wall_clock_budget():
    """`timeout` bounds the whole completion, not each socket operation.

    httpx's own timeouts are per-connect/read/write, so a stream that dribbles
    a token every second could run forever inside them. The old client used one
    `AbortController` for the whole request; this is its port.
    """

    async def handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.5)
        return httpx.Response(200, content=DONE.encode())

    with pytest.raises(LlmError) as excinfo:
        await stream_chat(
            "http://box/v1", None, "m", [], timeout=0.01, transport=_transport(handler)
        )

    assert excinfo.value.kind == "timeout"
    assert excinfo.value.is_connection_level is True


async def test_stream_chat_surfaces_a_mid_stream_error_payload_as_a_stream_failure():
    handler = _ok_stream(sse({"error": {"message": "model not loaded"}}), DONE)

    with pytest.raises(LlmError) as excinfo:
        await stream_chat("http://box/v1", None, "m", [], transport=_transport(handler))

    assert excinfo.value.kind == "stream"
    assert "model not loaded" in str(excinfo.value)
