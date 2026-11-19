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
from typing import Any, get_args

import pytest

from app.models.toolsets import ToolSource
from app.services.llm import ChatMessage, LlmResult, ToolCall, compute_tokens_per_sec
from app.services.message_assembly import HistoryTurn
from app.services.tool_loop import (
    TOOL_SOURCES,
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
        "reasoning_tokens": None,
        "ttft_content_ms": None,
    }
    return TurnMetrics(**{**defaults, **overrides})


def tool(
    name: str = "lookup",
    *,
    source: str = "manual",
    mock_response: str | None = '{"ok":true}',
    mock_responses: Sequence[str] | None = None,
    toolset_id: int = 1,
) -> SnapshotTool:
    """`mock_response` is the one-response shorthand; `mock_responses` wins."""
    responses = (
        tuple(mock_responses)
        if mock_responses is not None
        else (() if mock_response is None else (mock_response,))
    )
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
        mock_responses=responses,
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

    def test_sums_reasoning_tokens_keeping_null_only_when_no_turn_reported_any(self):
        """Same rule as `prompt_tokens`: silence and a reported zero differ."""
        both = aggregate([turn(reasoning_tokens=200), turn(reasoning_tokens=90)])
        one = aggregate([turn(reasoning_tokens=None), turn(reasoning_tokens=90)])

        assert both.reasoning_tokens == 290
        assert one.reasoning_tokens == 90
        assert aggregate([turn(), turn()]).reasoning_tokens is None
        assert aggregate([turn(reasoning_tokens=0)]).reasoning_tokens == 0

    def test_takes_the_content_ttft_from_the_first_turn_the_way_ttft_does(self):
        result = aggregate(
            [turn(index=0, ttft_content_ms=800), turn(index=1, ttft_content_ms=120)]
        )

        assert result.ttft_content_ms == 800

    def test_a_thinking_turn_contributes_its_whole_generation_window(self):
        """Thinks for 7.2s, answers in 0.12s: the window is the 7.3s the model
        was busy, not the 121ms it was visible.
        """
        result = aggregate([turn(ttft_ms=130, duration_ms=7417, completion_tokens=479)])

        assert result.tokens_per_sec == pytest.approx(479 / 7.287, abs=0.1)
        assert result.tokens_per_sec < 100


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


class TestConversationHistory:
    HISTORY = (
        HistoryTurn("user", "List open invoices."),
        HistoryTurn("assistant", '{"invoices": [4711, 4712]}'),
    )

    async def test_history_lands_between_the_system_and_the_user_message(self):
        streamer = ScriptedStreamer(llm_result(text="4712"))

        await run(
            streamer,
            system_prompt="You are terse.",
            history=self.HISTORY,
            user_message="Same list, only Germany.",
        )

        assert streamer.calls[0]["messages"] == [
            {"role": "system", "content": "You are terse."},
            {"role": "user", "content": "List open invoices."},
            {"role": "assistant", "content": '{"invoices": [4711, 4712]}'},
            {"role": "user", "content": "Same list, only Germany."},
        ]

    async def test_history_follows_directly_when_there_is_no_system_prompt(self):
        streamer = ScriptedStreamer(llm_result(text="4712"))

        await run(streamer, history=self.HISTORY, user_message="Only Germany.")

        assert [message["role"] for message in streamer.calls[0]["messages"]] == [
            "user",
            "assistant",
            "user",
        ]

    async def test_history_is_in_the_transcript_without_a_turn_index(self):
        streamer = ScriptedStreamer(
            llm_result(tool_calls=[call()]), llm_result(text="done")
        )

        result = await run(
            streamer,
            system_prompt="sys",
            history=self.HISTORY,
            snapshot=[tool()],
            tool_mode="execute",
            max_turns=3,
        )

        roles_and_turns = [(message.role, message.turn) for message in result.transcript]
        assert roles_and_turns == [
            ("system", None),
            ("user", None),
            ("assistant", None),
            ("user", None),
            ("assistant", 0),
            ("tool", 0),
            ("assistant", 1),
        ]
        # The authored history is not a model turn: metrics, turn count and the
        # final answer are the measured turns' alone.
        assert result.turn_count == 2
        assert [turn.index for turn in result.turns] == [0, 1]
        assert result.text == "done"
        # And the second turn still re-sends the history ahead of the user message.
        assert streamer.calls[1]["messages"][1:3] == [
            {"role": "user", "content": "List open invoices."},
            {"role": "assistant", "content": '{"invoices": [4711, 4712]}'},
        ]
        # A transcript round trip keeps the history messages turn-less.
        parsed = parse_transcript(serialize_transcript(result.transcript)) or []
        assert [message.turn for message in parsed[:4]] == [None, None, None, None]
        assert "turn" not in json.loads(serialize_transcript(result.transcript))[2]

    async def test_no_history_leaves_the_request_unchanged(self):
        streamer = ScriptedStreamer(llm_result(text="42"))

        await run(streamer, history=())

        assert streamer.calls[0]["messages"] == [
            {"role": "user", "content": "Reconcile invoice 4711."}
        ]


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

    async def test_answers_by_call_order_and_repeats_the_last_response(self):
        # Three turns of the same tool, then a fourth with two calls in one turn:
        # the counter runs per tool name across the whole result, every turn.
        streamer = ScriptedStreamer(
            llm_result(tool_calls=[call(call_id="a")]),
            llm_result(tool_calls=[call(call_id="b")]),
            llm_result(tool_calls=[call(call_id="c"), call(call_id="d")]),
            llm_result(text="Done."),
        )

        result = await run(
            streamer,
            snapshot=[tool(mock_responses=["page 1", "page 2", "page 3"])],
            tool_mode="execute",
            max_turns=6,
        )

        answers = [message.content for message in result.transcript if message.role == "tool"]
        assert answers == ["page 1", "page 2", "page 3", "page 3"]
        assert all(
            message.tool_is_error is False
            for message in result.transcript
            if message.role == "tool"
        )

    async def test_a_malformed_call_does_not_use_up_a_scripted_response(self):
        # The first call is refused for its arguments, so the model never read
        # "page 1" — the next valid call has to get it, not "page 2".
        streamer = ScriptedStreamer(
            llm_result(tool_calls=[call(call_id="a", arguments="{not json")]),
            llm_result(tool_calls=[call(call_id="b", arguments='{"id":1}')]),
            llm_result(text="Done."),
        )

        result = await run(
            streamer,
            snapshot=[tool(mock_responses=["page 1", "page 2"])],
            tool_mode="execute",
            max_turns=6,
        )

        tool_messages = [message for message in result.transcript if message.role == "tool"]
        assert tool_messages[0].tool_is_error is True
        assert tool_messages[1].content == "page 1"
        assert tool_messages[1].tool_is_error is False

    async def test_counts_each_tool_by_its_own_name(self):
        streamer = ScriptedStreamer(
            llm_result(tool_calls=[call("lookup", call_id="a"), call("convert", call_id="b")]),
            llm_result(tool_calls=[call("lookup", call_id="c"), call("convert", call_id="d")]),
            llm_result(text="Done."),
        )

        result = await run(
            streamer,
            snapshot=[
                tool("lookup", mock_responses=["L1", "L2"]),
                tool("convert", mock_responses=["C1"]),
            ],
            tool_mode="execute",
        )

        answers = [message.content for message in result.transcript if message.role == "tool"]
        assert answers == ["L1", "C1", "L2", "C1"]

    async def test_an_empty_response_list_is_still_the_unconfigured_error(self):
        streamer = ScriptedStreamer(llm_result(tool_calls=[call()]), llm_result(text="Done."))

        result = await run(streamer, snapshot=[tool(mock_responses=[])], tool_mode="execute")

        tool_message = next(m for m in result.transcript if m.role == "tool")
        assert tool_message.tool_is_error is True
        assert "no canned response configured" in tool_message.content

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

    async def test_keeps_every_turns_thinking_in_the_transcript_and_the_last_in_the_column(self):
        """Per-turn thinking is where an agent's decision-making lives, so it
        stays on each message; the column holds the final turn's, like `text`.
        """
        streamer = ScriptedStreamer(
            llm_result(tool_calls=[call()], reasoning_text="I should look this up."),
            llm_result(text="PAID.", reasoning_text="The lookup settles it."),
        )

        result = await run(streamer, snapshot=[tool()], tool_mode="execute")

        assert result.reasoning_text == "The lookup settles it."
        assert [
            message.reasoning for message in result.transcript if message.role == "assistant"
        ] == ["I should look this up.", "The lookup settles it."]

    async def test_never_echoes_the_thinking_back_to_the_endpoint(self):
        """A provider that splits reasoning off does not accept it back."""
        streamer = ScriptedStreamer(
            llm_result(tool_calls=[call()], reasoning_text="thinking"),
            llm_result(text="done"),
        )

        await run(streamer, snapshot=[tool()], tool_mode="execute")

        second_request = streamer.calls[1]["messages"]
        assert all("reasoning" not in message for message in second_request)
        assert all("reasoning_content" not in message for message in second_request)

    async def test_a_later_silent_turn_does_not_blank_an_earlier_turns_thinking(self):
        """Turn 0 answers *and* calls a tool; turn 1 says nothing at all."""
        streamer = ScriptedStreamer(
            llm_result(
                text="The answer is 42.",
                reasoning_text="Some thinking.",
                tool_calls=[call()],
            ),
            llm_result(),
        )

        result = await run(streamer, snapshot=[tool()], tool_mode="execute")

        assert streamer.turns_taken == 2
        assert result.text == "The answer is 42."
        assert result.reasoning_text == "Some thinking."

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

    async def test_routes_a_document_tool_to_the_executor_not_to_a_canned_response(self):
        # The bug this pins: a `documents` tool carries no `mock_response`, so
        # reading its source as `manual` answered every document call with "this
        # tool has no canned response configured" instead of reaching the corpus.
        executed: list[str] = []

        async def executor(one: ToolCall) -> ToolExecutionOutcome:
            executed.append(one.name)
            return ToolExecutionOutcome('{"document_count": 0}', False)

        streamer = ScriptedStreamer(
            llm_result(tool_calls=[call(name="list_documents")]), llm_result(text="Nothing there.")
        )

        content = await self._answer(
            streamer,
            snapshot=[tool("list_documents", source="documents", mock_response=None)],
            execute_tool=executor,
        )

        assert executed == ["list_documents"]
        assert json.loads(content) == {"document_count": 0}

    async def test_reports_a_document_tool_with_no_executor_wired_up(self):
        streamer = ScriptedStreamer(llm_result(tool_calls=[call()]), llm_result(text="Sorry."))

        content = await self._answer(
            streamer, snapshot=[tool(source="documents", mock_response=None)]
        )

        assert json.loads(content)["error"] == (
            "No executor is configured for document tools in this run."
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

    def test_a_single_response_entry_is_byte_identical_to_the_old_shape(self):
        # `describe_row_drift` compares these entries across a row, so the same
        # one-response tool frozen before and after call order existed must
        # serialize to the same bytes — or every older run reports "tools" drift.
        legacy = json.dumps(
            [
                {
                    "definition": tool().definition,
                    "source": "manual",
                    "toolset_id": 1,
                    "toolset_name": "Support Desk (mock)",
                    "mock_response": '{"ok":true}',
                }
            ]
        )

        assert serialize_tools_snapshot([tool()]) == legacy
        assert "mock_responses" not in json.loads(serialize_tools_snapshot([tool()]))[0]

    def test_an_unconfigured_entry_still_writes_a_null_mock_response(self):
        stored = json.loads(serialize_tools_snapshot([tool(mock_response=None)]))[0]

        assert stored["mock_response"] is None
        assert "mock_responses" not in stored

    def test_several_responses_add_the_list_beside_the_first(self):
        stored = json.loads(serialize_tools_snapshot([tool(mock_responses=["one", "two"])]))[0]

        assert stored["mock_response"] == "one"
        assert stored["mock_responses"] == ["one", "two"]

    def test_round_trips_several_responses(self):
        snapshot = [tool(mock_responses=["one", "two", "three"])]

        assert parse_tools_snapshot(serialize_tools_snapshot(snapshot)) == snapshot

    def test_reads_an_old_snapshot_holding_only_mock_response(self):
        legacy = json.dumps([{**tool().to_json(), "mock_response": "PAID"}])

        assert parse_tools_snapshot(legacy)[0].mock_responses == ("PAID",)

    def test_a_malformed_response_list_falls_back_to_the_single_response(self):
        stored = json.dumps(
            [{**tool().to_json(), "mock_response": "first", "mock_responses": ["first", 2]}]
        )

        assert parse_tools_snapshot(stored)[0].mock_responses == ("first",)

    def test_reads_a_missing_or_malformed_snapshot_as_no_tools(self):
        assert parse_tools_snapshot(None) == []
        assert parse_tools_snapshot("") == []
        assert parse_tools_snapshot("not json") == []
        assert parse_tools_snapshot('{"definition":{}}') == []
        assert parse_tools_snapshot('[{"definition":{"function":{}}}]') == []

    def test_round_trips_a_documents_tool_with_its_corpus_shape(self):
        snapshot = [
            SnapshotTool(
                definition=tool("read_document").definition,
                source="documents",
                toolset_id=7,
                toolset_name="Support Handbook",
                document_count=12,
                corpus_updated_at="2026-08-17T09:30:00+00:00",
            )
        ]

        assert parse_tools_snapshot(serialize_tools_snapshot(snapshot)) == snapshot

    def test_keeps_documents_as_its_own_source_rather_than_manual(self):
        # A `documents` tool has no canned response, so reading it as `manual`
        # would answer every call with "no canned response configured" instead
        # of reaching the corpus.
        parsed = parse_tools_snapshot(
            json.dumps([{**tool().to_json(), "source": "documents", "mock_response": None}])
        )

        assert [entry.source for entry in parsed] == ["documents"]

    def test_omits_the_corpus_keys_from_a_manual_or_mcp_snapshot(self):
        stored = json.loads(serialize_tools_snapshot([tool()]))

        assert "document_count" not in stored[0]
        assert "corpus_updated_at" not in stored[0]

    def test_reads_a_snapshot_frozen_before_the_corpus_keys_existed(self):
        legacy = json.dumps([{**tool().to_json(), "source": "documents"}])

        parsed = parse_tools_snapshot(legacy)

        assert (parsed[0].document_count, parsed[0].corpus_updated_at) == (None, None)

    def test_every_source_the_column_allows_survives_the_round_trip(self):
        # The same bug one level lower, where it can be prevented rather than
        # fixed: `TOOL_SOURCES` is read off the model's own `Literal`, so a
        # fourth source added to the column cannot be reintroduced as a name
        # this reader silently degrades to `manual`.
        assert set(TOOL_SOURCES) == set(get_args(ToolSource))
        for source in TOOL_SOURCES:
            stored = json.dumps([{**tool().to_json(), "source": source}])
            assert [entry.source for entry in parse_tools_snapshot(stored)] == [source], source

    def test_reads_an_unknown_tool_source_as_manual(self):
        # Safe direction: a manual tool with no canned response answers with an
        # error the model can see, rather than calling a server nobody named.
        parsed = parse_tools_snapshot(
            json.dumps([{**tool().to_json(), "source": "smtp", "mock_response": None}])
        )

        assert [(entry.source, entry.mock_responses) for entry in parsed] == [("manual", ())]

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

    def test_round_trips_the_reasoning_metrics_of_a_thinking_turn(self):
        turns = [turn(index=0, reasoning_tokens=470, ttft_content_ms=7296)]

        assert parse_turns(serialize_turns(turns)) == turns

    def test_leaves_a_non_thinking_turns_stored_json_exactly_as_it_was(self):
        """The two new keys are omitted, not null, so existing rows keep shape."""
        stored = json.loads(serialize_turns([turn(index=0)]))

        assert "reasoning_tokens" not in stored[0]
        assert "ttft_content_ms" not in stored[0]

    def test_round_trips_a_turns_chain_of_thought_in_the_transcript(self):
        messages = [
            TranscriptMessage(
                role="assistant",
                content="PAID it is.",
                turn=0,
                reasoning="The lookup said PAID, so no follow-up call is needed.",
            )
        ]

        assert parse_transcript(serialize_transcript(messages)) == messages

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
