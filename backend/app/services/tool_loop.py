"""The agentic loop: one `run_results` row, one to N model turns.

A tool-free test case is just the degenerate case — a single turn with no
tool definitions — so the executor runs *everything* through here and keeps
one code path for persistence, cancellation and error handling.

Three things this module is responsible for, and the reasoning behind each:

* **The turn budget.** In `execute` mode the loop stops *before* running calls
  it has no turn left to feed back to the model. Executing them would mean side
  effects on a real ERP for results nobody can ever read.
* **Tool failures are data.** A tool that blows up does not fail the row: its
  error text is serialized back to the model as that tool's output, which is
  exactly what a real agent sees and is itself worth measuring. Only a
  connection-level :class:`~app.services.llm.LlmError` can fail a result.
* **Metric aggregation.** :func:`aggregate` folds per-turn numbers into the
  columns `run_results` already has. `duration_ms` sums the *model* turns only
  — waiting on a slow tool must not read as a slow endpoint — and the
  throughput denominator is the sum of each turn's own generation window, so
  later prefills are never counted as generation. For a single turn it reduces
  exactly to `compute_tokens_per_sec(tokens, duration, ttft)`.

The frozen half of a run's tool configuration lives here too
(:class:`SnapshotTool` and its (de)serializers): the definition sent to the
model and a manual tool's canned response are *content* and travel with the
run, while an MCP toolset's URL and headers are credentials and are read live
at execution time — the same line an endpoint's `base_url`/`api_key` sits on.
Which is why the loop takes an `execute_tool` callable rather than looking a
server up itself: building that executor needs a scoped database read, and is
the executor's job.

A `documents` toolset sits on that line differently, and it is worth being
explicit about: its three tool *definitions* freeze like any others, but the
markdown they read is queried live, so a corpus edited between two runs is not
something a past run can currently prove it saw. Hence `document_count` and
`corpus_updated_at` below — two keys in a dict that was being serialized
anyway, frozen so a later version can report that drift without needing a
migration to discover it. They describe the corpus; they are not the corpus,
and nothing in this build reads them back.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from time import monotonic
from typing import Any, NamedTuple, Protocol, get_args

from app.models.runs import StoppedReason
from app.models.toolsets import ToolChoice, ToolMode, ToolSource
from app.services.llm import (
    ChatMessage,
    DeltaCallback,
    LlmResult,
    ToolCall,
    compute_tokens_per_sec,
    stream_chat,
)
from app.services.tool_config import normalize_max_turns

#: One entry of the OpenAI-compatible `tools` array, as it goes on the wire.
#: A plain dict for the same reason `ChatMessage` is one: it is serialized
#: verbatim into the request body *and* into `run_results.tools_snapshot`, and
#: a model class here would only be a lossy copy of whatever the tool row said.
ToolDefinition = dict[str, Any]

#: `(turn, text_so_far)`. Synchronous, like `llm.DeltaCallback` — the
#: executor's only use is to push an event onto a queue.
TurnDeltaCallback = Callable[[int, str], None]


# ---------------------------------------------------------------------------
# The frozen tool configuration
# ---------------------------------------------------------------------------


#: Every tool source, derived from the column's own `Literal` rather than
#: repeated here — which is the whole point: :func:`parse_tools_snapshot` used to
#: name its sources by hand, so `documents` arriving in the column was silently
#: read as `manual` and every document tool answered "no canned response
#: configured". A source added to the model can no longer be missing from here.
TOOL_SOURCES: tuple[ToolSource, ...] = get_args(ToolSource)


@dataclass(frozen=True)
class SnapshotTool:
    """One frozen tool in a `run_results.tools_snapshot`."""

    definition: ToolDefinition
    source: ToolSource
    toolset_id: int
    toolset_name: str
    #: A manual tool's canned response, returned verbatim. None for MCP and
    #: document tools (and for a manual tool nobody configured, which the loop
    #: reports).
    mock_response: str | None = None
    #: How much corpus a `documents` toolset held when this run was created, and
    #: when it had last changed (ISO-8601, or None for a corpus that has never
    #: had a document in it). None on both for every other source. Frozen for
    #: later drift reporting only — see the module docstring — and a string
    #: rather than a `datetime` so the JSON round trip stays lossless.
    document_count: int | None = None
    corpus_updated_at: str | None = None

    @property
    def name(self) -> str:
        """The function name the model will call this tool by."""
        function = self.definition.get("function")
        if isinstance(function, dict):
            name = function.get("name")
            if isinstance(name, str):
                return name
        return ""

    def to_json(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "definition": self.definition,
            "source": self.source,
            "toolset_id": self.toolset_id,
            "toolset_name": self.toolset_name,
            "mock_response": self.mock_response,
        }
        # Omitted rather than written as nulls, so a manual or MCP tool's frozen
        # entry stays byte-identical to what this column has always held.
        if self.document_count is not None:
            payload["document_count"] = self.document_count
        if self.corpus_updated_at is not None:
            payload["corpus_updated_at"] = self.corpus_updated_at
        return payload


def serialize_tools_snapshot(snapshot: Sequence[SnapshotTool]) -> str:
    """The `tools_snapshot` column for a run result."""
    return json.dumps([tool.to_json() for tool in snapshot])


def _json_array(raw: str | None) -> list[Any]:
    """A stored JSON array, or an empty list for anything else.

    Every reader below degrades rather than raising: a malformed snapshot must
    not keep a past run from rendering.
    """
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except ValueError:
        return []
    return parsed if isinstance(parsed, list) else []


def _tool_source(value: Any) -> ToolSource:
    """A stored `source`, or `manual` for anything this build does not know.

    The same fail-closed shape as `app.auth.policy.parse_role`, and for the same
    reason: the column is plain text, so a value written by a later build has to
    degrade to the one source that needs nothing looked up. It answers the model
    with an error it can read instead of calling a server — or a corpus — nobody
    named.
    """
    return value if value in TOOL_SOURCES else "manual"  # type: ignore[return-value]


def parse_tools_snapshot(raw: str | None) -> list[SnapshotTool]:
    """Reads a `tools_snapshot` column back, skipping anything malformed.

    Every reader degrades: an unrecognized `source` becomes `manual`
    (:func:`_tool_source`), and the two corpus keys are absent from every
    snapshot frozen before they existed, so they read as None there.
    """
    tools: list[SnapshotTool] = []
    for entry in _json_array(raw):
        if not isinstance(entry, dict):
            continue
        definition = entry.get("definition")
        if not isinstance(definition, dict):
            continue
        function = definition.get("function")
        if not isinstance(function, dict) or not isinstance(function.get("name"), str):
            continue

        source = entry.get("source")
        toolset_id = entry.get("toolset_id")
        toolset_name = entry.get("toolset_name")
        mock_response = entry.get("mock_response")

        tools.append(
            SnapshotTool(
                definition=definition,
                source=_tool_source(source),
                toolset_id=toolset_id if isinstance(toolset_id, int) else 0,
                toolset_name=toolset_name if isinstance(toolset_name, str) else "",
                mock_response=mock_response if isinstance(mock_response, str) else None,
                document_count=_optional_int(entry.get("document_count")),
                corpus_updated_at=_optional_str(entry.get("corpus_updated_at")),
            )
        )
    return tools


def snapshot_definitions(snapshot: Sequence[SnapshotTool]) -> list[ToolDefinition]:
    """The wire `tools` array for a snapshot."""
    return [tool.definition for tool in snapshot]


def snapshot_tool_names(snapshot: Sequence[SnapshotTool]) -> list[str]:
    """Names in a snapshot, for compact display in lists and the matrix."""
    return [tool.name for tool in snapshot]


# ---------------------------------------------------------------------------
# Tool-call arguments
# ---------------------------------------------------------------------------


class ParsedArguments(NamedTuple):
    """The outcome of reading a tool call's `arguments` string.

    Models emit malformed JSON often enough that this has to be a recorded
    finding rather than a crash: the caller feeds `error` back to the model as
    the tool's output, which is exactly what a real agent would see.
    """

    value: dict[str, Any] | None
    error: str | None

    @property
    def ok(self) -> bool:
        return self.error is None


def parse_tool_arguments(raw: str | None) -> ParsedArguments:
    """Parses the `arguments` string of a tool call.

    An empty string is how a no-argument call arrives and parses to `{}`;
    anything that is not a JSON *object* is an error the model gets to read.
    """
    text = (raw or "").strip()
    if not text:
        return ParsedArguments({}, None)

    try:
        parsed = json.loads(text)
    except ValueError as exc:
        return ParsedArguments(None, f"Arguments are not valid JSON: {exc}")

    if not isinstance(parsed, dict):
        return ParsedArguments(None, "Arguments must be a JSON object.")
    return ParsedArguments(parsed, None)


# ---------------------------------------------------------------------------
# Per-turn metrics and the transcript
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TurnMetrics:
    """Metrics for a single model turn; the `turns_json` element."""

    #: 0-based turn number.
    index: int
    ttft_ms: int | None
    duration_ms: int
    prompt_tokens: int | None
    completion_tokens: int
    tokens_estimated: bool
    finish_reason: str | None
    tool_call_count: int

    def to_json(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "ttft_ms": self.ttft_ms,
            "duration_ms": self.duration_ms,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "tokens_estimated": self.tokens_estimated,
            "finish_reason": self.finish_reason,
            "tool_call_count": self.tool_call_count,
        }


@dataclass(frozen=True)
class TranscriptMessage:
    """A persisted message: the wire shape plus display-only annotations.

    `turn` and the two tool timings are never sent to the model — they exist so
    the run detail view can lay the conversation out turn by turn.
    """

    role: str
    content: str
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None
    name: str | None = None
    #: Which model turn produced (or consumed) this message.
    turn: int | None = None
    #: Wall time the tool itself took.
    tool_duration_ms: int | None = None
    #: The tool failed and its error text was fed back to the model.
    tool_is_error: bool | None = None

    def to_json(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_calls:
            payload["tool_calls"] = [
                {"id": call.id, "name": call.name, "arguments": call.arguments}
                for call in self.tool_calls
            ]
        if self.tool_call_id is not None:
            payload["tool_call_id"] = self.tool_call_id
        if self.name is not None:
            payload["name"] = self.name
        if self.turn is not None:
            payload["turn"] = self.turn
        if self.tool_duration_ms is not None:
            payload["tool_duration_ms"] = self.tool_duration_ms
        if self.tool_is_error is not None:
            payload["tool_is_error"] = self.tool_is_error
        return payload


_ROLES = ("system", "user", "assistant", "tool")


def _parse_tool_calls(value: Any) -> list[ToolCall] | None:
    if not isinstance(value, list):
        return None
    calls = [
        ToolCall(
            id=entry.get("id") if isinstance(entry.get("id"), str) else "",
            name=entry.get("name") if isinstance(entry.get("name"), str) else "",
            arguments=entry.get("arguments") if isinstance(entry.get("arguments"), str) else "",
        )
        for entry in value
        if isinstance(entry, dict)
    ]
    return calls or None


def parse_transcript(raw: str | None) -> list[TranscriptMessage] | None:
    """Reads a `transcript_json` column back. None when the run had none."""
    messages = []
    for entry in _json_array(raw):
        if not isinstance(entry, dict) or entry.get("role") not in _ROLES:
            continue
        content = entry.get("content")
        messages.append(
            TranscriptMessage(
                role=entry["role"],
                content=content if isinstance(content, str) else "",
                tool_calls=_parse_tool_calls(entry.get("tool_calls")),
                tool_call_id=_optional_str(entry.get("tool_call_id")),
                name=_optional_str(entry.get("name")),
                turn=_optional_int(entry.get("turn")),
                tool_duration_ms=_optional_int(entry.get("tool_duration_ms")),
                tool_is_error=(
                    entry["tool_is_error"] if isinstance(entry.get("tool_is_error"), bool) else None
                ),
            )
        )
    return messages or None


def parse_turns(raw: str | None) -> list[TurnMetrics]:
    """Reads a `turns_json` column back."""
    turns = []
    for entry in _json_array(raw):
        if not isinstance(entry, dict) or _optional_int(entry.get("index")) is None:
            continue
        turns.append(
            TurnMetrics(
                index=entry["index"],
                ttft_ms=_optional_int(entry.get("ttft_ms")),
                duration_ms=_optional_int(entry.get("duration_ms")) or 0,
                prompt_tokens=_optional_int(entry.get("prompt_tokens")),
                completion_tokens=_optional_int(entry.get("completion_tokens")) or 0,
                tokens_estimated=bool(entry.get("tokens_estimated")),
                finish_reason=_optional_str(entry.get("finish_reason")),
                tool_call_count=_optional_int(entry.get("tool_call_count")) or 0,
            )
        )
    return turns


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _optional_int(value: Any) -> int | None:
    """`True` is not a number, and a float token count is still a count."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return None


def serialize_transcript(messages: Sequence[TranscriptMessage]) -> str:
    """The `transcript_json` column for a run result."""
    return json.dumps([message.to_json() for message in messages])


def serialize_turns(turns: Sequence[TurnMetrics]) -> str:
    """The `turns_json` column for a run result."""
    return json.dumps([turn.to_json() for turn in turns])


# ---------------------------------------------------------------------------
# Executing one call
# ---------------------------------------------------------------------------


class ToolExecutionOutcome(NamedTuple):
    """Outcome of running one tool call. An error is data, not an exception."""

    content: str
    is_error: bool


#: Runs one call the loop cannot answer from the snapshot alone — an MCP tool or
#: a document tool. Required for `execute` mode with either, unused otherwise.
#: Built by the executor, which holds the scope and the session needed to look a
#: server's live URL and headers, or a corpus's markdown, up.
ToolExecutor = Callable[[ToolCall], Awaitable[ToolExecutionOutcome]]


def error_payload(message: str) -> str:
    """Serializes a tool failure into something the model can read and react to."""
    return json.dumps({"error": message})


async def _execute_one(
    call: ToolCall,
    snapshot: Sequence[SnapshotTool],
    execute_tool: ToolExecutor | None,
) -> ToolExecutionOutcome:
    """Dispatches one call.

    A failure here is never a failed result — see the module docstring. Every
    branch answers *something*, because a model left waiting on a tool message
    that never arrives is the one outcome that teaches nothing.
    """
    entry = next((tool for tool in snapshot if tool.name == call.name), None)
    if entry is None:
        return ToolExecutionOutcome(
            error_payload(
                f'The model called "{call.name}", which was not one of the tools it was offered.'
            ),
            True,
        )

    parsed = parse_tool_arguments(call.arguments)
    if parsed.error is not None:
        return ToolExecutionOutcome(error_payload(parsed.error), True)

    if entry.source == "manual":
        if entry.mock_response is None:
            return ToolExecutionOutcome(
                error_payload("This tool has no canned response configured."), True
            )
        return ToolExecutionOutcome(entry.mock_response, False)

    if execute_tool is None:
        # Reachable when the toolset behind the call was deleted or switched
        # kind after the run was frozen, so the executor found nothing live to
        # build against. Naming which kind of tool went unanswered is the
        # difference between a readable transcript and a puzzle.
        kind = "document tools" if entry.source == "documents" else "MCP tools"
        return ToolExecutionOutcome(
            error_payload(f"No executor is configured for {kind} in this run."), True
        )

    try:
        return await execute_tool(call)
    except Exception as exc:  # noqa: BLE001 - a tool failure is data, not a crash
        # Deliberately not `BaseException`: a `CancelledError` (the client
        # disconnected mid-run) has to keep propagating so the executor can
        # reset the in-flight row to `pending`.
        return ToolExecutionOutcome(error_payload(str(exc) or "Tool execution failed."), True)


# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------


class ChatStreamer(Protocol):
    """The one call the loop makes against an endpoint — a test seam.

    Production passes :func:`app.services.llm.stream_chat`; the tests pass a
    scripted stand-in, which is what keeps the loop's own behaviour (turn
    budget, transcript assembly, tool dispatch) testable without a socket.
    """

    async def __call__(
        self,
        base_url: str,
        api_key: str | None,
        model: str,
        messages: Sequence[ChatMessage],
        *,
        tools: Sequence[Mapping[str, Any]] | None = None,
        tool_choice: ToolChoice | None = None,
        params: Mapping[str, Any] | None = None,
        on_delta: DeltaCallback | None = None,
    ) -> LlmResult: ...


@dataclass(frozen=True)
class Aggregates:
    """Per-turn metrics folded into the columns `run_results` already has."""

    ttft_ms: int | None
    duration_ms: int
    prompt_tokens: int | None
    completion_tokens: int
    tokens_estimated: bool
    tokens_per_sec: float | None


@dataclass(frozen=True)
class ToolRunResult:
    """Everything one result row needs, tools or no tools."""

    #: Final assistant text — what `run_results.response_text` holds.
    text: str
    transcript: list[TranscriptMessage]
    turns: list[TurnMetrics]
    stopped_reason: StoppedReason
    #: Aggregates, written into the pre-existing metric columns.
    ttft_ms: int | None
    duration_ms: int
    prompt_tokens: int | None
    completion_tokens: int
    tokens_estimated: bool
    tokens_per_sec: float | None
    tool_call_count: int
    #: Turns the model actually took, i.e. `len(turns)`.
    turn_count: int = 0

    @property
    def transcript_json(self) -> str:
        return serialize_transcript(self.transcript)

    @property
    def turns_json(self) -> str:
        return serialize_turns(self.turns)


async def run_tool_loop(
    *,
    base_url: str,
    api_key: str | None = None,
    model: str,
    user_message: str,
    system_prompt: str | None = None,
    params: Mapping[str, Any] | None = None,
    # Frozen tool configuration. Empty means a plain one-shot completion.
    snapshot: Sequence[SnapshotTool] = (),
    tool_mode: ToolMode = "none",
    tool_choice: ToolChoice | None = None,
    max_turns: int | None = None,
    execute_tool: ToolExecutor | None = None,
    on_turn_start: Callable[[int], None] | None = None,
    # Streaming text of the current turn. Throttling is the caller's business.
    on_delta: TurnDeltaCallback | None = None,
    on_tool_calls: Callable[[int, list[ToolCall]], None] | None = None,
    on_tool_result: Callable[[int, TranscriptMessage], None] | None = None,
    stream: ChatStreamer = stream_chat,
) -> ToolRunResult:
    """Runs one test case to completion: one model turn, or several with tools.

    A snapshot with no definitions, or `tool_mode="none"`, degenerates to a
    single turn that sends no `tools` array at all — which is what keeps a
    plain prompt's request byte-identical to what it was before tools existed.

    Raises :class:`~app.services.llm.LlmError` straight through: only the model
    connection can fail a result row, and grading that failure is the
    executor's job.
    """
    definitions = snapshot_definitions(snapshot)
    active_mode: ToolMode = tool_mode if definitions else "none"
    active = active_mode != "none"
    turn_budget = normalize_max_turns(max_turns) if active else 1

    messages: list[ChatMessage] = []
    transcript: list[TranscriptMessage] = []

    if system_prompt and system_prompt.strip():
        messages.append({"role": "system", "content": system_prompt})
        transcript.append(TranscriptMessage(role="system", content=system_prompt))
    messages.append({"role": "user", "content": user_message})
    transcript.append(TranscriptMessage(role="user", content=user_message))

    turns: list[TurnMetrics] = []
    stopped_reason: StoppedReason = "stop"
    final_text = ""
    tool_call_count = 0

    for turn in range(turn_budget):
        if on_turn_start is not None:
            on_turn_start(turn)

        metrics = await stream(
            base_url,
            api_key,
            model,
            messages,
            tools=definitions if active else None,
            tool_choice=tool_choice if active else None,
            params=params,
            on_delta=(
                None
                if on_delta is None
                else (lambda _delta, text_so_far, turn=turn: on_delta(turn, text_so_far))
            ),
        )

        turns.append(
            TurnMetrics(
                index=turn,
                ttft_ms=metrics.ttft_ms,
                duration_ms=metrics.duration_ms,
                prompt_tokens=metrics.prompt_tokens,
                completion_tokens=metrics.completion_tokens,
                tokens_estimated=metrics.tokens_estimated,
                finish_reason=metrics.finish_reason,
                tool_call_count=len(metrics.tool_calls),
            )
        )

        assistant: ChatMessage = {"role": "assistant", "content": metrics.text}
        if metrics.tool_calls:
            assistant["tool_calls"] = [call.to_wire() for call in metrics.tool_calls]
        messages.append(assistant)
        transcript.append(
            TranscriptMessage(
                role="assistant",
                content=metrics.text,
                turn=turn,
                tool_calls=list(metrics.tool_calls) or None,
            )
        )

        # The last assistant text wins, but a turn that only asked for tools
        # must not blank out an answer the model gave alongside its calls.
        if metrics.text:
            final_text = metrics.text

        if not metrics.tool_calls:
            stopped_reason = "stop"
            break

        tool_call_count += len(metrics.tool_calls)
        if on_tool_calls is not None:
            on_tool_calls(turn, list(metrics.tool_calls))

        if active_mode == "definitions":
            stopped_reason = "definitions_only"
            break

        # Out of budget: stop before running anything. Executing tools whose
        # results can never reach the model would mean side effects on a real
        # system for nothing.
        if turn == turn_budget - 1:
            stopped_reason = "max_turns"
            break

        for call in metrics.tool_calls:
            started_at = monotonic()
            outcome = await _execute_one(call, snapshot, execute_tool)
            duration_ms = round((monotonic() - started_at) * 1000)

            messages.append(
                {
                    "role": "tool",
                    "content": outcome.content,
                    "tool_call_id": call.id,
                    "name": call.name,
                }
            )
            message = TranscriptMessage(
                role="tool",
                content=outcome.content,
                tool_call_id=call.id,
                name=call.name,
                turn=turn,
                tool_duration_ms=duration_ms,
                tool_is_error=outcome.is_error,
            )
            transcript.append(message)
            if on_tool_result is not None:
                on_tool_result(turn, message)

    totals = aggregate(turns)
    return ToolRunResult(
        text=final_text,
        transcript=transcript,
        turns=turns,
        stopped_reason=stopped_reason,
        ttft_ms=totals.ttft_ms,
        duration_ms=totals.duration_ms,
        prompt_tokens=totals.prompt_tokens,
        completion_tokens=totals.completion_tokens,
        tokens_estimated=totals.tokens_estimated,
        tokens_per_sec=totals.tokens_per_sec,
        tool_call_count=tool_call_count,
        turn_count=len(turns),
    )


def aggregate(turns: Sequence[TurnMetrics]) -> Aggregates:
    """Folds per-turn metrics into the columns that already exist.

    `duration_ms` deliberately sums only the model turns and excludes the time
    tools spent working — otherwise waiting on a slow ERP would show up as the
    model being slow. Tool timings live per call in the transcript.

    The throughput denominator is the sum of each turn's own generation window,
    so a multi-turn rate stays a real tokens-per-second figure instead of being
    diluted by later prefills. For a single turn it reduces exactly to what
    :func:`~app.services.llm.compute_tokens_per_sec` produces on its own.
    """
    if not turns:
        return Aggregates(
            ttft_ms=None,
            duration_ms=0,
            prompt_tokens=None,
            completion_tokens=0,
            tokens_estimated=True,
            tokens_per_sec=None,
        )

    duration_ms = sum(turn.duration_ms for turn in turns)
    completion_tokens = sum(turn.completion_tokens for turn in turns)
    generation_ms = sum(max(0, turn.duration_ms - (turn.ttft_ms or 0)) for turn in turns)

    prompt_turns = [turn for turn in turns if turn.prompt_tokens is not None]
    prompt_tokens = sum(turn.prompt_tokens or 0 for turn in prompt_turns) if prompt_turns else None

    return Aggregates(
        ttft_ms=turns[0].ttft_ms,
        duration_ms=duration_ms,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        tokens_estimated=any(turn.tokens_estimated for turn in turns),
        tokens_per_sec=compute_tokens_per_sec(completion_tokens, generation_ms, 0),
    )


__all__ = [
    "Aggregates",
    "ChatStreamer",
    "ParsedArguments",
    "SnapshotTool",
    "ToolDefinition",
    "ToolExecutionOutcome",
    "ToolExecutor",
    "ToolRunResult",
    "TranscriptMessage",
    "TurnMetrics",
    "aggregate",
    "error_payload",
    "parse_tool_arguments",
    "parse_tools_snapshot",
    "parse_transcript",
    "parse_turns",
    "run_tool_loop",
    "serialize_tools_snapshot",
    "serialize_transcript",
    "serialize_turns",
    "snapshot_definitions",
    "snapshot_tool_names",
]
