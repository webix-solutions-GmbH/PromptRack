"""`/api/results/matrix` — the comparison matrix, both pivots, in one payload.

The SPA needs everything the page draws answered in one place: the pickers,
the selection it actually honoured, the columns, the rows and the per-column
tallies. Splitting it into four endpoints would only mean four round trips to
render one table, and the pickers have to agree with the matrix anyway — they
are computed from the same reads.

All of the logic is in :mod:`app.services.compare` (pure) and
:mod:`app.repos.results` (scoped reads). What lives here is the query-string
contract and the mapping from `run_results` rows to matrix cells:

* `?mode=` wins; without it a URL carrying `?runs=` stays in **run** mode, so a
  bookmarked run comparison keeps its pivot while everything else defaults to
  **models**.
* `?runs=` is a comma-separated id list (repeated params are joined), `?models=`
  is **repeated** rather than comma-joined because a model id is free-form text
  that must never need escaping, and `?group=` narrows model mode's rows.

A shared `/results` link is a contract, so the machines→endpoints rename stopped
at the wire: `?models=` keeps its name and its positional `<id>|<model_id>`
format (`app.services.compare.model_column_key`), and only the identifiers
around it read `endpoint` now. Same reasoning that kept `?runs=` meaningful
without an explicit `?mode=`.

Reading results is `CurrentUser`, not `Writer`: a viewer's whole job is to look
at them.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.guards import CurrentScope, CurrentUser, DbSession
from app.repos.results import (
    ComparableRunRow,
    CompareCellRow,
    compare_cells_for_models,
    compare_cells_for_runs,
    list_comparable_runs,
    model_column_inputs,
    run_group_names,
)
from app.repos.test_cases import compare_test_case_rows, live_expected_outputs
from app.scope import Scope
from app.services.compare import (
    MAX_GROUP_FILTER,
    MIN_COMPARE_MODELS,
    MIN_COMPARE_RUNS,
    CompareCellView,
    CompareMode,
    CompareRowView,
    CompareRunView,
    CompareTestCaseView,
    ModelColumnResult,
    ModelColumnRun,
    ModelColumnView,
    annotate_drift,
    annotate_live_rubric,
    build_compare_matrix,
    build_model_columns,
    build_model_matrix,
    live_rubrics_by_test_case,
    live_texts_by_test_case,
    model_column_key,
    parse_compare_mode,
    parse_id_list,
    parse_model_column_keys,
    parse_run_ids,
    snapshot_endpoint_name,
    split_model_column_key,
)
from app.services.tool_loop import parse_transcript

router = APIRouter(prefix="/results", tags=["results"])


# --------------------------------------------------------------------------
# Wire shapes
# --------------------------------------------------------------------------


class GroupOption(BaseModel):
    """One chip of model mode's group filter."""

    id: int
    name: str
    test_case_count: int


class ColumnTally(BaseModel):
    """Per-column totals over the cells **on screen**.

    Not over whole runs: a whole-run total would count test cases this
    comparison filtered out, and in model mode a column's cells come from
    several runs anyway. `unrated` is derived rather than counted, so a stored
    rating nobody recognises still shows up in the total.
    """

    answered: int
    good: int
    meh: int
    bad: int
    unrated: int
    avg_rate: float | None
    #: Sum of `duration_ms` over the cells on screen — model generation time
    #: only, same reasoning as `avg_rate` above: a column's cells can come
    #: from several runs in model mode, so this is tallied here rather than
    #: read off any one run.
    total_duration_ms: int | None


class MatrixResponse(BaseModel):
    """Everything `/results` draws, for whichever pivot it is in.

    The fields of the *other* pivot come back empty rather than absent, so the
    client can switch modes without branching on presence.
    """

    mode: CompareMode
    #: How many columns this pivot needs before a matrix means anything.
    min_columns: int
    rows: list[CompareRowView] = []

    # --- run mode ---
    available_runs: list[CompareRunView] = []
    selected_run_ids: list[int] = []
    run_columns: list[CompareRunView] = []
    #: Archived runs the picker is hiding — the one thing the UI cannot show on
    #: its own, and therefore the only sentence the page carries.
    hidden_archived_runs: int = 0

    # --- model mode ---
    available_models: list[ModelColumnView] = []
    selected_model_keys: list[str] = []
    model_columns: list[ModelColumnView] = []
    column_tallies: list[ColumnTally] = []
    groups: list[GroupOption] = []
    selected_group_ids: list[int] = []
    #: Test cases in scope that no selected model has answered yet.
    uncovered_test_cases: int = 0


# --------------------------------------------------------------------------
# Row -> cell
# --------------------------------------------------------------------------


def _tool_call_names(transcript_json: str | None) -> list[str]:
    """The tool names a stored transcript shows being called, in order."""
    return [
        call.name
        for message in (parse_transcript(transcript_json) or [])
        for call in (message.tool_calls or [])
    ]


def _to_cell(scope: Scope, row: CompareCellRow, column_key: str = "") -> CompareCellView:
    """One `run_results` row as a matrix cell.

    Always the row's own snapshots — the three frozen texts (system prompt,
    task prompt, the case's own content), the rubric they were graded against
    and the tools; the run is consulted only for what is not frozen per result
    — when it was created, and the request params it was sent with.
    """
    result = row.result
    return CompareCellView(
        id=result.id,
        run_id=result.run_id,
        run_created_at=row.run_created_at,
        # The customer id, which is what keeps the deleted-test-case text
        # fallback from matching across workspaces.
        scope_key=str(scope.customer_id or ""),
        test_case_id=result.test_case_id,
        system_prompt_version_id=result.system_prompt_version_id,
        task_prompt_version_id=result.task_prompt_version_id,
        sort_order=result.sort_order,
        group_name=result.group_name,
        test_case_title=result.test_case_title,
        test_case_text=result.test_case_text,
        system_prompt_text=result.system_prompt_text,
        task_prompt_text=result.task_prompt_text,
        expected_output=result.expected_output,
        tools_snapshot=result.tools_snapshot,
        tool_mode=result.tool_mode,
        tool_choice=result.tool_choice,
        max_turns=result.max_turns,
        run_params=row.run_params,
        status=result.status,
        response_text=result.response_text,
        reasoning_text=result.reasoning_text,
        error=result.error,
        duration_ms=result.duration_ms,
        ttft_ms=result.ttft_ms,
        completion_tokens=result.completion_tokens,
        tokens_per_sec=result.tokens_per_sec,
        tokens_estimated=result.tokens_estimated,
        rating=result.rating,
        rating_note=result.rating_note,
        turn_count=result.turn_count,
        tool_call_count=result.tool_call_count,
        tool_call_names=_tool_call_names(result.transcript_json),
        column_key=column_key,
    )


def _run_view(row: ComparableRunRow, group_names: list[str]) -> CompareRunView:
    run = row.run
    return CompareRunView(
        id=run.id,
        model_id=run.model_id,
        endpoint_name=snapshot_endpoint_name(run.endpoint_snapshot),
        status=run.status,
        archived=run.archived_at is not None,
        created_at=run.created_at,
        group_names=group_names,
        # Verbatim, not parsed: `formatParams` on the client already renders
        # this shape for a run's own page.
        params=run.params,
        comment=run.comment,
        good=row.good,
        meh=row.meh,
        bad=row.bad,
        ok=row.ok,
        error=row.error,
        avg_rate=row.avg_rate,
        total_duration_ms=row.total_duration_ms,
    )


def _tallies(rows: list[CompareRowView], column: int) -> ColumnTally:
    cells = [row.cells[column] for row in rows if row.cells[column] is not None]
    rates = [cell.tokens_per_sec for cell in cells if cell.tokens_per_sec is not None]
    # Never coerced to 0, mirroring `avg_rate`: a column with no measured
    # duration reports "nothing measured", not "took no time at all".
    durations = [cell.duration_ms for cell in cells if cell.duration_ms is not None]
    good = sum(1 for cell in cells if cell.rating == "good")
    meh = sum(1 for cell in cells if cell.rating == "meh")
    bad = sum(1 for cell in cells if cell.rating == "bad")
    return ColumnTally(
        answered=len(cells),
        good=good,
        meh=meh,
        bad=bad,
        unrated=len(cells) - good - meh - bad,
        avg_rate=sum(rates) / len(rates) if rates else None,
        total_duration_ms=sum(durations) if durations else None,
    )


# --------------------------------------------------------------------------
# The endpoint
# --------------------------------------------------------------------------


@router.get("/matrix")
async def results_matrix_endpoint(
    actor: CurrentUser,
    scope: CurrentScope,
    session: DbSession,
    mode: str | None = None,
    runs: Annotated[list[str] | None, Query()] = None,
    models: Annotated[list[str] | None, Query()] = None,
    group: Annotated[list[str] | None, Query()] = None,
) -> MatrixResponse:
    """The comparison matrix and the pickers that select it."""
    del actor
    # Model mode is the default; an existing `?runs=` link keeps its pivot.
    resolved: CompareMode = parse_compare_mode(mode) or ("runs" if runs else "models")
    if resolved == "runs":
        return await _run_mode(scope, session, runs)
    return await _model_mode(scope, session, models, group)


async def _run_mode(
    scope: Scope, session: AsyncSession, runs: list[str] | None
) -> MatrixResponse:
    """Hand-picked runs as columns — the only pivot that can put two runs of the
    same model side by side, and therefore the one a baseline comparison uses.
    """
    summaries = await list_comparable_runs(scope, session)
    names = await run_group_names(scope, session, [row.run.id for row in summaries])

    # A run is comparable once it has produced at least one result — which
    # covers completed runs as well as stopped or partially failed ones.
    available = [
        _run_view(row, names.get(row.run.id, []))
        for row in summaries
        if row.ok > 0 or row.error > 0 or row.run.status == "completed"
    ]
    by_id = {run.id: run for run in available}

    selected_ids = [run_id for run_id in parse_run_ids(runs) if run_id in by_id]
    # Archived runs are hidden from the picker, but an already-selected one
    # stays listed so a bookmarked comparison still works — and can be
    # deselected.
    picker = [run for run in available if not run.archived or run.id in selected_ids]

    rows: list[CompareRowView] = []
    if len(selected_ids) >= MIN_COMPARE_RUNS:
        cells = [
            _to_cell(scope, row)
            for row in await compare_cells_for_runs(scope, session, selected_ids)
        ]
        # Run mode passes no live anchor: its rows are a set of runs, not a
        # claim about what the suite says today, so "edited since" would be
        # meaningless here.
        compared = annotate_drift(build_compare_matrix(selected_ids, cells))
        # The rubric is the exception, and the only live read this pivot makes:
        # the model never saw it, so editing it does not invalidate these
        # results — it moves the standard they are graded by, which is exactly
        # what someone rating them here needs. Narrowed to the ids on screen.
        rows = annotate_live_rubric(
            compared,
            live_by_test_case=await live_expected_outputs(
                scope,
                session,
                [row.test_case_id for row in compared if row.test_case_id is not None],
            ),
        )

    return MatrixResponse(
        mode="runs",
        min_columns=MIN_COMPARE_RUNS,
        rows=rows,
        available_runs=picker,
        selected_run_ids=selected_ids,
        run_columns=[by_id[run_id] for run_id in selected_ids],
        hidden_archived_runs=len(available) - len(picker),
    )


async def _model_mode(
    scope: Scope,
    session: AsyncSession,
    models: list[str] | None,
    group: list[str] | None,
) -> MatrixResponse:
    """Live test cases as rows, each cell a model's most recent usable result."""
    inputs = await model_column_inputs(scope, session)
    available = build_model_columns(
        [
            ModelColumnRun(
                id=run.id,
                endpoint_id=run.endpoint_id,
                endpoint_name=snapshot_endpoint_name(run.endpoint_snapshot),
                model_id=run.model_id,
                created_at=run.created_at,
                # The read already excluded archived runs; the pure function
                # would skip them anyway.
                archived=False,
            )
            for run in inputs.runs
        ],
        [
            ModelColumnResult(
                run_id=result.run_id,
                test_case_id=result.test_case_id,
                status=result.status,
                rating=result.rating,
                tokens_per_sec=result.tokens_per_sec,
                duration_ms=result.duration_ms,
            )
            for result in inputs.results
        ],
    )
    by_key = {column.key: column for column in available}
    selected_keys = [key for key in parse_model_column_keys(models) if key in by_key]

    test_cases = [
        CompareTestCaseView(
            id=row.id,
            group_id=row.group_id,
            group_name=row.group_name,
            title=row.title,
            text=row.text,
            # The *current* drafts of the two slots' prompts, which is what
            # makes model mode's "edited since" three comparisons rather than
            # one. `compare_test_case_rows` joins `prompts` twice for them.
            system_prompt_text=row.system_prompt_text,
            task_prompt_text=row.task_prompt_text,
            # Never sent, so it takes the *other* kind of comparison: a rubric
            # rewritten since the runs leaves their results valid and moves the
            # standard they are graded by.
            expected_output=row.expected_output,
        )
        for row in await compare_test_case_rows(scope, session)
    ]

    groups: dict[int, GroupOption] = {}
    for test_case in test_cases:
        current = groups.get(test_case.group_id)
        groups[test_case.group_id] = GroupOption(
            id=test_case.group_id,
            name=test_case.group_name,
            test_case_count=1 if current is None else current.test_case_count + 1,
        )
    selected_group_ids = [
        group_id for group_id in parse_id_list(group, MAX_GROUP_FILTER) if group_id in groups
    ]
    scoped_cases = (
        [row for row in test_cases if row.group_id in selected_group_ids]
        if selected_group_ids
        else test_cases
    )

    cells: list[CompareCellView] = []
    if len(selected_keys) >= MIN_COMPARE_MODELS:
        refs = [split_model_column_key(key) for key in selected_keys]
        # Only the group filter narrows the test cases; without one every live
        # test case is in scope and the extra predicate would be noise.
        scoped_ids = [row.id for row in scoped_cases] if selected_group_ids else None
        cells = [
            _to_cell(scope, row, model_column_key(row.endpoint_id, row.model_id))
            for row in await compare_cells_for_models(
                scope,
                session,
                [(ref.endpoint_id, ref.model_id) for ref in refs if ref is not None],
                scoped_ids,
            )
        ]

    matrix = build_model_matrix(selected_keys, scoped_cases, cells)
    rows = annotate_live_rubric(
        annotate_drift(matrix.rows, live_by_test_case=live_texts_by_test_case(scoped_cases)),
        live_by_test_case=live_rubrics_by_test_case(scoped_cases),
    )

    return MatrixResponse(
        mode="models",
        min_columns=MIN_COMPARE_MODELS,
        rows=rows,
        available_models=available,
        selected_model_keys=selected_keys,
        model_columns=[by_key[key] for key in selected_keys],
        column_tallies=[_tallies(rows, index) for index in range(len(selected_keys))],
        groups=list(groups.values()),
        selected_group_ids=selected_group_ids,
        uncovered_test_cases=matrix.uncovered_test_cases,
    )
