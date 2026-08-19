"""Executing a run: every still-pending row, one after the other.

Four invariants make an interrupted run recoverable rather than corrupt:

* **One execution per run**, across processes — a Postgres advisory lock
  (:mod:`app.services.run_lock`) held on its own connection for the whole run.
* **Every row is persisted the moment it finishes.** A crash, a closed tab or a
  dead endpoint never loses completed work: the run simply keeps its remaining
  `pending` rows and Resume finishes it.
* **A row error is a row error.** The loop marks it `error` and continues; only
  a run whose every attempt died at connection level ends `failed`, which is
  what keeps that status meaning "the endpoint was never reachable".
* **An interrupted row goes back to `pending`**, giving up everything it had
  half-written — including rows left `running` by a process that crashed, which
  are reclaimed at the start of the next execution (safe precisely because the
  lock above is held by then, so no other execution of this run can be live).

Two things are read **live** rather than from the run's snapshot, both
credentials: the endpoint's `base_url`/`api_key` and an MCP toolset's URL and
headers. A moved endpoint must not break Resume. The frozen half — text, tool
definitions, a manual tool's canned response — travels with the run.

A `documents` toolset's markdown is a third live read and the one that is *not*
a credential: the three document tools query the corpus per call, through the
scoped repository, on the session this module already holds for the run's
duration. That is a real limitation rather than a preference — this version
freezes no corpus, only `document_count` and `corpus_updated_at` beside each
definition, so that a later one can say the corpus moved under a run.

This is also where the two messages are **assembled**
(`app.services.message_assembly`): the run's three frozen texts become a system
message and a user message here, at dispatch, rather than at run creation. The
frozen columns therefore keep the task prompt and the case's own data as
separate, comparable things, which is what `/results` reports drift from — the
transcript only ever records the assembled strings.

Cancellation is an explicit :class:`asyncio.Event` rather than task
cancellation, and that is deliberate: Starlette cancels a streaming response's
task group when the client disconnects, and inside a cancelled scope every
further `await` raises immediately — which is exactly when this module still
has to write the in-flight row back to `pending`. So the route runs the
executor as a *detached* task (:func:`run_in_background`) and merely sets the
flag; the executor notices, rolls back cleanly, and releases the lock.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Coroutine
from contextlib import suppress
from dataclasses import dataclass
from time import monotonic
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db import async_session
from app.models.runs import Run, RunStatus
from app.repos.documents import (
    document_summary,
    get_document_by_path,
    list_documents,
    search_documents,
)
from app.repos.endpoints import get_endpoint
from app.repos.runs import (
    count_pending_results,
    get_run_result,
    list_result_statuses,
    reset_results_in_status,
    scope_for_run,
    update_run_result,
    update_run_status,
)
from app.repos.scoped import utc_now
from app.repos.toolsets import list_mcp_servers
from app.scope import Scope
from app.services.documents import (
    LIST_DOCUMENTS,
    READ_DOCUMENT,
    SEARCH_DOCUMENTS,
    list_documents_payload,
    read_document_payload,
    search_documents_payload,
    unknown_path_message,
    unknown_tool_message,
    window_document,
)
from app.services.llm import LlmError, ToolCall, stream_chat
from app.services.mcp_client import call_mcp_tool
from app.services.message_assembly import assert_user_message, system_message, user_message
from app.services.params import strip_reserved
from app.services.run_events import (
    Aborted,
    Delta,
    EmitRunEvent,
    ResultDone,
    ResultError,
    ResultMetrics,
    ResultStart,
    RunDone,
    RunStart,
    ToolCallEvent,
    ToolResultEvent,
    TurnStart,
)
from app.services.run_lock import acquire_run_lock
from app.services.tool_loop import (
    ChatStreamer,
    SnapshotTool,
    ToolExecutionOutcome,
    ToolExecutor,
    error_payload,
    parse_tool_arguments,
    parse_tools_snapshot,
    run_tool_loop,
)

#: How often, at most, a `delta` event is pushed to the client. Streaming every
#: token would spend more time serializing progress than generating it.
DELTA_THROTTLE_MS = 250


class RunAlreadyExecutingError(Exception):
    """Someone else holds this run's lock. The route turns it into a 409."""

    def __init__(self, run_id: int) -> None:
        super().__init__(f"Run {run_id} is already executing.")
        self.run_id = run_id


class RunNotExecutableError(Exception):
    """The run cannot be executed at all — it is gone, or has no endpoint."""


#: Everything a half-written result must give up when it goes back to
#: `pending`, kept in one place so a new output column cannot be forgotten in
#: one of the three rollback paths (stale-`running` reclaim, pre-attempt reset,
#: abort).
RESET_TO_PENDING: dict[str, Any] = {
    "status": "pending",
    "started_at": None,
    "finished_at": None,
    "response_text": None,
    "error": None,
    "transcript_json": None,
    "turns_json": None,
    "turn_count": None,
    "tool_call_count": None,
    "stopped_reason": None,
}


@dataclass(frozen=True)
class _Endpoint:
    """Where this run's completions actually go."""

    base_url: str
    api_key: str | None


# ---------------------------------------------------------------------------
# Detached execution
# ---------------------------------------------------------------------------

#: Strong references to running executions. `asyncio` only holds a weak one, so
#: a detached task can otherwise be garbage-collected mid-run.
_BACKGROUND: set[asyncio.Task[Any]] = set()


def run_in_background(coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
    """Starts a coroutine outside the caller's cancellation scope.

    Used by the execute route (whose streaming body is cancelled the moment the
    client disconnects, while the executor still has a row to roll back) and by
    the MCP server's fire-and-forget `execute_run`, which returns long before a
    dozen test cases have run.
    """
    task = asyncio.ensure_future(coro)
    _BACKGROUND.add(task)
    task.add_done_callback(_BACKGROUND.discard)
    return task


async def _unless_cancelled[T](
    awaitable: Awaitable[T], cancelled: asyncio.Event | None
) -> T | None:
    """Awaits `awaitable`, giving up as soon as `cancelled` is set.

    Returns `None` for "the client hung up" — unambiguous here because every
    caller awaits something that returns a value. The in-flight work is
    cancelled rather than left running, so a disconnect really does stop the
    completion instead of merely stopping the *next* one.
    """
    if cancelled is None:
        return await awaitable

    work = asyncio.ensure_future(awaitable)
    watch = asyncio.ensure_future(cancelled.wait())
    try:
        done, _ = await asyncio.wait({work, watch}, return_when=asyncio.FIRST_COMPLETED)
    except asyncio.CancelledError:
        work.cancel()
        raise
    finally:
        watch.cancel()

    if work in done:
        return work.result()

    work.cancel()
    with suppress(asyncio.CancelledError):
        await work
    return None


# ---------------------------------------------------------------------------
# Live lookups
# ---------------------------------------------------------------------------


def _parse_snapshot_base_url(snapshot: str) -> str | None:
    try:
        parsed = json.loads(snapshot)
    except ValueError:
        return None
    if not isinstance(parsed, dict):
        return None
    value = parsed.get("base_url")
    return value if isinstance(value, str) and value else None


def _parse_params(raw: str | None) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(parsed, dict):
        return None
    # The frozen column is validated on the way in, so this only ever fires on
    # a run frozen before that rule existed or edited in the database directly.
    # Last chance to keep a stored parameter from replacing the run's own
    # messages or tools.
    return strip_reserved(parsed)


def _error_message(exc: BaseException) -> str:
    return str(exc) or "Unknown error."


async def _build_tool_executor(
    scope: Scope, session: AsyncSession, snapshot: list[SnapshotTool]
) -> ToolExecutor | None:
    """Builds the executor for everything a result cannot answer from its
    snapshot, or None when it has no such tool.

    Two sources need this layer, and they need opposite things from it, which is
    why this is a dispatcher over two closures rather than one closure with a
    branch in it:

    * **MCP** needs credentials the snapshot deliberately does not hold, looked
      up once here, up front.
    * **documents** needs a scoped query per call, because the corpus is not
      frozen into the run (see `app.services.tool_loop`'s module docstring).

    Routing is on `entry.source`, never on the tool's name: `list_documents` is
    a name a manual toolset is free to use for something else, and the frozen
    entry is the only thing that knows which it was. A name the snapshot does
    not contain never reaches here — the loop answers that itself — so a miss
    below means the snapshot named a source this build cannot execute, and the
    model is told so rather than the row dying.
    """
    source_by_name = {tool.name: tool.source for tool in snapshot}
    mcp = await _build_mcp_executor(scope, session, snapshot)
    documents = _build_documents_executor(scope, session, snapshot)
    if mcp is None and documents is None:
        return None

    async def execute(call: ToolCall) -> ToolExecutionOutcome:
        chosen = documents if source_by_name.get(call.name) == "documents" else mcp
        if chosen is None:
            return ToolExecutionOutcome(
                error_payload(f'"{call.name}" cannot be executed in this run.'), True
            )
        return await chosen(call)

    return execute


async def _build_mcp_executor(
    scope: Scope, session: AsyncSession, snapshot: list[SnapshotTool]
) -> ToolExecutor | None:
    """Builds the executor for a result's MCP tools, or None when it has none.

    The endpoint and its auth are read live here rather than taken from the
    frozen snapshot — the same tradeoff an endpoint's `base_url` already makes.
    Every server is looked up once, up front, so the closure never touches the
    session while a completion is in flight.
    """
    toolset_ids = list(
        dict.fromkeys(tool.toolset_id for tool in snapshot if tool.source == "mcp")
    )
    if not toolset_ids:
        return None

    servers = {
        row.id: row
        for row in await list_mcp_servers(scope, session, toolset_ids)
        if row.mcp_url
    }
    toolset_id_by_name = {tool.name: tool.toolset_id for tool in snapshot}

    async def execute(call: ToolCall) -> ToolExecutionOutcome:
        toolset_id = toolset_id_by_name.get(call.name)
        server = None if toolset_id is None else servers.get(toolset_id)
        if server is None or server.mcp_url is None:
            # The toolset was deleted, switched to manual, or lost its URL
            # after the run was created. The model hears about it and reacts.
            return ToolExecutionOutcome(
                error_payload(f'The MCP server for "{call.name}" is no longer configured.'),
                True,
            )

        # The loop already validated the arguments; parse again for the typed
        # object the MCP call needs.
        parsed = parse_tool_arguments(call.arguments)
        outcome = await call_mcp_tool(
            server.mcp_url, server.mcp_headers, call.name, parsed.value or {}
        )
        return ToolExecutionOutcome(outcome.content, outcome.is_error)

    return execute


def _build_documents_executor(
    scope: Scope, session: AsyncSession, snapshot: list[SnapshotTool]
) -> ToolExecutor | None:
    """Builds the executor for a result's document tools, or None when it has
    none.

    Unlike the MCP closure above, this one queries per call: the corpus is not
    frozen into the run, so `search_documents` and `read_document` have to see
    what the documents actually say now. That means touching the session between
    model turns, which is safe because the loop is strictly sequential — the
    executor awaits `run_tool_loop`, which awaits each tool call in turn, so no
    other statement is ever in flight on this session.

    **The corpus a call can reach is fixed by the frozen `toolset_id` plus this
    run's scope, and that is the whole of the security story.** A `path` the model
    invents (`../../etc/passwd`, another customer's handbook) is one more value in
    a `WHERE` clause that matches nothing: there is no filesystem read here and
    therefore no traversal to sanitize. The scope comes from the run row
    (`scope_from_row`), so a borrowed global corpus stays readable — a visible
    read, never an owned one, which is `app.repos.documents`' distinction and not
    this closure's to make.

    A missing path, an offset past the end, an empty corpus and a query that
    matches nothing are all answers the model reads and recovers from — which is
    exactly the retrieval behaviour this workload exists to measure, and the app's
    standing rule that a tool failure is data.

    **Querying per call is also why this closure owns a rollback.** It is the only
    tool executor that runs a statement on the executor's *own* session, and a
    statement Postgres refuses leaves that session's transaction aborted — after
    which every later statement on it raises `InFailedSQLTransactionError`. The
    row's own `ok` write happens *after* `run_tool_loop` returns and therefore
    outside the per-row handler, so a poisoned session would fail that write, fail
    the remaining rows with it and leave the run stuck `running`: one tool argument
    costing the whole suite, precisely the blast radius the per-row handler exists
    to prevent. Every call therefore runs inside a savepoint — see `execute` below
    for why a savepoint and not a rollback — and the model reads what happened as
    an ordinary tool error.
    """
    toolset_id_by_name = {
        tool.name: tool.toolset_id for tool in snapshot if tool.source == "documents"
    }
    if not toolset_id_by_name:
        return None

    async def paths(toolset_id: int) -> list[str]:
        """Every path in the corpus, for a bad-path message worth reading."""
        metas = await list_documents(scope, session, toolset_ids=[toolset_id])
        return [meta.path for meta in metas]

    async def answer(call: ToolCall) -> ToolExecutionOutcome:
        toolset_id = toolset_id_by_name[call.name]
        # The loop already validated the arguments; parse again for the values.
        arguments = parse_tool_arguments(call.arguments).value or {}

        if call.name == LIST_DOCUMENTS:
            metas = await list_documents(scope, session, toolset_ids=[toolset_id])
            return _json_outcome(
                list_documents_payload([meta.summary() for meta in metas])
            )

        if call.name == SEARCH_DOCUMENTS:
            query = _text_argument(arguments.get("query"))
            matches = await search_documents(
                scope,
                session,
                toolset_id,
                query=query,
                limit=_int_argument(arguments.get("limit")),
            )
            return _json_outcome(search_documents_payload(query, matches))

        if call.name == READ_DOCUMENT:
            # Verbatim, deliberately: `get_document_by_path` matches exactly, so
            # the path the model quoted back from a listing is the path it gets
            # and anything else is an honest miss it is told about. Trimming here
            # would be normalisation by the back door.
            path = _text_argument(arguments.get("path"))
            document = await get_document_by_path(scope, session, toolset_id, path)
            if document is None:
                return ToolExecutionOutcome(
                    error_payload(unknown_path_message(path, await paths(toolset_id))),
                    True,
                )
            window = window_document(
                document.content,
                _int_argument(arguments.get("offset")),
                _int_argument(arguments.get("limit")),
            )
            return _json_outcome(
                read_document_payload(document_summary(document), window)
            )

        # A run frozen by a build that offered a fourth document tool. The
        # snapshot survives; the model is told which tools this corpus has.
        return ToolExecutionOutcome(error_payload(unknown_tool_message(call.name)), True)

    async def execute(call: ToolCall) -> ToolExecutionOutcome:
        # A SAVEPOINT, not a plain `rollback()`, and the difference is the whole
        # reason this wrapper is subtle. A refused statement aborts the session's
        # transaction, and `session.rollback()` does clear that — but it also
        # **expires every instance in the identity map**, so the next row's
        # attribute read becomes lazy IO in a context that cannot await it
        # (`greenlet_spawn has not been called`). The suite would keep running and
        # fail every row after the first. Rolling back to a savepoint clears the
        # aborted state and leaves the identity map alone, which is the same
        # nested-safety `app.repos.scoped.transaction` already relies on.
        try:
            async with session.begin_nested():
                return await answer(call)
        except Exception as exc:  # noqa: BLE001 - a tool failure is data, not a crash
            # Deliberately not `BaseException`: a `CancelledError` has to keep
            # propagating so the executor can reset the in-flight row to
            # `pending`, exactly as `tool_loop.execute_one` argues.
            #
            # The reachable case is an argument the *model* chose. A `query` or
            # `path` carrying a NUL byte cannot be a bind parameter at all —
            # asyncpg raises `CharacterNotInRepertoireError` — and no corpus row
            # could hold one either, since `normalize_markdown` and
            # `clean_document_path` refuse it at every write door. So the call was
            # always going to be a miss; this is what keeps it a miss for *this
            # row* instead of for every row after it.
            return ToolExecutionOutcome(
                error_payload(f"The corpus could not be queried: {exc}"), True
            )

    return execute


def _json_outcome(payload: dict[str, Any]) -> ToolExecutionOutcome:
    """A successful document tool answer, as the tool message the model reads.

    `ensure_ascii=False` because these corpora are frequently German: escaping
    every umlaut would hand the model `R\\u00fcckgabe` to read and pay tokens for,
    where the request body is UTF-8 either way.
    """
    return ToolExecutionOutcome(json.dumps(payload, ensure_ascii=False), False)


def _text_argument(value: Any) -> str:
    """A string argument, or `""` for a model that sent something else.

    Empty rather than refused: a blank `path` falls through to the bad-path
    message, which lists what does exist, and a blank `query` to the no-match
    note that suggests `list_documents`. Both are a usable next step, where a
    complaint about JSON types is a turn spent on nothing.
    """
    return value if isinstance(value, str) else ""


def _int_argument(value: Any) -> int | None:
    """An integer argument, or None to let the pure default apply.

    A numeric string counts, because models emit `"limit": "10"` often enough
    that refusing it would measure their JSON habits rather than their retrieval.
    `True` is not a number. Out-of-range values are *not* corrected here —
    `app.services.documents` clamps every one of them, in one place, next to the
    ceilings it clamps to.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------


async def execute_run(
    run_id: int,
    emit: EmitRunEvent,
    cancelled: asyncio.Event | None = None,
    *,
    stream: ChatStreamer = stream_chat,
) -> None:
    """Executes every still-pending result of a run, sequentially.

    Raises :class:`RunAlreadyExecutingError` when another process holds the
    lock, and :class:`RunNotExecutableError` when the run is gone or has no
    endpoint left to talk to. Everything narrower than that is reported through
    `emit` and persisted, never raised.

    `stream` is a test seam (see :class:`app.services.tool_loop.ChatStreamer`)
    — production callers get the real SSE client.
    """
    lock = await acquire_run_lock(run_id)
    if lock is None:
        raise RunAlreadyExecutingError(run_id)

    try:
        # Its own session: the executor outlives the request that started it
        # (and under MCP there is no request at all), so it can neither borrow
        # nor keep the caller's.
        async with async_session() as session:
            await _execute(run_id, emit, cancelled, session, stream)
    finally:
        await lock.release()


async def _execute(
    run_id: int,
    emit: EmitRunEvent,
    cancelled: asyncio.Event | None,
    session: AsyncSession,
    stream: ChatStreamer,
) -> None:
    # The executor runs outside any request, so its scope comes from the run
    # row itself — the one deliberately unscoped lookup in the repositories.
    found = await scope_for_run(session, run_id)
    if found is None:
        raise RunNotExecutableError(f"Run {run_id} not found.")
    scope, run = found

    # Rows stuck in `running` are leftovers from a crashed process. The lock is
    # held by now, so no other execution of this run can be live: reclaim them
    # and let this execution redo them.
    await reset_results_in_status(scope, session, run_id, "running", RESET_TO_PENDING)
    await session.commit()

    all_results = await list_result_statuses(scope, session, run_id)
    total = len(all_results)
    pending_ids = [row.id for row in all_results if row.status == "pending"]
    index_by_id = {row.id: index + 1 for index, row in enumerate(all_results)}

    emit(RunStart(run_id=run_id, pending=len(pending_ids), total=total))

    if not pending_ids:
        emit(RunDone(run_id=run_id, status=run.status, nothing_pending=True))
        return

    endpoint = await _resolve_endpoint(scope, session, run)
    params = _parse_params(run.params)

    await update_run_status(
        scope,
        session,
        run_id,
        status="running",
        started_at=run.started_at or utc_now(),
        finished_at=None,
    )
    await session.commit()

    aborted = False
    succeeded = 0
    attempted = 0
    connection_errors = 0

    for result_id in pending_ids:
        if cancelled is not None and cancelled.is_set():
            aborted = True
            emit(Aborted(result_id=None))
            break

        result = await get_run_result(scope, session, result_id)
        if result is None or result.status != "pending":
            continue

        await update_run_result(
            scope,
            session,
            run_id,
            result_id,
            {**RESET_TO_PENDING, "status": "running", "started_at": utc_now()},
        )
        await session.commit()

        emit(
            ResultStart(
                result_id=result_id, index=index_by_id.get(result_id, 0), total=total
            )
        )

        snapshot = parse_tools_snapshot(result.tools_snapshot)
        tool_run = bool(snapshot) and result.tool_mode != "none"
        execute_tool = (
            await _build_tool_executor(scope, session, snapshot)
            if result.tool_mode == "execute"
            else None
        )

        last_delta_at = 0.0
        started_at = monotonic()
        attempted += 1

        def on_turn_start(
            turn: int, *, result_id: int = result_id, tool_run: bool = tool_run
        ) -> None:
            nonlocal last_delta_at
            if not tool_run:
                return
            # Each turn streams its own text, so the throttle window restarts
            # with it — otherwise a turn beginning right after a delta would
            # stay silent until the window expired.
            last_delta_at = 0.0
            emit(TurnStart(result_id=result_id, turn=turn))

        def on_delta(
            turn: int,
            text_so_far: str,
            *,
            result_id: int = result_id,
            tool_run: bool = tool_run,
        ) -> None:
            nonlocal last_delta_at
            now = monotonic() * 1000
            if now - last_delta_at < DELTA_THROTTLE_MS:
                return
            last_delta_at = now
            # A plain prompt emits exactly the event shape it always did.
            emit(
                Delta(
                    result_id=result_id,
                    text=text_so_far,
                    turn=turn if tool_run else None,
                )
            )

        try:
            # Assembly happens here, from the three frozen columns, not at run
            # creation — which is what keeps the parts separately recoverable
            # for drift reporting. A row can only arrive with both halves of the
            # user message blank if a prompt was deleted after the two guards
            # ran (`SET NULL` on `task_prompt_id`), so refuse it here rather
            # than let an empty user turn reach the provider and come back as a
            # 400 nobody can read.
            assert_user_message(
                result.task_prompt_text,
                result.test_case_text,
                subject=f'Test case "{result.test_case_title}"',
            )
            outcome = await _unless_cancelled(
                run_tool_loop(
                    base_url=endpoint.base_url,
                    api_key=endpoint.api_key,
                    model=run.model_id,
                    user_message=user_message(
                        result.task_prompt_text, result.test_case_text
                    ),
                    system_prompt=system_message(result.system_prompt_text),
                    params=params,
                    snapshot=snapshot,
                    tool_mode=result.tool_mode,
                    tool_choice=result.tool_choice,
                    max_turns=result.max_turns,
                    execute_tool=execute_tool,
                    on_turn_start=on_turn_start,
                    on_delta=on_delta,
                    on_tool_calls=(
                        lambda turn, calls, result_id=result_id: emit(
                            ToolCallEvent(result_id=result_id, turn=turn, calls=calls)
                        )
                    ),
                    on_tool_result=(
                        lambda turn, message, result_id=result_id: emit(
                            ToolResultEvent(result_id=result_id, turn=turn, message=message)
                        )
                    ),
                    stream=stream,
                ),
                cancelled,
            )
        except Exception as exc:  # noqa: BLE001 - one bad row never stops the run
            # Deliberately not `BaseException`: a `CancelledError` still has to
            # propagate, so a shutting-down process does not mark rows `error`.
            if isinstance(exc, LlmError) and exc.is_connection_level:
                connection_errors += 1

            message = _error_message(exc)
            await update_run_result(
                scope,
                session,
                run_id,
                result_id,
                {
                    "status": "error",
                    "error": message,
                    "duration_ms": round((monotonic() - started_at) * 1000),
                    "finished_at": utc_now(),
                },
            )
            await session.commit()
            emit(ResultError(result_id=result_id, error=message))
            continue

        if outcome is None:
            # The client hung up. Roll the row back to `pending` so Resume
            # picks it up again instead of leaving it half-written.
            await update_run_result(scope, session, run_id, result_id, RESET_TO_PENDING)
            await session.commit()
            attempted -= 1
            aborted = True
            emit(Aborted(result_id=result_id))
            break

        await update_run_result(
            scope,
            session,
            run_id,
            result_id,
            {
                "status": "ok",
                "response_text": outcome.text,
                "error": None,
                "duration_ms": outcome.duration_ms,
                "ttft_ms": outcome.ttft_ms,
                "prompt_tokens": outcome.prompt_tokens,
                "completion_tokens": outcome.completion_tokens,
                "tokens_per_sec": outcome.tokens_per_sec,
                "tokens_estimated": outcome.tokens_estimated,
                # Only tool runs carry transcript detail; a plain prompt keeps
                # these null so its card renders exactly as it always did.
                "transcript_json": outcome.transcript_json if tool_run else None,
                "turns_json": outcome.turns_json if tool_run else None,
                "turn_count": outcome.turn_count if tool_run else None,
                "tool_call_count": outcome.tool_call_count if tool_run else None,
                "stopped_reason": outcome.stopped_reason if tool_run else None,
                "finished_at": utc_now(),
            },
        )
        await session.commit()

        succeeded += 1
        emit(
            ResultDone(
                result_id=result_id,
                text=outcome.text,
                metrics=ResultMetrics(
                    duration_ms=outcome.duration_ms,
                    ttft_ms=outcome.ttft_ms,
                    prompt_tokens=outcome.prompt_tokens,
                    completion_tokens=outcome.completion_tokens,
                    tokens_per_sec=outcome.tokens_per_sec,
                    tokens_estimated=outcome.tokens_estimated,
                    turn_count=outcome.turn_count if tool_run else None,
                    tool_call_count=outcome.tool_call_count if tool_run else None,
                ),
                transcript=outcome.transcript if tool_run else None,
                turns=outcome.turns if tool_run else None,
                stopped_reason=outcome.stopped_reason if tool_run else None,
            )
        )

    if aborted:
        await update_run_status(scope, session, run_id, status="pending", finished_at=None)
        await session.commit()
        emit(RunDone(run_id=run_id, status="pending"))
        return

    remaining = await count_pending_results(scope, session, run_id)

    # `failed` is reserved for "the endpoint was never reachable": every result
    # we tried died at connection level and nothing succeeded. A run where the
    # model merely errored on some rows is still a completed run.
    everything_unreachable = (
        succeeded == 0 and attempted > 0 and connection_errors == attempted
    )
    status: RunStatus = (
        "pending" if remaining > 0 else "failed" if everything_unreachable else "completed"
    )

    await update_run_status(
        scope,
        session,
        run_id,
        status=status,
        finished_at=None if status == "pending" else utc_now(),
    )
    await session.commit()

    emit(RunDone(run_id=run_id, status=status))


async def _resolve_endpoint(scope: Scope, session: AsyncSession, run: Run) -> _Endpoint:
    """Where to send this run's completions.

    The endpoint row may have been edited or deleted since the run was created:
    prefer live credentials, fall back to the snapshot's URL (which carries no
    key, deliberately — a snapshot is display data).
    """
    if run.endpoint_id is not None:
        endpoint = await get_endpoint(scope, session, run.endpoint_id)
        if endpoint is not None:
            return _Endpoint(base_url=endpoint.base_url, api_key=endpoint.api_key)

    snapshot_url = _parse_snapshot_base_url(run.endpoint_snapshot)
    if snapshot_url is None:
        raise RunNotExecutableError(
            "The endpoint for this run no longer exists and its snapshot has no base URL."
        )
    return _Endpoint(base_url=snapshot_url, api_key=None)


__all__ = [
    "DELTA_THROTTLE_MS",
    "RESET_TO_PENDING",
    "RunAlreadyExecutingError",
    "RunNotExecutableError",
    "execute_run",
    "run_in_background",
]
