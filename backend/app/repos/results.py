"""Cross-entity reads for `/results` — ported from `master:src/db/repo/results.ts`.

Each pivot gets exactly the rows it uses: run mode fetches the results of the
selected runs, model mode fetches the results of the selected (machine, model)
*pairs*. Neither loads the whole `run_results` table, which is what the old page
did in both modes on every request.

Everything here is scoped through `runs`, the root that carries `customer_id`;
`run_results` inherits it through `run_id` like every other child table.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import ColumnElement, and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Run, RunResult
from app.repos.scoped import apply_where
from app.scope import Scope, where_scoped


def _count_where(condition: ColumnElement[bool]):
    """`count(case when … then 1 end)` — counts matching rows, ignores the rest."""
    return func.count(case((condition, 1)))


#: The per-run tallies the run picker shows. `unrated` is deliberately absent:
#: it is `total - good - meh - bad` at the point of display, which is what keeps
#: an unrecognised stored rating counted as unrated rather than dropped.
_TALLIES = (
    _count_where(RunResult.status == "ok").label("ok"),
    _count_where(RunResult.status == "error").label("error"),
    _count_where(RunResult.rating == "good").label("good"),
    _count_where(RunResult.rating == "meh").label("meh"),
    _count_where(RunResult.rating == "bad").label("bad"),
    # Never coerced to 0: "nothing measured" is not "0 tok/s".
    func.avg(RunResult.tokens_per_sec).label("avg_rate"),
)


@dataclass(frozen=True)
class ComparableRunRow:
    """One selectable run in the run-mode picker, with its tallies."""

    run: Run
    ok: int
    error: int
    good: int
    meh: int
    bad: int
    avg_rate: float | None


async def list_comparable_runs(scope: Scope, session: AsyncSession) -> list[ComparableRunRow]:
    """Every run of the workspace, newest first, with its result tallies.

    Archived runs are included: the picker hides them itself but has to keep one
    that is already selected, so a bookmarked comparison still works.
    """
    statement = (
        apply_where(
            select(Run, *_TALLIES).outerjoin(RunResult, RunResult.run_id == Run.id),
            where_scoped(scope, Run),
        )
        .group_by(Run.id)
        .order_by(Run.created_at.desc(), Run.id.desc())
    )
    rows = await session.execute(statement)
    return [
        ComparableRunRow(
            run=row[0],
            ok=row[1],
            error=row[2],
            good=row[3],
            meh=row[4],
            bad=row[5],
            avg_rate=None if row[6] is None else float(row[6]),
        )
        for row in rows.all()
    ]


async def run_group_names(
    scope: Scope, session: AsyncSession, run_ids: Sequence[int]
) -> dict[int, list[str]]:
    """Distinct result group names per run, in the order the rows were written.

    `min(id)` is what makes "first seen" reproducible — reading it off an
    unordered scan is insertion order in practice, but nothing guarantees it.
    """
    if not run_ids:
        return {}

    first_id = func.min(RunResult.id)
    statement = (
        apply_where(
            select(RunResult.run_id, RunResult.group_name, first_id).join(
                Run, RunResult.run_id == Run.id
            ),
            where_scoped(scope, Run, RunResult.run_id.in_(list(run_ids))),
        )
        .group_by(RunResult.run_id, RunResult.group_name)
        .order_by(RunResult.run_id.asc(), first_id.asc())
    )
    rows = await session.execute(statement)

    by_run: dict[int, list[str]] = {}
    for run_id, group_name, _ in rows.all():
        by_run.setdefault(run_id, []).append(group_name)
    return by_run


@dataclass(frozen=True)
class ModelColumnRunRow:
    """A non-archived run, as model-column building reads it."""

    id: int
    machine_id: int | None
    machine_snapshot: str
    model_id: str
    created_at: datetime


@dataclass(frozen=True)
class ModelColumnResultRow:
    """An `ok` result of such a run."""

    run_id: int
    test_case_id: int | None
    status: str
    rating: str | None
    tokens_per_sec: float | None


@dataclass(frozen=True)
class ModelColumnInputs:
    runs: list[ModelColumnRunRow]
    results: list[ModelColumnResultRow]


async def model_column_inputs(scope: Scope, session: AsyncSession) -> ModelColumnInputs:
    """What the model picker is built from.

    Narrowed in SQL to what `build_model_columns` actually reads — non-archived
    runs and only their `ok` results. The output is identical either way,
    because the pure function skips both itself; this only keeps the read small.
    """
    run_statement = apply_where(
        select(
            Run.id, Run.machine_id, Run.machine_snapshot, Run.model_id, Run.created_at
        ),
        where_scoped(scope, Run, Run.archived_at.is_(None)),
    ).order_by(Run.created_at.desc(), Run.id.desc())

    result_statement = apply_where(
        select(
            RunResult.run_id,
            RunResult.test_case_id,
            RunResult.status,
            RunResult.rating,
            RunResult.tokens_per_sec,
        ).join(Run, RunResult.run_id == Run.id),
        where_scoped(scope, Run, Run.archived_at.is_(None), RunResult.status == "ok"),
    ).order_by(RunResult.id.asc())

    run_rows = (await session.execute(run_statement)).all()
    result_rows = (await session.execute(result_statement)).all()

    return ModelColumnInputs(
        runs=[
            ModelColumnRunRow(
                id=row[0],
                machine_id=row[1],
                machine_snapshot=row[2],
                model_id=row[3],
                created_at=row[4],
            )
            for row in run_rows
        ],
        results=[
            ModelColumnResultRow(
                run_id=row[0],
                test_case_id=row[1],
                status=row[2],
                rating=row[3],
                tokens_per_sec=row[4],
            )
            for row in result_rows
        ],
    )


@dataclass(frozen=True)
class CompareCellRow:
    """A result plus the two run fields a cell is not frozen with."""

    result: RunResult
    run_created_at: datetime
    run_params: str | None
    machine_id: int | None = None
    model_id: str = ""


async def compare_cells_for_runs(
    scope: Scope, session: AsyncSession, run_ids: Sequence[int]
) -> list[CompareCellRow]:
    """Run mode: every result of the selected runs."""
    if not run_ids:
        return []
    statement = apply_where(
        select(RunResult, Run.created_at, Run.params).join(Run, RunResult.run_id == Run.id),
        where_scoped(scope, Run, RunResult.run_id.in_(list(run_ids))),
    )
    rows = await session.execute(statement)
    return [
        CompareCellRow(result=row[0], run_created_at=row[1], run_params=row[2])
        for row in rows.all()
    ]


async def compare_cells_for_models(
    scope: Scope,
    session: AsyncSession,
    columns: Sequence[tuple[int | None, str]],
    test_case_ids: Sequence[int] | None,
) -> list[CompareCellRow]:
    """Model mode: the `ok` and `error` results of non-archived runs, restricted
    to the selected (machine, model) *pairs*.

    The pair predicate matters: filtering on the model id alone would load the
    same model's results from every other machine and then throw them away, and
    `tokens_per_sec` is a property of the hardware. Errors are fetched too, so a
    newer failed attempt can be *reported* rather than silently skipped.
    """
    if not columns:
        return []
    if test_case_ids is not None and not test_case_ids:
        return []

    pairs = [
        and_(
            Run.machine_id.is_(None) if machine_id is None else Run.machine_id == machine_id,
            Run.model_id == model_id,
        )
        for machine_id, model_id in columns
    ]

    statement = apply_where(
        select(
            RunResult, Run.created_at, Run.params, Run.machine_id, Run.model_id
        ).join(Run, RunResult.run_id == Run.id),
        where_scoped(
            scope,
            Run,
            Run.archived_at.is_(None),
            RunResult.status.in_(["ok", "error"]),
            pairs[0] if len(pairs) == 1 else or_(*pairs),
            None
            if test_case_ids is None
            else RunResult.test_case_id.in_(list(test_case_ids)),
        ),
    )
    rows = await session.execute(statement)
    return [
        CompareCellRow(
            result=row[0],
            run_created_at=row[1],
            run_params=row[2],
            machine_id=row[3],
            model_id=row[4],
        )
        for row in rows.all()
    ]
