"""Wire format for the NDJSON stream produced by `POST /api/runs/{id}/execute`.

One event per line, each a JSON object with a `type` — a plain
`Content-Type: application/x-ndjson` body rather than SSE, because the client
is a `fetch` reader and not an `EventSource`, and NDJSON survives a proxy
that would otherwise reframe SSE.

Two shapes are load-bearing and deliberately conditional:

* `delta` carries a `turn` **only** on a tool run, so a plain one-shot prompt's
  stream is byte-identical to what it would have been before tools existed.
* `resultDone` carries `transcript` / `turns` / `stopped_reason` only for tool
  runs, for the same reason — and so the finished card can render without a
  reload.

Event *type* names stay camelCase (`runStart`, `resultStart`, …) because they
are the protocol's vocabulary, named by the plan; field names are snake_case
like every other response this API produces.

Kept free of the database and of `app.repos` on purpose: this is the contract
between the executor and the browser, and nothing else.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, ClassVar

from app.models.runs import RunStatus, StoppedReason
from app.services.llm import ToolCall
from app.services.tool_loop import TranscriptMessage, TurnMetrics


@dataclass(frozen=True)
class ResultMetrics:
    """What one finished result measured, as the client needs it.

    `turn_count`/`tool_call_count` are null for the classic one-shot path, which
    is exactly how they are stored.
    """

    duration_ms: int | None
    ttft_ms: int | None
    prompt_tokens: int | None
    completion_tokens: int | None
    tokens_per_sec: float | None
    tokens_estimated: bool
    turn_count: int | None = None
    tool_call_count: int | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "duration_ms": self.duration_ms,
            "ttft_ms": self.ttft_ms,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "tokens_per_sec": self.tokens_per_sec,
            "tokens_estimated": self.tokens_estimated,
            "turn_count": self.turn_count,
            "tool_call_count": self.tool_call_count,
        }


@dataclass(frozen=True)
class _Event:
    """Base of the event union: every event serializes to `{type, ...}`."""

    TYPE: ClassVar[str] = ""

    def payload(self) -> dict[str, Any]:
        raise NotImplementedError

    def to_json(self) -> dict[str, Any]:
        return {"type": self.TYPE, **self.payload()}


@dataclass(frozen=True)
class RunStart(_Event):
    """Execution began. `pending` of `total` rows will be attempted."""

    TYPE: ClassVar[str] = "runStart"

    run_id: int
    pending: int
    total: int

    def payload(self) -> dict[str, Any]:
        return {"run_id": self.run_id, "pending": self.pending, "total": self.total}


@dataclass(frozen=True)
class ResultStart(_Event):
    """One row started. `index` is 1-based over *all* rows, not just pending
    ones, so the progress line reads "3 of 12" the way the detail page lists
    them.
    """

    TYPE: ClassVar[str] = "resultStart"

    result_id: int
    index: int
    total: int

    def payload(self) -> dict[str, Any]:
        return {"result_id": self.result_id, "index": self.index, "total": self.total}


@dataclass(frozen=True)
class TurnStart(_Event):
    """A new model turn began. Only emitted for tool runs."""

    TYPE: ClassVar[str] = "turnStart"

    result_id: int
    turn: int

    def payload(self) -> dict[str, Any]:
        return {"result_id": self.result_id, "turn": self.turn}


@dataclass(frozen=True)
class Delta(_Event):
    """Throttled progress. `text` is the full response of this turn so far.

    `turn` is omitted entirely on a plain prompt — see the module docstring.
    """

    TYPE: ClassVar[str] = "delta"

    result_id: int
    text: str
    turn: int | None = None

    def payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"result_id": self.result_id, "text": self.text}
        if self.turn is not None:
            payload["turn"] = self.turn
        return payload


@dataclass(frozen=True)
class ToolCallEvent(_Event):
    """The model asked to call tools."""

    TYPE: ClassVar[str] = "toolCall"

    result_id: int
    turn: int
    calls: Sequence[ToolCall]

    def payload(self) -> dict[str, Any]:
        return {
            "result_id": self.result_id,
            "turn": self.turn,
            "calls": [
                {"id": call.id, "name": call.name, "arguments": call.arguments}
                for call in self.calls
            ],
        }


@dataclass(frozen=True)
class ToolResultEvent(_Event):
    """One tool finished and its output was fed back to the model."""

    TYPE: ClassVar[str] = "toolResult"

    result_id: int
    turn: int
    message: TranscriptMessage

    def payload(self) -> dict[str, Any]:
        return {
            "result_id": self.result_id,
            "turn": self.turn,
            "message": self.message.to_json(),
        }


@dataclass(frozen=True)
class ResultDone(_Event):
    """A row finished successfully and is already persisted."""

    TYPE: ClassVar[str] = "resultDone"

    result_id: int
    text: str
    metrics: ResultMetrics
    #: Present for tool runs only, so the card can render without a reload.
    transcript: Sequence[TranscriptMessage] | None = None
    turns: Sequence[TurnMetrics] | None = None
    stopped_reason: StoppedReason | None = None

    def payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "result_id": self.result_id,
            "text": self.text,
            "metrics": self.metrics.to_json(),
        }
        if self.transcript is not None:
            payload["transcript"] = [message.to_json() for message in self.transcript]
        if self.turns is not None:
            payload["turns"] = [turn.to_json() for turn in self.turns]
        if self.stopped_reason is not None:
            payload["stopped_reason"] = self.stopped_reason
        return payload


@dataclass(frozen=True)
class ResultError(_Event):
    """A row failed. The loop continues with the next one."""

    TYPE: ClassVar[str] = "resultError"

    result_id: int
    error: str

    def payload(self) -> dict[str, Any]:
        return {"result_id": self.result_id, "error": self.error}


@dataclass(frozen=True)
class Aborted(_Event):
    """The client hung up. `result_id` (when set) was reset to `pending`."""

    TYPE: ClassVar[str] = "aborted"

    result_id: int | None

    def payload(self) -> dict[str, Any]:
        return {"result_id": self.result_id}


@dataclass(frozen=True)
class RunDone(_Event):
    """Execution finished, with the run's resulting status."""

    TYPE: ClassVar[str] = "runDone"

    run_id: int
    status: RunStatus
    #: True when there was nothing left to do — the stream's whole content.
    nothing_pending: bool = False

    def payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"run_id": self.run_id, "status": self.status}
        if self.nothing_pending:
            payload["nothing_pending"] = True
        return payload


@dataclass(frozen=True)
class RunError(_Event):
    """Execution could not start, or crashed outside of a single result."""

    TYPE: ClassVar[str] = "runError"

    run_id: int
    error: str

    def payload(self) -> dict[str, Any]:
        return {"run_id": self.run_id, "error": self.error}


#: Anything the executor may emit.
RunEvent = (
    RunStart
    | ResultStart
    | TurnStart
    | Delta
    | ToolCallEvent
    | ToolResultEvent
    | ResultDone
    | ResultError
    | Aborted
    | RunDone
    | RunError
)

#: What the executor is handed. Synchronous — the route's only use is to push
#: onto a queue, and an awaitable here would put the response's scheduling
#: inside the token loop.
EmitRunEvent = Callable[[RunEvent], None]

#: The one media type this stream is served as.
NDJSON_MEDIA_TYPE = "application/x-ndjson; charset=utf-8"


def serialize_event(event: RunEvent) -> str:
    """One NDJSON line, newline included."""
    return f"{json.dumps(event.to_json())}\n"


__all__ = [
    "NDJSON_MEDIA_TYPE",
    "Aborted",
    "Delta",
    "EmitRunEvent",
    "ResultDone",
    "ResultError",
    "ResultMetrics",
    "ResultStart",
    "RunDone",
    "RunError",
    "RunEvent",
    "RunStart",
    "ToolCallEvent",
    "ToolResultEvent",
    "TurnStart",
    "serialize_event",
]
