"""`/api/runs` — creating, listing and *executing* a run, plus `/api/results`.

Two routers live here because they are one subject: a result row only exists
inside a run, and the one thing you do to it directly — rate it — is the whole
point of having run it.

The interesting endpoint is `POST /runs/{id}/execute`, which streams NDJSON
(`app.services.run_events`). Three things about it are deliberate:

* **The guards run as dependencies**, so a refusal is a plain JSON body with a
  `message` and never a truncated NDJSON stream. Same reason the old route
  called `guardRequest` before constructing its `ReadableStream`.
* **Nothing pending is not an error.** The response is a single `runDone` line
  with `nothing_pending`, so the client's stream reader handles it like any
  other outcome instead of branching on a status code.
* **The executor runs detached** (`run_in_background`) with the events crossing
  into the response body through a queue. Starlette cancels the streaming body
  the moment the client disconnects, and the executor's whole job at that point
  is to write the in-flight row back to `pending` — which it could not do from
  inside a cancelled scope. It is handed an `asyncio.Event` instead and rolls
  back cleanly after the response is long gone.

Runs are content, not credentials, so every mutation here sits at `Writer`.
Archiving and deleting are refused while a run is executing: the list the user
is looking at would otherwise be lying about it.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query, Response, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import StreamingResponse

from app.auth.guards import CurrentScope, CurrentUser, DbSession, Writer
from app.models import (
    RatedVia,
    Rating,
    ResultStatus,
    Run,
    RunResult,
    RunStatus,
    StoppedReason,
)
from app.models.toolsets import ToolChoice, ToolMode
from app.repos.runs import (
    count_pending_results,
    delete_run,
    get_run,
    get_run_result,
    list_run_results,
    list_runs,
    rate_result,
    set_run_archived_at,
)
from app.repos.scoped import utc_now
from app.scope import CrossCustomerError, Scope
from app.services.executor import execute_run, run_in_background
from app.services.message_assembly import NoUserMessageError
from app.services.run_create import RunCreateError, create_run_record
from app.services.run_events import (
    NDJSON_MEDIA_TYPE,
    RunDone,
    RunError,
    RunEvent,
    serialize_event,
)
from app.services.run_lock import is_run_executing
from app.services.tool_config import ToolConfigError

router = APIRouter(prefix="/runs", tags=["runs"])
results_router = APIRouter(prefix="/results", tags=["runs"])

#: Proxies must not buffer the stream, or progress arrives all at once at the
#: end — which is exactly the information the page exists to show.
NDJSON_HEADERS = {
    "Cache-Control": "no-store, no-transform",
    "X-Accel-Buffering": "no",
}


# --------------------------------------------------------------------------
# Wire shapes
# --------------------------------------------------------------------------


def _json_value(raw: str | None) -> Any:
    """Reads a stored JSON column back for display.

    Degrades to `None` rather than raising: a malformed snapshot must never
    keep a past run from rendering.
    """
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


class RunView(BaseModel):
    """A run as its list row and header show it — snapshots already parsed."""

    id: int
    endpoint_id: int | None
    #: Name, endpoint and hardware notes as they were at creation time.
    endpoint_snapshot: dict[str, Any] | None
    model_id: str
    params: dict[str, Any] | None
    comment: str | None
    group_names: list[str]
    llm_info: dict[str, Any] | None
    status: RunStatus
    archived_at: datetime | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class RunResultView(BaseModel):
    """One result row: its frozen inputs, its outcome, its verdict."""

    id: int
    run_id: int
    test_case_id: int | None
    #: The committed version each slot's draft matched, if any — attribution,
    #: not selection. Null means that slot tested a dirty draft, or is empty.
    #: One per slot: the two prompts are versioned independently.
    system_prompt_version_id: int | None
    task_prompt_version_id: int | None
    sort_order: int

    group_name: str
    test_case_title: str
    #: The test case's own `content` — the data half of the user message,
    #: frozen on its own. The task prompt is the other half; the executor
    #: joins them (`app.services.message_assembly.user_message`).
    test_case_text: str | None
    expected_output: str | None
    #: The system prompt's draft text, verbatim, as it was at run creation.
    system_prompt_text: str | None
    #: The task prompt's draft text, verbatim. Kept apart from
    #: `test_case_text` so `/results` can say *the task prompt changed*
    #: instead of *the user message changed*.
    task_prompt_text: str | None
    tools_snapshot: list[Any] | None
    tool_mode: ToolMode
    tool_choice: ToolChoice | None
    max_turns: int

    status: ResultStatus
    response_text: str | None
    transcript: list[Any] | None
    turns: list[Any] | None
    turn_count: int | None
    tool_call_count: int | None
    stopped_reason: StoppedReason | None
    error: str | None

    duration_ms: int | None
    ttft_ms: int | None
    prompt_tokens: int | None
    completion_tokens: int | None
    tokens_per_sec: float | None
    tokens_estimated: bool

    rating: Rating | None
    rating_note: str | None
    #: Which credential set the verdict — `token` is an agent judging over MCP,
    #: which the UI badges as such. Null on an unrated row, and on one rated
    #: before the column existed.
    rated_via: RatedVia | None
    started_at: datetime | None
    finished_at: datetime | None


class RunDetailView(RunView):
    results: list[RunResultView]


class RunCreateRequest(BaseModel):
    """The new-run form. `group_ids` selects what to run, the rest is how.

    `temperature`/`max_tokens` are spelled out rather than taken as a free
    `params` object: an empty input has to be *omitted* from the request body
    so the endpoint keeps its own default, and a bare passthrough would send
    `null` instead.
    """

    endpoint_id: int
    model_id: str = Field(min_length=1)
    group_ids: list[int] = Field(min_length=1)
    temperature: float | None = None
    max_tokens: int | None = None
    comment: str | None = None

    @field_validator("model_id")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Select or enter a model.")
        return cleaned

    @field_validator("temperature")
    @classmethod
    def _valid_temperature(cls, value: float | None) -> float | None:
        if value is not None and not 0 <= value <= 2:
            raise ValueError("Temperature must be between 0 and 2.")
        return value

    @field_validator("max_tokens")
    @classmethod
    def _valid_max_tokens(cls, value: int | None) -> int | None:
        if value is not None and value < 1:
            raise ValueError("Max tokens must be a positive whole number.")
        return value

    def params(self) -> dict[str, Any] | None:
        params: dict[str, Any] = {}
        if self.temperature is not None:
            params["temperature"] = self.temperature
        if self.max_tokens is not None:
            params["max_tokens"] = self.max_tokens
        return params or None


class RatingRequest(BaseModel):
    """`unrated` is the wire word for "clear it".

    JSON cannot distinguish an absent key from a null one by the time Pydantic
    has coerced the body, so the *word* carries the intent — the same choice
    the old MCP `set_rating` made. `note` follows the opposite rule: omitting
    it leaves an existing note untouched, which is what the UI's rating buttons
    already do, so it is read through `model_fields_set` rather than by value.
    """

    rating: Literal["good", "meh", "bad", "unrated"] | None = None
    note: str | None = None

    @field_validator("note")
    @classmethod
    def _blank_to_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


# --------------------------------------------------------------------------
# View builders / lookups
# --------------------------------------------------------------------------


def _run_view(run: Run) -> RunView:
    group_names = _json_value(run.group_names)
    return RunView(
        id=run.id,
        endpoint_id=run.endpoint_id,
        endpoint_snapshot=_json_value(run.endpoint_snapshot),
        model_id=run.model_id,
        params=_json_value(run.params),
        comment=run.comment,
        group_names=group_names if isinstance(group_names, list) else [],
        llm_info=_json_value(run.llm_info),
        status=run.status,
        archived_at=run.archived_at,
        created_at=run.created_at,
        started_at=run.started_at,
        finished_at=run.finished_at,
    )


def _result_view(result: RunResult) -> RunResultView:
    return RunResultView(
        id=result.id,
        run_id=result.run_id,
        test_case_id=result.test_case_id,
        system_prompt_version_id=result.system_prompt_version_id,
        task_prompt_version_id=result.task_prompt_version_id,
        sort_order=result.sort_order,
        group_name=result.group_name,
        test_case_title=result.test_case_title,
        test_case_text=result.test_case_text,
        expected_output=result.expected_output,
        system_prompt_text=result.system_prompt_text,
        task_prompt_text=result.task_prompt_text,
        tools_snapshot=_json_value(result.tools_snapshot),
        tool_mode=result.tool_mode,
        tool_choice=result.tool_choice,
        max_turns=result.max_turns,
        status=result.status,
        response_text=result.response_text,
        transcript=_json_value(result.transcript_json),
        turns=_json_value(result.turns_json),
        turn_count=result.turn_count,
        tool_call_count=result.tool_call_count,
        stopped_reason=result.stopped_reason,
        error=result.error,
        duration_ms=result.duration_ms,
        ttft_ms=result.ttft_ms,
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
        tokens_per_sec=result.tokens_per_sec,
        tokens_estimated=result.tokens_estimated,
        rating=result.rating,
        rating_note=result.rating_note,
        rated_via=result.rated_via,
        started_at=result.started_at,
        finished_at=result.finished_at,
    )


async def _get_or_404(scope: Scope, session: AsyncSession, run_id: int) -> Run:
    run = await get_run(scope, session, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such run.")
    return run


# --------------------------------------------------------------------------
# Runs
# --------------------------------------------------------------------------


@router.get("")
async def list_runs_endpoint(
    actor: CurrentUser,
    scope: CurrentScope,
    session: DbSession,
    archived: Literal["exclude", "only", "all"] = "exclude",
    run_status: Annotated[str | None, Query(alias="status")] = None,
    limit: int | None = None,
) -> list[RunView]:
    """Newest first. Archived runs are hidden unless asked for — archiving is
    not a status value, so it filters separately (see `runs.archived_at`).
    """
    del actor
    runs = await list_runs(
        scope, session, status=run_status, archived=archived, limit=limit
    )
    return [_run_view(run) for run in runs]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_run_endpoint(
    body: RunCreateRequest, actor: Writer, scope: CurrentScope, session: DbSession
) -> RunView:
    """Creates a run and freezes every test case into it.

    The snapshotting itself lives in `create_run_record`, which the MCP
    `create_run` tool calls too — an MCP-created run is indistinguishable from
    one made here.
    """
    del actor
    try:
        created = await create_run_record(
            scope,
            session,
            endpoint_id=body.endpoint_id,
            model_id=body.model_id,
            group_ids=body.group_ids,
            params=body.params(),
            comment=body.comment,
        )
    except CrossCustomerError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except (RunCreateError, ToolConfigError, NoUserMessageError) as exc:
        # `NoUserMessageError` is not a `RunCreateError` on purpose (a test case
        # left with neither content nor a task prompt — a deleted prompt SET
        # NULLs the slot), but at this boundary it is the same thing: a refusal
        # naming the case to fix, not a server fault.
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    await session.commit()
    return _run_view(await _get_or_404(scope, session, created.run_id))


@router.get("/{run_id}")
async def get_run_endpoint(
    run_id: int, actor: CurrentUser, scope: CurrentScope, session: DbSession
) -> RunDetailView:
    del actor
    run = await _get_or_404(scope, session, run_id)
    results = await list_run_results(scope, session, run_id)
    return RunDetailView(
        **_run_view(run).model_dump(),
        results=[_result_view(result) for result in results],
    )


@router.post("/{run_id}/archive")
async def archive_run_endpoint(
    run_id: int, actor: Writer, scope: CurrentScope, session: DbSession
) -> RunView:
    """Hides a run from the default lists. Nothing else changes — status and
    results are untouched, so an archived run with pending rows can be
    unarchived and resumed.
    """
    del actor
    run = await _get_or_404(scope, session, run_id)
    if await is_run_executing(session, run_id):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This run is currently executing — stop it before archiving.",
        )
    await set_run_archived_at(scope, session, run_id, utc_now())
    await session.commit()
    # The write went out as a bulk UPDATE, so the instance this session already
    # holds has to be re-read rather than trusted.
    await session.refresh(run)
    return _run_view(run)


@router.post("/{run_id}/unarchive")
async def unarchive_run_endpoint(
    run_id: int, actor: Writer, scope: CurrentScope, session: DbSession
) -> RunView:
    del actor
    run = await _get_or_404(scope, session, run_id)
    await set_run_archived_at(scope, session, run_id, None)
    await session.commit()
    await session.refresh(run)
    return _run_view(run)


@router.delete("/{run_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_run_endpoint(
    run_id: int, actor: Writer, scope: CurrentScope, session: DbSession
) -> None:
    """Deletes the run and, by FK cascade, all of its results."""
    del actor
    await _get_or_404(scope, session, run_id)
    if await is_run_executing(session, run_id):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This run is currently executing — stop it before deleting.",
        )
    await delete_run(scope, session, run_id)
    await session.commit()


# --------------------------------------------------------------------------
# Execution
# --------------------------------------------------------------------------


@router.post("/{run_id}/execute")
async def execute_run_endpoint(
    run_id: int, actor: Writer, scope: CurrentScope, session: DbSession
) -> Response:
    """Streams the execution of a run as NDJSON — one event per line.

    Execution is tied to this request: closing the tab aborts the in-flight
    completion. That is the deliberate tradeoff of having no job queue — the
    client gets live progress over one plain connection with no polling, and
    the cost is paid by rolling the interrupted row back to `pending` so
    Resume (this same endpoint) finishes the run later.
    """
    del actor
    run = await _get_or_404(scope, session, run_id)

    if await is_run_executing(session, run_id):
        raise HTTPException(status.HTTP_409_CONFLICT, "This run is already executing.")

    pending = await count_pending_results(scope, session, run_id)
    if pending == 0:
        # A complete, one-line stream rather than a status code: the client
        # reads every outcome the same way.
        return Response(
            content=serialize_event(
                RunDone(run_id=run_id, status=run.status, nothing_pending=True)
            ),
            media_type=NDJSON_MEDIA_TYPE,
            headers=NDJSON_HEADERS,
        )

    queue: asyncio.Queue[RunEvent | None] = asyncio.Queue()
    cancelled = asyncio.Event()

    def emit(event: RunEvent) -> None:
        queue.put_nowait(event)

    async def drive() -> None:
        try:
            await execute_run(run_id, emit, cancelled)
        except Exception as exc:  # noqa: BLE001 - reported on the stream itself
            emit(
                RunError(run_id=run_id, error=str(exc) or "Run execution failed.")
            )
        finally:
            queue.put_nowait(None)

    async def body() -> AsyncIterator[bytes]:
        run_in_background(drive())
        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield serialize_event(event).encode()
        finally:
            # Reached both on a clean end (where it is a no-op, the executor
            # having already finished) and on a client disconnect, which is the
            # case that matters: the detached executor sees the flag, resets its
            # in-flight row to `pending` and releases the run lock.
            cancelled.set()

    return StreamingResponse(
        body(), media_type=NDJSON_MEDIA_TYPE, headers=NDJSON_HEADERS
    )


# --------------------------------------------------------------------------
# Results
# --------------------------------------------------------------------------


@results_router.get("/{result_id}")
async def get_result_endpoint(
    result_id: int, actor: CurrentUser, scope: CurrentScope, session: DbSession
) -> RunResultView:
    del actor
    result = await get_run_result(scope, session, result_id)
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such result.")
    return _result_view(result)


@results_router.patch("/{result_id}")
async def rate_result_endpoint(
    result_id: int,
    body: RatingRequest,
    actor: Writer,
    scope: CurrentScope,
    session: DbSession,
) -> RunResultView:
    """Sets a result's verdict and/or its note.

    A row that has not finished is refused: execution can be fire-and-forget
    (MCP `execute_run`), so a grading loop can trivially outrun it and would
    otherwise be rating a response that does not exist yet.

    The verdict is stamped with how this request proved itself (`actor.via`),
    so a human clicking a rating here clears the judge badge an agent's earlier
    verdict left on the row — which is the whole point of recording it.
    """
    if "rating" not in body.model_fields_set and "note" not in body.model_fields_set:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Send a rating, a note, or both."
        )

    result = await get_run_result(scope, session, result_id)
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such result.")
    if result.status in ("pending", "running"):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This result has not finished running yet — rate it once it has.",
        )

    # A note-only patch leaves the rating (and with it `rated_via`) alone
    # rather than restating it: re-writing the same verdict would restamp it
    # with whoever edited the note.
    rating: Rating | None = (
        None if body.rating in (None, "unrated") else body.rating  # type: ignore[assignment]
    )

    written = await rate_result(
        scope,
        session,
        result_id,
        rating=rating,
        rating_note=body.note,
        write_note="note" in body.model_fields_set,
        write_rating="rating" in body.model_fields_set,
        rated_via=actor.via,
    )
    if written is None:  # pragma: no cover - deleted between the two statements
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such result.")
    await session.commit()
    # Bulk UPDATE again: re-read rather than trust the instance in hand.
    await session.refresh(result)
    return _result_view(result)
