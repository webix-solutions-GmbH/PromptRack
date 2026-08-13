"""Runs and their result rows.

``run_results`` is where the snapshot invariant lives, so nothing in here ever
rewrites a frozen column: run creation fills them once (see the run-create
service), and everything afterwards only touches the outcome, the metrics and
the manual verdict.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Machine, Run, RunResult
from app.repos.customers import assert_same_customer
from app.repos.scoped import apply_where, scope_through_parent
from app.scope import Scope, combine, scope_from_row, scope_values, where_scoped

# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------


async def get_run(scope: Scope, session: AsyncSession, run_id: int) -> Run | None:
    statement = apply_where(select(Run), where_scoped(scope, Run, Run.id == run_id))
    return (await session.scalars(statement)).first()


async def list_runs(
    scope: Scope,
    session: AsyncSession,
    *,
    status: str | None = None,
    archived: str = "all",
    run_ids: Sequence[int] | None = None,
    limit: int | None = None,
) -> list[Run]:
    """Runs, newest first.

    ``archived`` is ``exclude`` / ``only`` / ``all``: archiving is not a status
    value (status is the execution state machine Resume depends on), so it
    filters separately.
    """
    if run_ids is not None and not run_ids:
        return []

    statement = apply_where(
        select(Run),
        where_scoped(
            scope,
            Run,
            None if status is None else Run.status == status,
            _archived_condition(archived),
            None if run_ids is None else Run.id.in_(list(run_ids)),
        ),
    ).order_by(Run.created_at.desc(), Run.id.desc())
    if limit is not None:
        statement = statement.limit(limit)
    return list((await session.scalars(statement)).all())


def _archived_condition(archived: str):
    if archived == "exclude":
        return Run.archived_at.is_(None)
    if archived == "only":
        return Run.archived_at.is_not(None)
    return None


async def create_run(
    scope: Scope,
    session: AsyncSession,
    *,
    machine_id: int | None,
    machine_snapshot: str,
    model_id: str,
    group_names: str,
    params: str | None = None,
    comment: str | None = None,
    llm_info: str | None = None,
    status: str = "pending",
) -> Run:
    """Writes the run row.

    The machine is the run's one cross-root reference — the only place a run can
    be pointed at another workspace — so it is checked here rather than at the
    call site.
    """
    if machine_id is not None:
        await assert_same_customer(session, scope, Machine, machine_id)

    run = Run(
        machine_id=machine_id,
        machine_snapshot=machine_snapshot,
        model_id=model_id,
        group_names=group_names,
        params=params,
        comment=comment,
        llm_info=llm_info,
        status=status,
        **scope_values(scope),
    )
    session.add(run)
    await session.flush()
    return run


async def update_run_status(
    scope: Scope,
    session: AsyncSession,
    run_id: int,
    *,
    status: str,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
) -> None:
    values: dict[str, Any] = {"status": status}
    if started_at is not None:
        values["started_at"] = started_at
    if finished_at is not None:
        values["finished_at"] = finished_at
    statement = apply_where(update(Run), where_scoped(scope, Run, Run.id == run_id))
    await session.execute(statement.values(**values))


async def update_run_comment(
    scope: Scope, session: AsyncSession, run_id: int, comment: str | None
) -> None:
    statement = apply_where(update(Run), where_scoped(scope, Run, Run.id == run_id))
    await session.execute(statement.values(comment=comment))


async def set_run_archived_at(
    scope: Scope, session: AsyncSession, run_id: int, archived_at: datetime | None
) -> None:
    statement = apply_where(update(Run), where_scoped(scope, Run, Run.id == run_id))
    await session.execute(statement.values(archived_at=archived_at))


async def delete_run(scope: Scope, session: AsyncSession, run_id: int) -> None:
    statement = apply_where(delete(Run), where_scoped(scope, Run, Run.id == run_id))
    await session.execute(statement)


async def count_runs(
    scope: Scope, session: AsyncSession, *, archived: str = "all"
) -> int:
    statement = apply_where(
        select(func.count()).select_from(Run),
        where_scoped(scope, Run, _archived_condition(archived)),
    )
    return await session.scalar(statement) or 0


async def scope_for_run(
    session: AsyncSession, run_id: int
) -> tuple[Scope, Run] | None:
    """The scope entry point for background work — deliberately unscoped.

    The executor runs outside any request (MCP ``execute_run`` is
    fire-and-forget), so it cannot derive a scope from a session. It reads the
    run row instead and takes the scope *from* it. This is the only function in
    the repositories that is not itself scoped; authorization for it lives at
    the boundaries that can reach it, not here, since by then there is no
    request to authenticate.
    """
    run = await session.get(Run, run_id)
    if run is None:
        return None
    return scope_from_row(run.customer_id), run


# ---------------------------------------------------------------------------
# Run results — scope inherited through `run_id`
# ---------------------------------------------------------------------------


async def insert_run_results(
    scope: Scope,
    session: AsyncSession,
    run_id: int,
    rows: Sequence[Mapping[str, Any]],
) -> None:
    """Writes every result row of a run in one multi-row INSERT.

    Atomic with the run row when the caller wraps both in one transaction, which
    is what keeps a crash from leaving a run with no rows in it — Resume would
    have reported that as finished.

    Scope needs no predicate here: each row carries ``run_id``, and the run is
    what carries the workspace.
    """
    del scope
    if not rows:
        return
    await session.execute(
        insert(RunResult), [{**row, "run_id": run_id} for row in rows]
    )


async def list_run_results(
    scope: Scope, session: AsyncSession, run_id: int
) -> list[RunResult]:
    statement = apply_where(select(RunResult), _results_of_run(scope, run_id)).order_by(
        RunResult.sort_order.asc(), RunResult.id.asc()
    )
    return list((await session.scalars(statement)).all())


async def get_run_result(
    scope: Scope, session: AsyncSession, result_id: int
) -> RunResult | None:
    """A single result, scoped through the run it belongs to."""
    statement = apply_where(
        select(RunResult).join(Run, RunResult.run_id == Run.id),
        where_scoped(scope, Run, RunResult.id == result_id),
    )
    return (await session.scalars(statement)).first()


@dataclass(frozen=True)
class ResultStatusRow:
    id: int
    status: str


async def list_result_statuses(
    scope: Scope, session: AsyncSession, run_id: int
) -> list[ResultStatusRow]:
    """Ids and statuses in execution order — what the executor loops over."""
    statement = apply_where(
        select(RunResult.id, RunResult.status), _results_of_run(scope, run_id)
    ).order_by(RunResult.sort_order.asc(), RunResult.id.asc())
    rows = await session.execute(statement)
    return [ResultStatusRow(id=row[0], status=row[1]) for row in rows.all()]


async def count_pending_results(scope: Scope, session: AsyncSession, run_id: int) -> int:
    statement = apply_where(
        select(func.count()).select_from(RunResult),
        combine([_results_of_run(scope, run_id), RunResult.status == "pending"]),
    )
    return await session.scalar(statement) or 0


async def update_run_result(
    scope: Scope,
    session: AsyncSession,
    run_id: int,
    result_id: int,
    values: Mapping[str, Any],
) -> None:
    """Updates one result.

    Both keys go into the WHERE: the executor always knows which run it is
    working on, and the run is what carries the scope.
    """
    if not values:
        return
    statement = apply_where(
        update(RunResult),
        combine([_results_of_run(scope, run_id), RunResult.id == result_id]),
    )
    await session.execute(statement.values(**values))


async def reset_results_in_status(
    scope: Scope,
    session: AsyncSession,
    run_id: int,
    status: str,
    values: Mapping[str, Any],
) -> None:
    """Bulk-resets the rows in one status — how rows left ``running`` by a
    crashed process are reclaimed to ``pending`` at the next execution start.
    """
    statement = apply_where(
        update(RunResult),
        combine([_results_of_run(scope, run_id), RunResult.status == status]),
    )
    await session.execute(statement.values(**values))


@dataclass(frozen=True)
class RatedResult:
    """What a rating write actually stored, plus the run it belongs to."""

    run_id: int
    rating: str | None
    rating_note: str | None


async def rate_result(
    scope: Scope,
    session: AsyncSession,
    result_id: int,
    *,
    rating: str | None,
    rating_note: str | None = None,
    write_note: bool = False,
) -> RatedResult | None:
    """Sets a result's verdict.

    Only a result id is available here (both the UI and MCP have one), so the
    scope comes through the parent run. ``write_note`` is what distinguishes
    "clear the note" from "leave it alone": an omitted note must not wipe one,
    which is what the UI's rating buttons already do.

    Returns what was stored, or ``None`` when nothing matched.
    """
    values: dict[str, Any] = {"rating": rating}
    if write_note:
        values["rating_note"] = rating_note

    statement = apply_where(update(RunResult), _result_by_id(scope, result_id))
    result = await session.execute(
        statement.values(**values).returning(
            RunResult.run_id, RunResult.rating, RunResult.rating_note
        )
    )
    row = result.first()
    if row is None:
        return None
    return RatedResult(run_id=row[0], rating=row[1], rating_note=row[2])


async def set_result_note(
    scope: Scope, session: AsyncSession, result_id: int, note: str | None
) -> int | None:
    """Saves a result's free-text note without touching its rating.

    Returns the run id the row belongs to, or ``None`` when nothing matched.
    """
    statement = apply_where(update(RunResult), _result_by_id(scope, result_id))
    result = await session.execute(
        statement.values(rating_note=note).returning(RunResult.run_id)
    )
    row = result.first()
    return None if row is None else row[0]


async def list_result_ratings(
    scope: Scope, session: AsyncSession, run_id: int
) -> list[str | None]:
    statement = apply_where(select(RunResult.rating), _results_of_run(scope, run_id))
    return list((await session.scalars(statement)).all())


def _results_of_run(scope: Scope, run_id: int):
    """``run_results`` rows of one run — scoped through the run."""
    return combine(
        [
            RunResult.run_id == run_id,
            scope_through_parent(scope, RunResult.run_id, Run, Run.id),
        ]
    )


def _result_by_id(scope: Scope, result_id: int):
    """One ``run_results`` row by its own id — scoped through its run."""
    return combine(
        [
            RunResult.id == result_id,
            scope_through_parent(scope, RunResult.run_id, Run, Run.id),
        ]
    )
