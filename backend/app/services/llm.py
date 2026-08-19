"""A streaming client for OpenAI-compatible `/chat/completions` endpoints.

Raw HTTP (httpx) and hand-rolled SSE parsing, no vendor SDK: the whole point
of this app is that *any* endpoint —
vLLM, Ollama, LM Studio, a hosted frontier API — can be measured side by side,
and the providers differ in exactly the places an SDK would hide:

* **Where usage lives.** vLLM (and LM Studio with `stream_options`) send a
  final `choices: []` chunk carrying `usage`; Ollama piggybacks it on the last
  content chunk; some servers never send one at all, in which case tokens are
  estimated and the row is flagged so the UI can print `~`.
* **How tool calls are streamed.** `delta.tool_calls[]` arrives as fragments
  that can be split anywhere — including mid-JSON — keyed by `index`, or by
  `id`, or by nothing at all. :class:`_ToolCallAccumulator` stitches all three
  shapes back together.
* **Where a line ends.** A network read can cut an SSE line in half, so
  :func:`parse_sse_chunk` hands the unfinished tail back to the next call.
* **Where the thinking goes.** Inline in `delta.content` wrapped in `<think>`
  tags (Ollama, vLLM without a reasoning parser), or split off onto
  `delta.reasoning_content` (vLLM with `--reasoning-parser`, DeepSeek). Both are
  read; the split-off shape lands in :attr:`LlmResult.reasoning_text`.

The measurement contract is what the rest of the app depends on:

* `ttft_ms` is the time to the first piece of output **of any kind** — a
  reasoning delta, a content delta, or a tool-call fragment. Reasoning counts
  because it is generation: measured from the first *visible* character, a
  thinking model's whole chain of thought falls inside the prefill that
  `compute_tokens_per_sec` excludes while `completion_tokens` still counts every
  reasoning token, which reported a 27-character answer at 3958 tok/s.
* `ttft_content_ms` is that older reading, kept as its own number: a real
  latency for a thinking model, and a useless throughput denominator.
* `duration_ms` is the whole stream, and
  :func:`compute_tokens_per_sec` divides completion tokens by the *generation*
  window (`duration - ttft`), never by the total, so a slow prefill does not
  read as a slow endpoint.
* The answer is stored with **leading whitespace stripped** — after `</think>`
  the chat template emits a newline pair, and a rubric demanding raw JSON with
  no preamble fails on it.

The parsing half (:func:`parse_sse_chunk`,
:func:`consume_chat_completion_stream`, :func:`compute_tokens_per_sec`) is kept
strictly separate from the HTTP half (:func:`stream_chat`) so recorded chunk
sequences from real endpoints can be replayed without a socket — the same
split the old `llm.test.ts` fixtures relied on, and the same
network-free-service split `app.services.discovery` makes.
"""

from __future__ import annotations

import asyncio
import json
import math
from collections.abc import AsyncIterable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from time import monotonic
from typing import Any, Literal, NamedTuple

import httpx

from app.models.toolsets import ToolChoice
from app.services.discovery import describe_transport_error

#: Hard ceiling for one completion, wall clock. A long agentic answer on a
#: small box is slow, not broken — mirrors the old `DEFAULT_TIMEOUT_MS`.
DEFAULT_TIMEOUT_S = 300.0

#: Tokens per second no single-stream decode reaches, on any hardware, and the
#: shortest window that can be told apart from a TTFT which swallowed the
#: generation it was meant to exclude. Both halves guard
#: :func:`compute_tokens_per_sec`; see its docstring for why one is not enough.
IMPLAUSIBLE_RATE = 2000.0
MIN_PLAUSIBLE_GENERATION_MS = 250.0

#: The `data:` payload that ends an OpenAI-compatible stream.
DONE_SENTINEL = "[DONE]"

#: One entry of the `messages` array, as it goes on the wire. Left as a plain
#: dict rather than a model: the shape differs per role (`tool` messages carry
#: `tool_call_id`, assistant messages may carry `tool_calls`), and every one of
#: them is serialized verbatim into the request body.
ChatMessage = dict[str, Any]

#: `(delta, text_so_far)`. Synchronous on purpose — the executor's only use is
#: to push an event onto a queue, and an awaitable here would put the caller's
#: scheduling inside the token loop.
DeltaCallback = Callable[[str, str], None]


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

LlmErrorKind = Literal[
    # The endpoint could not be reached at all (DNS, refused, reset, ...).
    "connection",
    # The request exceeded its timeout.
    "timeout",
    # The endpoint answered with a non-2xx status.
    "http",
    # The endpoint answered, but the stream was malformed or reported an error.
    "stream",
]


class LlmError(Exception):
    """A completion that did not produce a result, with *why* attached.

    The `kind` is what the executor grades a run on: only a connection-level
    failure means "the endpoint was never reachable" and can mark a whole run
    `failed`. An HTTP 400 or a mid-stream error is a failure of that one row.
    """

    def __init__(self, message: str, kind: LlmErrorKind, status: int | None = None) -> None:
        super().__init__(message)
        self.kind: LlmErrorKind = kind
        self.status = status

    @property
    def is_connection_level(self) -> bool:
        """True when the failure means the endpoint was never reached."""
        return self.kind in ("connection", "timeout")


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolCall:
    """One function call the model asked for.

    Flattened compared with the wire shape (which nests `name`/`arguments`
    under `function`) because every consumer — the tool loop, the transcript,
    the UI — wants the two strings; :meth:`to_wire` puts the nesting back for
    the assistant message that has to be echoed to the endpoint.

    `arguments` is kept **verbatim**, malformed JSON included: parsing it is
    the tool loop's job, and a model emitting broken JSON is a finding worth
    recording, not a crash.
    """

    id: str
    name: str
    arguments: str

    def to_wire(self) -> dict[str, Any]:
        """The OpenAI-compatible `tool_calls[]` entry."""
        return {
            "id": self.id,
            "type": "function",
            "function": {"name": self.name, "arguments": self.arguments},
        }


@dataclass(frozen=True)
class LlmResult:
    """Everything one completion produced: the text, the calls, the numbers."""

    #: The answer, with leading whitespace stripped.
    text: str
    #: First output of *any* kind, reasoning included — the throughput
    #: denominator, and the only TTFT comparable across model types. None when
    #: the endpoint streamed nothing at all.
    ttft_ms: int | None
    duration_ms: int
    prompt_tokens: int | None
    completion_tokens: int
    #: True when no usage block arrived and `completion_tokens` is a guess.
    tokens_estimated: bool
    #: In the order the model emitted them (by `index` where one was sent).
    tool_calls: list[ToolCall] = field(default_factory=list)
    #: `stop`, `tool_calls`, `length`, ... as reported by the endpoint.
    finish_reason: str | None = None
    #: Empty when the model did not think, and when it inlined `<think>` tags
    #: into `text` instead — that shape is split for display client-side.
    reasoning_text: str = ""
    #: Part of `completion_tokens`, never additional to it.
    reasoning_tokens: int | None = None
    #: Time to the first *visible* token. None when nothing was ever visible.
    ttft_content_ms: int | None = None


# ---------------------------------------------------------------------------
# SSE parsing (pure)
# ---------------------------------------------------------------------------


class SSEParseResult(NamedTuple):
    """Complete `data:` payloads plus the unfinished tail of this read."""

    events: list[str]
    buffer: str


def parse_sse_chunk(buffer: str, chunk: str) -> SSEParseResult:
    """Feeds one network read into the SSE line parser.

    A read can split a line anywhere (even mid-JSON), so the unfinished tail is
    handed back as `buffer` and prepended to the next call. Comment lines
    (`: ping`) and non-`data:` fields (`event:`, `id:`) are ignored.
    """
    lines = (buffer + chunk).split("\n")
    rest = lines.pop()
    events: list[str] = []

    for raw_line in lines:
        line = raw_line[:-1] if raw_line.endswith("\r") else raw_line
        if not line or line.startswith(":"):
            continue
        if line.startswith("data:"):
            events.append(line[5:].strip())

    return SSEParseResult(events, rest)


# ---------------------------------------------------------------------------
# Chunk readers
# ---------------------------------------------------------------------------


def _as_int(value: Any) -> int | None:
    """A JSON number as an int, or None. `True` is not a token count."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return int(value)
    return None


def _first_delta(payload: Any) -> dict[str, Any] | None:
    """`choices[0].delta`, if this payload has one."""
    if not isinstance(payload, dict):
        return None
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    first = choices[0]
    if not isinstance(first, dict):
        return None
    delta = first.get("delta")
    return delta if isinstance(delta, dict) else None


class _Usage(NamedTuple):
    prompt_tokens: int | None
    completion_tokens: int | None
    #: A subset of `completion_tokens`, not an addition to it.
    reasoning_tokens: int | None = None


def _read_usage(payload: Any) -> _Usage | None:
    if not isinstance(payload, dict):
        return None
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return None
    details = usage.get("completion_tokens_details")
    reasoning_tokens = (
        _as_int(details.get("reasoning_tokens")) if isinstance(details, dict) else None
    )
    return _Usage(
        _as_int(usage.get("prompt_tokens")),
        _as_int(usage.get("completion_tokens")),
        reasoning_tokens,
    )


def _read_delta_content(payload: Any) -> str | None:
    delta = _first_delta(payload)
    if delta is None:
        return None
    content = delta.get("content")
    return content if isinstance(content, str) and content else None


#: `reasoning_content` is vLLM's `--reasoning-parser` and DeepSeek's API;
#: `reasoning` is what several OpenAI-compatible proxies use instead. First
#: non-empty wins — a payload never carries both.
REASONING_DELTA_FIELDS = ("reasoning_content", "reasoning")


def _read_delta_reasoning(payload: Any) -> str | None:
    """The reasoning fragment of one chunk, when it has its own channel.

    A provider that inlines `<think>` tags never reaches here; that shape stays
    in :attr:`LlmResult.text`.
    """
    delta = _first_delta(payload)
    if delta is None:
        return None
    for name in REASONING_DELTA_FIELDS:
        value = delta.get(name)
        if isinstance(value, str) and value:
            return value
    return None


@dataclass(frozen=True)
class _ToolCallFragment:
    """One `delta.tool_calls[]` entry, before fragments are stitched."""

    #: Slot the fragment belongs to. None when the endpoint omits `index`.
    index: int | None
    id: str | None
    name: str | None
    arguments_fragment: str | None


def _read_delta_tool_calls(payload: Any) -> list[_ToolCallFragment]:
    delta = _first_delta(payload)
    if delta is None:
        return []
    entries = delta.get("tool_calls")
    if not isinstance(entries, list):
        return []

    fragments: list[_ToolCallFragment] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        function = entry.get("function")
        function = function if isinstance(function, dict) else {}

        index = entry.get("index")
        call_id = entry.get("id")
        name = function.get("name")
        arguments = function.get("arguments")

        fragments.append(
            _ToolCallFragment(
                index=index if isinstance(index, int) and not isinstance(index, bool) else None,
                id=call_id if isinstance(call_id, str) and call_id else None,
                name=name if isinstance(name, str) and name else None,
                arguments_fragment=arguments if isinstance(arguments, str) else None,
            )
        )
    return fragments


def _read_finish_reason(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    first = choices[0]
    if not isinstance(first, dict):
        return None
    reason = first.get("finish_reason")
    return reason if isinstance(reason, str) and reason else None


def _read_streamed_error(payload: Any) -> str | None:
    """An `error` field sent *inside* an otherwise 200 stream."""
    if not isinstance(payload, dict):
        return None
    error = payload.get("error")
    if not error:
        return None
    if isinstance(error, str):
        return error
    if isinstance(error, dict):
        message = error.get("message")
        if isinstance(message, str):
            return message
    return "The server reported an error mid-stream."


# ---------------------------------------------------------------------------
# Tool-call stitching
# ---------------------------------------------------------------------------


@dataclass
class _PartialToolCall:
    index: int
    id: str | None = None
    name: str = ""
    arguments: str = ""


class _ToolCallAccumulator:
    """Stitches streamed `tool_calls` fragments back into whole calls.

    Endpoints differ as much here as they do over usage. vLLM streams one entry
    per call keyed by `index`, with `function.arguments` arriving as string
    fragments that can be split anywhere — including mid-JSON. Others send a
    finished call in a single chunk, and a few omit `index` entirely, in which
    case the call's `id` (or arrival order) has to stand in for it.
    """

    def __init__(self) -> None:
        self._slots: dict[int, _PartialToolCall] = {}
        self._index_by_id: dict[str, int] = {}

    def add(self, fragment: _ToolCallFragment) -> None:
        index = self._slot_for(fragment)
        slot = self._slots.get(index)
        if slot is None:
            slot = _PartialToolCall(index=index)
            self._slots[index] = slot

        if fragment.id is not None:
            slot.id = fragment.id
            self._index_by_id[fragment.id] = index
        # A name can also arrive in fragments, so append rather than replace.
        if fragment.name is not None:
            slot.name += fragment.name
        if fragment.arguments_fragment is not None:
            slot.arguments += fragment.arguments_fragment

    def _slot_for(self, fragment: _ToolCallFragment) -> int:
        if fragment.index is not None:
            return fragment.index
        if fragment.id is not None:
            known = self._index_by_id.get(fragment.id)
            if known is not None:
                return known
            return len(self._slots)
        # No index and no id: the only sane reading is "the call in flight".
        return 0 if not self._slots else len(self._slots) - 1

    def to_tool_calls(self) -> list[ToolCall]:
        """Materializes the calls in index order, synthesizing missing ids.

        A slot that never received a function name is dropped: it names nothing
        that could be executed, and forwarding it would only make the tool loop
        report a call to `""`.
        """
        return [
            ToolCall(
                id=slot.id if slot.id is not None else f"call_{slot.index}",
                name=slot.name,
                arguments=slot.arguments,
            )
            for slot in sorted(self._slots.values(), key=lambda slot: slot.index)
            if slot.name
        ]


def _tool_call_chars(calls: Sequence[ToolCall]) -> int:
    """Characters a tool call contributes when tokens have to be estimated."""
    return sum(len(call.name) + len(call.arguments) for call in calls)


# ---------------------------------------------------------------------------
# Stream consumption
# ---------------------------------------------------------------------------


def _now_ms() -> float:
    """A monotonic clock in milliseconds — never wall time, so an NTP step
    mid-generation cannot produce a negative TTFT."""
    return monotonic() * 1000


class _StreamAccumulator:
    """The mutable half of :func:`consume_chat_completion_stream`."""

    def __init__(
        self, started_at: float, now: Callable[[], float], on_delta: DeltaCallback | None
    ) -> None:
        self._started_at = started_at
        self._now = now
        self._on_delta = on_delta
        self.text = ""
        self.reasoning_text = ""
        self.ttft_ms: float | None = None
        self.ttft_content_ms: float | None = None
        self.usage: _Usage | None = None
        self.finish_reason: str | None = None
        self.done = False
        self._tool_calls = _ToolCallAccumulator()

    def _mark_first_output(self) -> None:
        if self.ttft_ms is None:
            self.ttft_ms = self._now() - self._started_at

    def _mark_first_content(self) -> None:
        if self.ttft_content_ms is None:
            self.ttft_content_ms = self._now() - self._started_at

    def _append_content(self, content: str) -> str | None:
        """Appends a content delta, dropping whitespace ahead of the answer.

        A reasoning parser leaves the chat template's post-`</think>` newline
        pair at the head of `content`; a rubric demanding raw JSON fails on it.
        Only dropped while nothing has been appended yet, so whitespace *inside*
        the answer is untouched. Returns what was appended, None if nothing was.
        """
        if not self.text:
            content = content.lstrip()
            if not content:
                return None
        self.text += content
        return content

    def feed(self, payload: str) -> None:
        """Processes one `data:` payload; sets `done` on the sentinel."""
        if payload == DONE_SENTINEL:
            self.done = True
            return
        if not payload:
            return

        try:
            parsed = json.loads(payload)
        except ValueError:
            # Ignore keep-alives and anything that is not JSON — a malformed
            # line should not throw away an otherwise good response.
            return

        streamed_error = _read_streamed_error(parsed)
        if streamed_error is not None:
            raise LlmError(streamed_error, "stream")

        # Thinking is generation, so TTFT stamps here — before anything visible.
        reasoning = _read_delta_reasoning(parsed)
        if reasoning is not None:
            self._mark_first_output()
            self.reasoning_text += reasoning

        content = _read_delta_content(parsed)
        if content is not None:
            self._mark_first_output()
            appended = self._append_content(content)
            if appended is not None:
                # Not the template's newline pair: the answer really starting.
                self._mark_first_content()
                if self._on_delta is not None:
                    self._on_delta(appended, self.text)

        # A tool-call-only response never streams content, so the first
        # fragment of a call is what TTFT has to measure in that case.
        for fragment in _read_delta_tool_calls(parsed):
            self._mark_first_output()
            self._tool_calls.add(fragment)

        # Usage may arrive on a final choices-less chunk (vLLM, LM Studio with
        # stream_options) or piggybacked on the last content chunk (Ollama).
        usage = _read_usage(parsed)
        if usage is not None:
            self.usage = usage
        finish_reason = _read_finish_reason(parsed)
        if finish_reason is not None:
            self.finish_reason = finish_reason

    def result(self, duration_ms: float) -> LlmResult:
        reported_completion = self.usage.completion_tokens if self.usage else None
        tokens_estimated = reported_completion is None
        calls = self._tool_calls.to_tool_calls()

        return LlmResult(
            text=self.text,
            ttft_ms=None if self.ttft_ms is None else round(self.ttft_ms),
            duration_ms=round(duration_ms),
            prompt_tokens=self.usage.prompt_tokens if self.usage else None,
            completion_tokens=(
                # Reasoning counts: it was generated inside the window the rate
                # divides by.
                math.ceil(
                    (len(self.text) + len(self.reasoning_text) + _tool_call_chars(calls)) / 4
                )
                if tokens_estimated
                else reported_completion
            ),
            tokens_estimated=tokens_estimated,
            tool_calls=calls,
            finish_reason=self.finish_reason,
            reasoning_text=self.reasoning_text,
            reasoning_tokens=self.usage.reasoning_tokens if self.usage else None,
            ttft_content_ms=(
                None if self.ttft_content_ms is None else round(self.ttft_content_ms)
            ),
        )


async def _as_async(chunks: AsyncIterable[str] | Iterable[str]) -> AsyncIterable[str]:
    if isinstance(chunks, AsyncIterable):
        async for chunk in chunks:
            yield chunk
    else:
        for chunk in chunks:
            yield chunk


async def consume_chat_completion_stream(
    chunks: AsyncIterable[str] | Iterable[str],
    *,
    started_at: float,
    now: Callable[[], float] | None = None,
    on_delta: DeltaCallback | None = None,
) -> LlmResult:
    """Consumes an OpenAI-compatible chat-completions SSE stream and measures it.

    Deliberately decoupled from the HTTP client so tests can feed recorded
    chunk sequences (vLLM / Ollama / LM Studio all differ slightly in where
    they put the usage block, and some never send one at all) together with a
    fake `now`, which is what makes the timing assertions deterministic.

    `started_at` and `now` share whatever unit the caller picked; production
    passes milliseconds from :func:`_now_ms`.
    """
    clock = now if now is not None else _now_ms
    state = _StreamAccumulator(started_at, clock, on_delta)
    buffer = ""

    async for chunk in _as_async(chunks):
        events, buffer = parse_sse_chunk(buffer, chunk)
        for event in events:
            state.feed(event)
            if state.done:
                break
        if state.done:
            break

    if not state.done and buffer:
        # Stream ended without a trailing newline — flush whatever is left.
        events, _ = parse_sse_chunk(buffer, "\n")
        for event in events:
            state.feed(event)
            if state.done:
                break

    return state.result(clock() - started_at)


# ---------------------------------------------------------------------------
# Metrics math
# ---------------------------------------------------------------------------


def compute_tokens_per_sec(
    completion_tokens: float | None,
    duration_ms: float | None,
    ttft_ms: float | None,
) -> float | None:
    """Generation throughput: completion tokens over the *generation* window.

    The window is the total duration minus the time-to-first-token prefill —
    an endpoint that spends two seconds on prefill and then generates fast is
    fast, and dividing by the total would report it as slow. Returns None
    whenever the numbers cannot produce a meaningful rate (no tokens, no
    duration, or a prefill at least as long as the whole request).

    `ttft_ms` therefore has to be the first output of *any* kind. Pass one
    measured at the first visible token and a reasoning model's thinking phase
    lands in the prefill, collapsing the window to milliseconds while the
    numerator still counts every reasoning token — a stored 3958 tok/s for a
    27-character answer.

    The plausibility guard is the backstop for the next provider that streams its
    thinking on a field name nobody here has seen yet: a five-digit rate out of a
    sub-quarter-second window is a mismeasured prefill, and None is the honest
    reading. Both halves are needed — a fast short answer has a tiny window at an
    ordinary rate, a batched server a high rate over a long window.
    """
    if completion_tokens is None or not math.isfinite(completion_tokens):
        return None
    if completion_tokens <= 0:
        return None
    if duration_ms is None or not math.isfinite(duration_ms):
        return None

    prefill = ttft_ms if ttft_ms is not None and math.isfinite(ttft_ms) else 0
    generation_ms = duration_ms - prefill
    if not math.isfinite(generation_ms) or generation_ms <= 0:
        return None

    rate = completion_tokens / (generation_ms / 1000)
    if not math.isfinite(rate):
        return None
    if generation_ms < MIN_PLAUSIBLE_GENERATION_MS and rate > IMPLAUSIBLE_RATE:
        return None
    return rate


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------


def _classify(exc: Exception) -> LlmError:
    """Maps an httpx failure onto the kind the executor grades runs on.

    httpx raises a distinct exception type per failure mode, so this is a
    straight lookup. `describe_transport_error` is the shared message mapping
    (see `app.services.discovery`).
    """
    if isinstance(exc, httpx.TimeoutException):
        return LlmError(describe_transport_error(exc), "timeout")
    if isinstance(exc, httpx.TransportError):
        return LlmError(describe_transport_error(exc), "connection")
    if isinstance(exc, httpx.InvalidURL):
        return LlmError(f"Invalid base URL: {exc}", "connection")
    return LlmError(str(exc) or "The stream failed.", "stream")


def _excerpt(body: str, limit: int = 400) -> str:
    """A one-line, bounded quote of an error body, for the result's message."""
    trimmed = " ".join(body.split())
    if not trimmed:
        return "(empty response body)"
    return f"{trimmed[:limit]}…" if len(trimmed) > limit else trimmed


async def stream_chat(
    base_url: str,
    api_key: str | None,
    model: str,
    messages: Sequence[ChatMessage],
    *,
    tools: Sequence[Mapping[str, Any]] | None = None,
    tool_choice: ToolChoice | None = None,
    params: Mapping[str, Any] | None = None,
    timeout: float = DEFAULT_TIMEOUT_S,
    on_delta: DeltaCallback | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> LlmResult:
    """Streams one completion from an OpenAI-compatible endpoint.

    `base_url` is the endpoint's stored base URL including its `/v1` (the same
    value `app.services.discovery` appends `/models` to); `/chat/completions`
    is appended here.

    Credentials are passed in rather than looked up: an endpoint's `base_url` and
    `api_key` are read live at execution time, never frozen into a run, so this
    function never touches the database.

    `params` is merged **last** so a test case's temperature/max_tokens can
    override anything above it. Cancellation is plain asyncio cancellation and
    propagates untouched, so the executor can reset an in-flight row to
    `pending` when the client disconnects.

    `transport` is a test seam only (`httpx.MockTransport`).

    Raises :class:`LlmError` for everything that produced no result; only
    `is_connection_level` ones mean the endpoint was never reachable.
    """
    url = f"{base_url.strip().rstrip('/')}/chat/completions"

    headers = {
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    body: dict[str, Any] = {
        "model": model,
        "messages": list(messages),
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    # Sending an empty `tools` array makes some servers unhappy, and
    # `tool_choice` is meaningless without it — omit both unless asked.
    if tools:
        body["tools"] = list(tools)
        if tool_choice:
            body["tool_choice"] = tool_choice
    if params:
        body.update(params)

    started_at = _now_ms()
    try:
        async with asyncio.timeout(timeout):
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(timeout), transport=transport
            ) as client:
                try:
                    async with client.stream("POST", url, json=body, headers=headers) as response:
                        if response.status_code >= 400:
                            await response.aread()
                            hint = (
                                " (unauthorized — check the API key)"
                                if response.status_code in (401, 403)
                                else ""
                            )
                            raise LlmError(
                                f"HTTP {response.status_code} {response.reason_phrase}{hint}"
                                f" — {_excerpt(response.text)}",
                                "http",
                                response.status_code,
                            )

                        return await consume_chat_completion_stream(
                            response.aiter_text(), started_at=started_at, on_delta=on_delta
                        )
                except (httpx.HTTPError, httpx.InvalidURL) as exc:
                    raise _classify(exc) from exc
    except TimeoutError as exc:
        # `asyncio.timeout` converts only *its own* deadline into this; an
        # outer cancellation still surfaces as CancelledError.
        raise LlmError("Request timed out.", "timeout") from exc
