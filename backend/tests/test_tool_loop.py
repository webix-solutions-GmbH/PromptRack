"""`app.services.tool_loop` — metric aggregation and the agentic loop itself.

The `aggregate` tests each pin one of the reasons the aggregation is not a
plain sum (ttft comes from the first turn only, the throughput denominator is
the sum of the per-turn generation windows, a turn with no ttft is pure
generation).

The loop tests run against a scripted `ChatStreamer` instead: no socket, no
database, and the turn budget, the transcript assembly and the tool dispatch
are exactly what a mock endpoint makes hardest to assert on.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence
from typing import Any

import pytest

from app.services.llm import ChatMessage, LlmResult, ToolCall, compute_tokens_per_sec
from app.services.tool_loop import (
    Aggregates,
    SnapshotTool,
    ToolExecutionOutcome,
    TranscriptMessage,
    TurnMetrics,
    aggregate,
    parse_tool_arguments,
    parse_tools_snapshot,
    parse_transcript,
    parse_turns,
    run_tool_loop,
    serialize_tools_snapshot,
    serialize_transcript,
    serialize_turns,
    snapshot_definitions,
)

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def turn(**overrides: Any) -> TurnMetrics:
    defaults: dict[str, Any] = {
        "index": 0,
        "ttft_ms": 100,
        "duration_ms": 1000,
        "prompt_tokens": 20,
        "completion_tokens": 45,
        "tokens_estimated": False,
        "finish_reason": "stop",
        "tool_call_count": 0,
    }
    return TurnMetrics(**{**defaults, **overrides})


def tool(
    name: str = "lookup",
    *,
    source: str = "manual",
    mock_response: str | None = '{"ok":true}',
    toolset_id: int = 1,
) -> SnapshotTool:
    return SnapshotTool(
        definition={
            "type": "function",
            "function": {
                "name": name,
                "description": f"The {name} tool.",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        source=source,  # type: ignore[arg-type]
        toolset_id=toolset_id,
        toolset_name="Support Desk (mock)",
        mock_response=mock_response,
    )


def call(name: str = "lookup", arguments: str = "{}", call_id: str = "call_0") -> ToolCall:
    return ToolCall(id=call_id, name=name, arguments=arguments)


def llm_result(
    text: str = "",
    tool_calls: Sequence[ToolCall] = (),
    **overrides: Any,
) -> LlmResult:
    defaults: dict[str, Any] = {
        "ttft_ms": 100,
        "duration_ms": 1000,
        "prompt_tokens": 20,
        "completion_tokens": 45,
        "tokens_estimated": False,
        "finish_reason": "tool_calls" if tool_calls else "stop",
    }
    return LlmResult(text=text, tool_calls=list(tool_calls), **{**defaults, **overrides})


class ScriptedStreamer:
    """A `ChatStreamer` that hands out canned results, one per turn.

    Records the `messages`/`tools` of every call so the tests can assert on
    what the model was actually shown — which is the whole point of the loop.
    """

    def __init__(self, *results: LlmResult) -> None:
        self._results = list(results)
        self.calls: list[dict[str, Any]] = []

    async def __call__(
        self,
        base_url: str,
        api_key: str | None,
        model: str,
        messages: Sequence[ChatMessage],
        *,
        tools: Sequence[Mapping[str, Any]] | None = None,
        tool_choice: str | None = None,
        params: Mapping[str, Any] | None = None,
        on_delta: Any = None,
    ) -> LlmResult:
        # Copy: the loop keeps appending to the same list.
        self.calls.append(
            {
                "base_url": base_url,
                "api_key": api_key,
                "model": model,
                "messages": [dict(message) for message in messages],
                "tools": None if tools is None else list(tools),
                "tool_choice": tool_choice,
                "params": params,
            }
        )
        result = self._results[min(len(self.calls) - 1, len(self._results) - 1)]
        if on_delta is not None and result.text:
            on_delta(result.text, result.text)
        return result

    @property
    def turns_taken(self) -> int:
        return len(self.calls)


async def run(streamer: ScriptedStreamer, **overrides: Any):
    defaults: dict[str, Any] = {
        "base_url": "http://box:8000/v1",
        "model": "qwen3-32b",
        "user_message": "Reconcile invoice 4711.",
        "stream": streamer,
    }
    return await run_tool_loop(**{**defaults, **overrides})


# ---------------------------------------------------------------------------
# aggregate
# ---------------------------------------------------------------------------


class TestAggregate:
    def test_reduces_to_the_single_turn_numbers_a_plain_prompt_always_produced(self):
        single = turn(ttft_ms=500, duration_ms=2000, completion_tokens=100)

        result = aggregate([single])

        assert result.ttft_ms == 500
        assert result.duration_ms == 2000
        assert result.completion_tokens == 100
        assert result.tokens_per_sec == pytest.approx(compute_tokens_per_sec(100, 2000, 500))

    def test_takes_ttft_from_the_first_turn_only(self):
        result = aggregate([turn(index=0, ttft_ms=120), turn(index=1, ttft_ms=900)])

        assert result.ttft_ms == 120

    def test_sums_durations_and_completion_tokens_across_turns(self):
        result = aggregate(
            [
                turn(index=0, duration_ms=1000, completion_tokens=10),
                turn(index=1, duration_ms=1500, completion_tokens=25),
            ]
        )

        assert result.duration_ms == 2500
        assert result.completion_tokens == 35

    def test_divides_by_the_sum_of_the_per_turn_generation_windows(self):
        # Turn 1: 1000 - 200 = 800ms. Turn 2: 1200 - 400 = 800ms. Total 1600ms.
        result = aggregate(
            [
                turn(index=0, ttft_ms=200, duration_ms=1000, completion_tokens=40),
                turn(index=1, ttft_ms=400, duration_ms=1200, completion_tokens=40),
            ]
        )

        assert result.tokens_per_sec == pytest.approx(80 / 1.6)
        # Naively using (total duration - first ttft) would overstate the window
        # and understate the rate, because later prefills would be counted as
        # generation.
        assert result.tokens_per_sec != pytest.approx(compute_tokens_per_sec(80, 2200, 200))

    def test_sums_prompt_tokens_keeping_null_only_when_no_turn_reported_any(self):
        assert aggregate([turn(prompt_tokens=20), turn(prompt_tokens=35)]).prompt_tokens == 55
        assert aggregate([turn(prompt_tokens=None), turn(prompt_tokens=35)]).prompt_tokens == 35
        assert aggregate([turn(prompt_tokens=None), turn(prompt_tokens=None)]).prompt_tokens is None

    def test_flags_the_whole_run_as_estimated_when_any_single_turn_was(self):
        assert aggregate([turn(), turn(tokens_estimated=True)]).tokens_estimated is True
        assert aggregate([turn(), turn()]).tokens_estimated is False

    def test_treats_a_turn_with_no_ttft_as_pure_generation(self):
        result = aggregate([turn(ttft_ms=None, duration_ms=500, completion_tokens=50)])

        assert result.tokens_per_sec == pytest.approx(100)

    def test_never_produces_a_negative_generation_window(self):
        # A clock skew or a usage-only trailing chunk can put ttft past duration.
        result = aggregate(
            [
                turn(ttft_ms=900, duration_ms=400, completion_tokens=10),
                turn(ttft_ms=100, duration_ms=1100, completion_tokens=10),
            ]
        )

        assert result.tokens_per_sec == pytest.approx(20)

    def test_returns_empty_metrics_when_no_turn_ran_at_all(self):
        assert aggregate([]) == Aggregates(
            ttft_ms=None,
            duration_ms=0,
            prompt_tokens=None,
            completion_tokens=0,
            tokens_estimated=True,
            tokens_per_sec=None,
        )


# ---------------------------------------------------------------------------
# The plain, tool-free path
# ---------------------------------------------------------------------------


class TestPlainCompletion:
    async def test_sends_no_tools_and_takes_exactly_one_turn(self):
        streamer = ScriptedStreamer(llm_result(text="42"))

        result = await run(streamer)

        assert streamer.turns_taken == 1
        assert streamer.calls[0]["tools"] is None
        assert streamer.calls[0]["tool_choice"] is None
        assert result.text == "42"
        assert result.stopped_reason == "stop"
        assert result.turn_count == 1
        assert result.tool_call_count == 0

    async def test_ignores_a_snapshot_when_the_mode_is_none(self):
        streamer = ScriptedStreamer(llm_result(text="42"))

        await run(streamer, snapshot=[tool()], tool_mode="none")

        assert streamer.calls[0]["tools"] is None

    async def test_offers_nothing_when_the_snapshot_is_empty_whatever_the_mode(self):
        # A test case whose toolset lost every tool must not send `tools: []`;
        # some servers reject it outright.
        streamer = ScriptedStreamer(llm_result(text="42"))

        result = await run(streamer, snapshot=[], tool_mode="execute", max_turns=5)

        assert streamer.calls[0]["tools"] is None
        assert streamer.turns_taken == 1
        assert result.stopped_reason == "stop"

    async def test_starts_the_transcript_with_the_system_prompt_when_there_is_one(self):
        streamer = ScriptedStreamer(llm_result(text="42"))

        result = await run(streamer, system_prompt="You are terse.")

        assert [message.role for message in result.transcript] == ["system", "user", "assistant"]
        assert streamer.calls[0]["messages"][0] == {
            "role": "system",
            "content": "You are terse.",
        }

    async def test_omits_a_blank_system_prompt_rather_than_sending_an_empty_message(self):
        streamer = ScriptedStreamer(llm_result(text="42"))

        result = await run(streamer, system_prompt="   ")

        assert [message.role for message in result.transcript] == ["user", "assistant"]
        assert streamer.calls[0]["messages"][0]["role"] == "user"

    async def test_forwards_credentials_and_params_to_the_endpoint(self):
        streamer = ScriptedStreamer(llm_result(text="42"))

        await run(streamer, api_key="sk-secret", params={"temperature": 0.2})

        assert streamer.calls[0]["api_key"] == "sk-secret"
        assert streamer.calls[0]["params"] == {"temperature": 0.2}

    async def test_aggregates_a_single_turn_into_the_result_columns(self):
        streamer = ScriptedStreamer(
            llm_result(text="42", ttft_ms=500, duration_ms=2000, completion_tokens=100)
        )

        result = await run(streamer)

        assert result.ttft_ms == 500
        assert result.duration_ms == 2000
        assert result.completion_tokens == 100
        assert result.tokens_per_sec == pytest.approx(compute_tokens_per_sec(100, 2000, 500))


# ---------------------------------------------------------------------------
# definitions mode
# ---------------------------------------------------------------------------


class TestDefinitionsMode:
    async def test_offers_the_tools_and_stops_at_the_first_call_without_executing(self):
        executed: list[ToolCall] = []

        async def executor(_call: ToolCall) -> ToolExecutionOutcome:
            executed.append(_call)
            return ToolExecutionOutcome("never", False)

        streamer = ScriptedStreamer(llm_result(tool_calls=[call()]), llm_result(text="unreachable"))

        result = await run(
            streamer,
            snapshot=[tool()],
            tool_mode="definitions",
            tool_choice="required",
            max_turns=6,
            execute_tool=executor,
        )

        assert streamer.turns_taken == 1
        assert streamer.calls[0]["tools"] == snapshot_definitions([tool()])
        assert streamer.calls[0]["tool_choice"] == "required"
        assert executed == []
        assert result.stopped_reason == "definitions_only"
        assert result.tool_call_count == 1
        # The call is still recorded — that *is* the measurement.
        assert result.transcript[-1].tool_calls == [call()]

    async def test_stops_normally_when_the_model_asks_for_nothing(self):
        streamer = ScriptedStreamer(llm_result(text="No tool needed."))

        result = await run(streamer, snapshot=[tool()], tool_mode="definitions")

        assert result.stopped_reason == "stop"
        assert result.tool_call_count == 0


# ---------------------------------------------------------------------------
# execute mode
# ---------------------------------------------------------------------------


class TestExecuteMode:
    async def test_feeds_a_manual_tools_canned_response_back_verbatim(self):
        streamer = ScriptedStreamer(
            llm_result(tool_calls=[call(arguments='{"id":4711}')]),
            llm_result(text="Invoice 4711 is paid."),
        )

        result = await run(
            streamer, snapshot=[tool(mock_response="PAID")], tool_mode="execute", max_turns=4
        )

        assert streamer.turns_taken == 2
        assert streamer.calls[1]["messages"][-1] == {
            "role": "tool",
            "content": "PAID",
            "tool_call_id": "call_0",
            "name": "lookup",
        }
        assert result.text == "Invoice 4711 is paid."
        assert result.stopped_reason == "stop"
        assert result.turn_count == 2
        assert result.tool_call_count == 1
        tool_message = result.transcript[-2]
        assert tool_message.role == "tool"
        assert tool_message.tool_is_error is False
        assert tool_message.turn == 0

    async def test_echoes_the_assistants_tool_calls_back_in_the_next_request(self):
        streamer = ScriptedStreamer(
            llm_result(text="Checking.", tool_calls=[call(arguments='{"id":1}')]),
            llm_result(text="Done."),
        )

        await run(streamer, snapshot=[tool()], tool_mode="execute")

        assistant = streamer.calls[1]["messages"][-2]
        assert assistant == {
            "role": "assistant",
            "content": "Checking.",
            "tool_calls": [
                {
                    "id": "call_0",
                    "type": "function",
                    "function": {"name": "lookup", "arguments": '{"id":1}'},
                }
            ],
        }

    async def test_executes_every_call_of_a_turn_in_order(self):
        streamer = ScriptedStreamer(
            llm_result(
                tool_calls=[
                    call(name="lookup", call_id="a"),
                    call(name="convert", call_id="b"),
                ]
            ),
            llm_result(text="Done."),
        )

        result = await run(
            streamer,
            snapshot=[tool("lookup", mock_response="L"), tool("convert", mock_response="C")],
            tool_mode="execute",
        )

        assert [message["content"] for message in streamer.calls[1]["messages"][-2:]] == ["L", "C"]
        assert result.tool_call_count == 2

    async def test_stops_before_executing_calls_it_has_no_turn_left_to_use(self):
        executed: list[ToolCall] = []

        async def executor(_call: ToolCall) -> ToolExecutionOutcome:
            executed.append(_call)
            return ToolExecutionOutcome("{}", False)

        # Never stops asking for tools — the mock endpoint's TRIGGER_TOOL_LOOP.
        streamer = ScriptedStreamer(llm_result(tool_calls=[call()]))

        result = await run(
            streamer,
            snapshot=[tool("lookup", source="mcp", mock_response=None)],
            tool_mode="execute",
            max_turns=3,
            execute_tool=executor,
        )

        assert streamer.turns_taken == 3
        assert result.stopped_reason == "max_turns"
        # Three turns asked for a call; only the first two had a turn left to
        # feed the result back into, so the ERP is never hit for the third.
        assert len(executed) == 2
        assert result.tool_call_count == 3

    async def test_clamps_the_turn_budget_and_defaults_it_when_unset(self):
        streamer = ScriptedStreamer(llm_result(tool_calls=[call()]))

        await run(streamer, snapshot=[tool()], tool_mode="execute", max_turns=None)
        assert streamer.turns_taken == 6  # DEFAULT_MAX_TURNS

        clamped = ScriptedStreamer(llm_result(tool_calls=[call()]))
        await run(clamped, snapshot=[tool()], tool_mode="execute", max_turns=0)
        assert clamped.turns_taken == 1

    async def test_keeps_the_last_text_the_model_produced_when_a_later_turn_only_calls_tools(self):
        # First turn answers *and* calls; the budget then runs out on a
        # text-free turn. The earlier answer must survive.
        streamer = ScriptedStreamer(
            llm_result(text="Partial answer.", tool_calls=[call()]),
            llm_result(text="", tool_calls=[call()]),
        )

        result = await run(streamer, snapshot=[tool()], tool_mode="execute", max_turns=2)

        assert result.stopped_reason == "max_turns"
        assert result.text == "Partial answer."

    async def test_sums_metrics_over_model_turns_only(self):
        streamer = ScriptedStreamer(
            llm_result(tool_calls=[call()], ttft_ms=200, duration_ms=1000, completion_tokens=40),
            llm_result(text="Done.", ttft_ms=400, duration_ms=1200, completion_tokens=40),
        )

        result = await run(streamer, snapshot=[tool()], tool_mode="execute")

        assert result.ttft_ms == 200
        assert result.duration_ms == 2200
        assert result.completion_tokens == 80
        assert result.tokens_per_sec == pytest.approx(80 / 1.6)


# ---------------------------------------------------------------------------
# Tool failures are data
# ---------------------------------------------------------------------------


class TestToolFailures:
    async def _answer(self, streamer: ScriptedStreamer, **overrides: Any) -> str:
        await run(streamer, tool_mode="execute", max_turns=3, **overrides)
        return streamer.calls[1]["messages"][-1]["content"]

    async def test_reports_a_call_to_a_tool_that_was_never_offered(self):
        streamer = ScriptedStreamer(
            llm_result(tool_calls=[call(name="drop_database")]), llm_result(text="Sorry.")
        )

        content = await self._answer(streamer, snapshot=[tool("lookup")])

        assert json.loads(content)["error"] == (
            'The model called "drop_database", which was not one of the tools it was offered.'
        )
        assert streamer.turns_taken == 2  # the loop kept going

    async def test_reports_arguments_that_are_not_valid_json(self):
        streamer = ScriptedStreamer(
            llm_result(tool_calls=[call(arguments='{"id": ')]), llm_result(text="Sorry.")
        )

        content = await self._answer(streamer, snapshot=[tool()])

        assert json.loads(content)["error"].startswith("Arguments are not valid JSON:")

    async def test_reports_arguments_that_are_not_an_object(self):
        streamer = ScriptedStreamer(
            llm_result(tool_calls=[call(arguments="[1, 2]")]), llm_result(text="Sorry.")
        )

        content = await self._answer(streamer, snapshot=[tool()])

        assert json.loads(content)["error"] == "Arguments must be a JSON object."

    async def test_reports_a_manual_tool_with_no_canned_response(self):
        streamer = ScriptedStreamer(llm_result(tool_calls=[call()]), llm_result(text="Sorry."))

        content = await self._answer(streamer, snapshot=[tool(mock_response=None)])

        assert json.loads(content)["error"] == "This tool has no canned response configured."

    async def test_reports_an_mcp_tool_with_no_executor_wired_up(self):
        streamer = ScriptedStreamer(llm_result(tool_calls=[call()]), llm_result(text="Sorry."))

        content = await self._answer(streamer, snapshot=[tool(source="mcp", mock_response=None)])

        assert json.loads(content)["error"] == (
            "No executor is configured for MCP tools in this run."
        )

    async def test_serializes_an_executor_that_raises_instead_of_failing_the_row(self):
        async def executor(_call: ToolCall) -> ToolExecutionOutcome:
            raise RuntimeError("Odoo said no.")

        streamer = ScriptedStreamer(llm_result(tool_calls=[call()]), llm_result(text="Sorry."))

        content = await self._answer(
            streamer,
            snapshot=[tool(source="mcp", mock_response=None)],
            execute_tool=executor,
        )

        assert json.loads(content)["error"] == "Odoo said no."
        assert streamer.turns_taken == 2

    async def test_marks_an_executors_own_error_outcome_without_rewriting_it(self):
        async def executor(_call: ToolCall) -> ToolExecutionOutcome:
            return ToolExecutionOutcome("MCP: unknown record", True)

        streamer = ScriptedStreamer(llm_result(tool_calls=[call()]), llm_result(text="Sorry."))

        result = await run(
            streamer,
            snapshot=[tool(source="mcp", mock_response=None)],
            tool_mode="execute",
            execute_tool=executor,
        )

        tool_message = next(m for m in result.transcript if m.role == "tool")
        assert tool_message.content == "MCP: unknown record"
        assert tool_message.tool_is_error is True

    async def test_lets_cancellation_through_rather_than_feeding_it_to_the_model(self):
        # The client disconnected: the executor has to be able to reset the
        # in-flight row to `pending`, which a swallowed CancelledError prevents.
        async def executor(_call: ToolCall) -> ToolExecutionOutcome:
            raise asyncio.CancelledError

        streamer = ScriptedStreamer(llm_result(tool_calls=[call()]), llm_result(text="Sorry."))

        with pytest.raises(asyncio.CancelledError):
            await run(
                streamer,
                snapshot=[tool(source="mcp", mock_response=None)],
                tool_mode="execute",
                execute_tool=executor,
            )


# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------


class TestCallbacks:
    async def test_reports_every_turn_delta_call_and_result(self):
        events: list[Any] = []
        streamer = ScriptedStreamer(
            llm_result(text="Looking.", tool_calls=[call()]), llm_result(text="Done.")
        )

        await run(
            streamer,
            snapshot=[tool(mock_response="PAID")],
            tool_mode="execute",
            on_turn_start=lambda t: events.append(("turn", t)),
            on_delta=lambda t, text: events.append(("delta", t, text)),
            on_tool_calls=lambda t, calls: events.append(("calls", t, [c.name for c in calls])),
            on_tool_result=lambda t, message: events.append(("result", t, message.content)),
        )

        assert events == [
            ("turn", 0),
            ("delta", 0, "Looking."),
            ("calls", 0, ["lookup"]),
            ("result", 0, "PAID"),
            ("turn", 1),
            ("delta", 1, "Done."),
        ]


# ---------------------------------------------------------------------------
# Column (de)serialization
# ---------------------------------------------------------------------------


class TestSerialization:
    def test_round_trips_a_tools_snapshot(self):
        snapshot = [tool("lookup"), tool("convert", source="mcp", mock_response=None)]

        assert parse_tools_snapshot(serialize_tools_snapshot(snapshot)) == snapshot

    def test_reads_a_missing_or_malformed_snapshot_as_no_tools(self):
        assert parse_tools_snapshot(None) == []
        assert parse_tools_snapshot("") == []
        assert parse_tools_snapshot("not json") == []
        assert parse_tools_snapshot('{"definition":{}}') == []
        assert parse_tools_snapshot('[{"definition":{"function":{}}}]') == []

    def test_reads_an_unknown_tool_source_as_manual(self):
        # Safe direction: a manual tool with no canned response answers with an
        # error the model can see, rather than calling a server nobody named.
        parsed = parse_tools_snapshot(
            json.dumps([{**tool().to_json(), "source": "smtp", "mock_response": None}])
        )

        assert [(entry.source, entry.mock_response) for entry in parsed] == [("manual", None)]

    def test_round_trips_a_transcript_including_the_display_only_annotations(self):
        messages = [
            TranscriptMessage(role="system", content="You are terse."),
            TranscriptMessage(role="user", content="Hi"),
            TranscriptMessage(
                role="assistant", content="", turn=0, tool_calls=[call(arguments='{"id":1}')]
            ),
            TranscriptMessage(
                role="tool",
                content="PAID",
                tool_call_id="call_0",
                name="lookup",
                turn=0,
                tool_duration_ms=12,
                tool_is_error=False,
            ),
        ]

        assert parse_transcript(serialize_transcript(messages)) == messages

    def test_omits_absent_annotations_from_the_stored_transcript(self):
        stored = json.loads(serialize_transcript([TranscriptMessage(role="user", content="Hi")]))

        assert stored == [{"role": "user", "content": "Hi"}]

    def test_reads_a_missing_or_roleless_transcript_as_none(self):
        assert parse_transcript(None) is None
        assert parse_transcript("[]") is None
        assert parse_transcript('[{"content":"orphan"}]') is None
        assert parse_transcript('[{"role":"narrator","content":"x"}]') is None

    def test_round_trips_turn_metrics(self):
        turns = [turn(index=0), turn(index=1, ttft_ms=None, finish_reason=None)]

        assert parse_turns(serialize_turns(turns)) == turns

    def test_skips_turn_entries_without_an_index(self):
        assert parse_turns('[{"duration_ms":10}]') == []
        assert parse_turns(None) == []


class TestParseToolArguments:
    def test_reads_an_empty_string_as_a_no_argument_call(self):
        for raw in ("", "   ", None):
            parsed = parse_tool_arguments(raw)
            assert parsed.ok is True
            assert parsed.value == {}

    def test_reads_an_object(self):
        parsed = parse_tool_arguments('{"id": 4711}')

        assert parsed.ok is True
        assert parsed.value == {"id": 4711}

    def test_refuses_anything_that_is_not_an_object(self):
        for raw in ("[1]", '"text"', "42", "null"):
            parsed = parse_tool_arguments(raw)
            assert parsed.ok is False
            assert parsed.value is None
