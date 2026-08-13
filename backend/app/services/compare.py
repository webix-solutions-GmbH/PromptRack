"""The results matrix — pure, ported from `git show master:src/lib/compare.ts`.

`/results` has two pivots and this module holds both, plus the parsing of the
selection that picks between them:

* **by run** — hand-picked runs as columns. The only pivot that can put two
  runs of the *same* model side by side (quantization swap, temperature A/B,
  test-case rewrite), which is what the spec's "compare against baseline" link
  uses as its behavior diff viewer.
* **by model** — the live test cases as rows, each cell the model's most recent
  usable result, whichever run produced it. "Which model is best at my suite"
  should not depend on remembering which run was which.

Everything here is a function of its arguments: no database, no session, no
request. The scoped reads live in `app.repos.results` and the wire shape in
`app.api.results`, the same split `app.services.effective_prompt` and
`app.services.attribution` draw.

Two things carry over from the old implementation unchanged in substance and
renamed in the obvious way (`prompts` are now `test_cases`; the frozen system
message is now the *effective prompt*):

* the deleted-test-case text fallback is keyed on `scope_key + normalized
  text`, so two workspaces' byte-identical test cases can never collapse into
  one row — ids are global, text is not;
* a model-mode cell keeps the newest **ok** result and *reports* a newer failed
  attempt rather than blanking the row (`CompareCellView.superseded`), because
  an endpoint that was down during the last run must not hide a good answer.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Literal

from app.models import Rating, ResultStatus, ToolChoice, ToolMode

#: Hard cap on how many runs can be compared side by side.
MAX_COMPARE_RUNS = 4
#: Minimum selection before a run-mode matrix can be rendered.
MIN_COMPARE_RUNS = 2

MAX_COMPARE_MODELS = 6
#: One model is a valid selection: the same test case × model matrix with a
#: single column is exactly "show me everything this model answered", the
#: cheapest review of a model across all of its runs. Run mode still needs two —
#: a single run is already its own detail page.
MIN_COMPARE_MODELS = 1

#: Enough to select every group; the cap only bounds a hostile URL.
MAX_GROUP_FILTER = 200

#: Which pivot `/results` is showing.
CompareMode = Literal["runs", "models"]

#: What a run whose machine row is gone displays as.
DELETED_MACHINE_NAME = "(deleted machine)"


# ---------------------------------------------------------------------------
# Selection parsing
# ---------------------------------------------------------------------------


def parse_compare_mode(raw: str | None) -> CompareMode | None:
    """Reads an explicit `?mode=`; `None` leaves the default to the caller."""
    return raw if raw in ("runs", "models") else None


#: Plain ASCII digits only. `int()` alone would also accept `1_0` and non-ASCII
#: digits, which is a needlessly generous reading of a hand-edited URL.
_INTEGER = re.compile(r"[+-]?[0-9]+")


def _parse_int(text: str) -> int | None:
    cleaned = text.strip()
    return int(cleaned) if _INTEGER.fullmatch(cleaned) else None


def parse_id_list(raw: str | Sequence[str] | None, max_items: int) -> list[int]:
    """Shared `1,5,7` / repeated-param id parsing.

    Ignores junk, zero and negatives, de-duplicates, and truncates, so a
    hand-edited URL can never blow up the table. Order is the order given.
    """
    if raw is None:
        return []
    value = raw if isinstance(raw, str) else ",".join(raw)
    if not value:
        return []

    ids: list[int] = []
    for part in value.split(","):
        parsed = _parse_int(part)
        if parsed is None or parsed <= 0 or parsed in ids:
            continue
        ids.append(parsed)
        if len(ids) >= max_items:
            break
    return ids


def parse_run_ids(
    raw: str | Sequence[str] | None, max_items: int = MAX_COMPARE_RUNS
) -> list[int]:
    """Parses a `?runs=1,5,7` selection into a clean list of run ids."""
    return parse_id_list(raw, max_items)


def serialize_run_ids(ids: Sequence[int]) -> str:
    """Serializes a selection back into the `runs` query param."""
    return ",".join(str(run_id) for run_id in ids)


_WHITESPACE = re.compile(r"\s+")


def _text_key(text: str) -> str:
    """Normalizes test-case text so trivial whitespace differences still match."""
    return _WHITESPACE.sub(" ", text).strip()


def snapshot_machine_name(raw: str | None) -> str:
    """Machine name out of a run's frozen `machine_snapshot` JSON.

    Rendering never reads the live machine row — that is the snapshot invariant
    — so a deleted or renamed machine still shows the name the run was made
    against, and a snapshot that cannot be parsed degrades to a label rather
    than to an exception.
    """
    try:
        parsed: Any = json.loads(raw) if raw else None
    except ValueError:
        return DELETED_MACHINE_NAME
    if isinstance(parsed, dict):
        name = parsed.get("name")
        if isinstance(name, str) and name:
            return name
    return DELETED_MACHINE_NAME


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CompareRunView:
    """One selectable run, as the picker and the run-mode column headers show it."""

    id: int
    model_id: str
    machine_name: str
    status: str
    #: Archived runs are kept out of the picker unless already selected.
    archived: bool
    created_at: datetime
    group_names: list[str]
    good: int
    meh: int
    bad: int
    ok: int
    error: int
    avg_rate: float | None


@dataclass(frozen=True)
class SupersededAttempt:
    """A newer attempt at the same test case that was *not* used as the cell.

    Model mode shows the latest result that actually produced an answer, so an
    endpoint that was down during the most recent run cannot hide a perfectly
    good older result. That fallback has to be visible, hence this.
    """

    run_id: int
    status: ResultStatus
    created_at: datetime


@dataclass(frozen=True)
class CompareCellView:
    """One cell of the matrix: a single `run_results` row."""

    id: int
    run_id: int
    #: `runs.created_at` — what "most recent result" is ordered by.
    run_created_at: datetime
    #: Opaque workspace key (the customer id). The deleted-test-case text
    #: fallback only matches within one key.
    scope_key: str
    test_case_id: int | None
    #: The committed prompt version this result tested, when the draft was
    #: clean at run creation. Null = a dirty draft, or no prompt at all.
    prompt_version_id: int | None
    sort_order: int
    group_name: str
    test_case_title: str
    test_case_text: str
    #: Effective prompt frozen into the row; part of the drift check.
    effective_prompt_text: str | None
    #: Raw `tools_snapshot` JSON, compared key-insensitively for drift.
    tools_snapshot: str | None
    tool_mode: ToolMode
    tool_choice: ToolChoice | None
    max_turns: int
    #: Raw `runs.params` JSON (temperature / max_tokens), or null for defaults.
    run_params: str | None
    status: ResultStatus
    response_text: str | None
    error: str | None
    duration_ms: int | None
    ttft_ms: int | None
    completion_tokens: int | None
    tokens_per_sec: float | None
    tokens_estimated: bool
    rating: Rating | None
    rating_note: str | None
    #: Null on an ordinary test case; set for a tool test.
    turn_count: int | None
    tool_call_count: int | None
    #: The tool names the model called, in order, with repeats — the quickest
    #: way to see whether a model picked the right tools.
    tool_call_names: list[str] = field(default_factory=list)
    #: Set by :func:`build_model_matrix` when a newer attempt was skipped.
    superseded: SupersededAttempt | None = None
    #: Which model column this cell belongs to; model mode only.
    column_key: str = ""


@dataclass(frozen=True)
class CompareRowView:
    """One row of the matrix: a test case, plus one cell (or `None`) per column."""

    #: Stable key — `test-case:<id>`, or `text:<n>` for a deleted test case.
    key: str
    test_case_id: int | None
    group_name: str
    test_case_title: str
    test_case_text: str
    #: Same length and order as the selected columns; `None` = no result.
    cells: list[CompareCellView | None]
    #: Conditions that are *not* held constant across the row; see
    #: :func:`describe_row_drift`. Filled by :func:`annotate_drift`.
    drift: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class CompareTestCaseView:
    """A live test case, which is what a model-mode row is anchored to."""

    id: int
    group_id: int
    group_name: str
    title: str
    text: str


# ---------------------------------------------------------------------------
# Run mode
# ---------------------------------------------------------------------------


@dataclass
class _RowBuilder:
    """Mutable while the matrix is assembled; frozen on the way out."""

    key: str
    test_case_id: int | None
    group_name: str
    test_case_title: str
    test_case_text: str
    cells: list[CompareCellView | None]
    #: Column the row was first seen in — drives row ordering.
    first_column: int
    #: `sort_order` within that first column.
    first_sort_order: int

    def freeze(self) -> CompareRowView:
        return CompareRowView(
            key=self.key,
            test_case_id=self.test_case_id,
            group_name=self.group_name,
            test_case_title=self.test_case_title,
            test_case_text=self.test_case_text,
            cells=self.cells,
        )


def build_compare_matrix(
    run_ids: Sequence[int], results: Sequence[CompareCellView]
) -> list[CompareRowView]:
    """Pivots flat `run_results` rows into a test case × run matrix.

    Row matching is primarily by `test_case_id`. Results whose test case was
    deleted (`test_case_id is None`) fall back to matching on identical
    normalized text *within one workspace*, which lets them line up with rows
    that still carry an id.

    Rows are ordered by first appearance: every test case of the first column in
    its run order, then those only present in the second column, and so on. If a
    single run maps two results onto the same row, the first one wins.
    """
    rows: list[_RowBuilder] = []
    by_test_case: dict[int, _RowBuilder] = {}
    by_text: dict[str, _RowBuilder] = {}

    column_of = {run_id: index for index, run_id in enumerate(run_ids)}

    ordered = sorted(
        (result for result in results if result.run_id in column_of),
        key=lambda result: (column_of[result.run_id], result.sort_order, result.id),
    )

    for result in ordered:
        column = column_of[result.run_id]
        # Ids are global, so only the text fallback needs the scope key.
        key = f"{result.scope_key} {_text_key(result.test_case_text)}"

        row: _RowBuilder | None
        if result.test_case_id is None:
            # Deleted test case: the text is all we have to go on.
            row = by_text.get(key)
        else:
            row = by_test_case.get(result.test_case_id)
            # Adopt a row created by a deleted-test-case result with the same
            # text, so both halves of the pair land in one row.
            text_row = by_text.get(key)
            if row is None and text_row is not None and text_row.test_case_id is None:
                row = text_row

        if row is None:
            row = _RowBuilder(
                key=(
                    f"test-case:{result.test_case_id}"
                    if result.test_case_id is not None
                    else f"text:{len(rows)}"
                ),
                test_case_id=result.test_case_id,
                group_name=result.group_name,
                test_case_title=result.test_case_title,
                test_case_text=result.test_case_text,
                cells=[None] * len(run_ids),
                first_column=column,
                first_sort_order=result.sort_order,
            )
            rows.append(row)

        if result.test_case_id is not None and result.test_case_id not in by_test_case:
            by_test_case[result.test_case_id] = row
            if row.test_case_id is None:
                row.test_case_id = result.test_case_id
        by_text.setdefault(key, row)

        if row.cells[column] is None:
            row.cells[column] = result

    rows.sort(key=lambda row: (row.first_column, row.first_sort_order))
    return [row.freeze() for row in rows]


# ---------------------------------------------------------------------------
# Model mode: test cases as the base, one column per model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ModelColumnRef:
    """A model column as a (machine, model) pair — what a query filters on."""

    machine_id: int | None
    model_id: str


def model_column_key(machine_id: int | None, model_id: str) -> str:
    """Stable identity of a model column: `<machine_id>|<model_id>`.

    Keyed on the machine *id* rather than its name so renaming an endpoint does
    not split a column, and including the machine at all because
    `tokens_per_sec` is a property of the hardware — one model on two boxes must
    stay two columns or the speed numbers become noise. A deleted machine
    collapses to id `0`.
    """
    return f"{machine_id or 0}|{model_id}"


def split_model_column_key(key: str) -> ModelColumnRef | None:
    """Inverse of :func:`model_column_key`; `None` for anything malformed."""
    separator = key.find("|")
    if separator <= 0:
        return None
    machine_id = _parse_int(key[:separator])
    model_id = key[separator + 1 :]
    if machine_id is None or machine_id < 0 or not model_id:
        return None
    return ModelColumnRef(machine_id=machine_id or None, model_id=model_id)


def parse_model_column_keys(
    raw: str | Sequence[str] | None, max_items: int = MAX_COMPARE_MODELS
) -> list[str]:
    """Parses a model-column selection.

    Always *repeated* params (`?models=1|a&models=2|b`) rather than one
    comma-joined value, because a model id is free-form text and must never need
    escaping.
    """
    if raw is None:
        return []
    values = [raw] if isinstance(raw, str) else list(raw)
    keys: list[str] = []
    for value in values:
        if split_model_column_key(value) is None or value in keys:
            continue
        keys.append(value)
        if len(keys) >= max_items:
            break
    return keys


@dataclass(frozen=True)
class ModelColumnView:
    """One selectable model column, as the picker and column headers show it."""

    key: str
    model_id: str
    machine_name: str
    #: Non-archived runs that produced at least one usable result for this pair.
    run_count: int
    latest_run_at: datetime
    #: Distinct test cases this model has a usable result for.
    test_case_count: int
    good: int
    meh: int
    bad: int
    avg_rate: float | None


@dataclass(frozen=True)
class ModelColumnRun:
    """The `runs` fields :func:`build_model_columns` needs."""

    id: int
    machine_id: int | None
    machine_name: str
    model_id: str
    created_at: datetime
    archived: bool


@dataclass(frozen=True)
class ModelColumnResult:
    """The `run_results` fields :func:`build_model_columns` needs."""

    run_id: int
    test_case_id: int | None
    status: str
    rating: str | None
    tokens_per_sec: float | None


@dataclass
class _ColumnBuilder:
    key: str
    model_id: str
    machine_name: str
    run_count: int
    latest_run_at: datetime
    good: int
    meh: int
    bad: int
    test_case_ids: set[int]
    rates: list[float]

    def freeze(self) -> ModelColumnView:
        return ModelColumnView(
            key=self.key,
            model_id=self.model_id,
            machine_name=self.machine_name,
            run_count=self.run_count,
            latest_run_at=self.latest_run_at,
            test_case_count=len(self.test_case_ids),
            good=self.good,
            meh=self.meh,
            bad=self.bad,
            avg_rate=_average(self.rates),
        )


def _average(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def build_model_columns(
    run_rows: Sequence[ModelColumnRun], result_rows: Sequence[ModelColumnResult]
) -> list[ModelColumnView]:
    """Groups every non-archived run into model columns.

    Only `ok` results count: a column exists because the model answered
    something, not because a run was created. Archived runs are excluded
    outright — unlike run mode there is no per-run selection that could ask one
    back.
    """
    results_by_run: dict[int, list[ModelColumnResult]] = {}
    for result in result_rows:
        results_by_run.setdefault(result.run_id, []).append(result)

    columns: dict[str, _ColumnBuilder] = {}

    for run in run_rows:
        if run.archived:
            continue
        ok_results = [
            result for result in results_by_run.get(run.id, []) if result.status == "ok"
        ]
        if not ok_results:
            continue

        key = model_column_key(run.machine_id, run.model_id)
        column = columns.get(key)
        if column is None:
            column = _ColumnBuilder(
                key=key,
                model_id=run.model_id,
                machine_name=run.machine_name,
                run_count=0,
                latest_run_at=run.created_at,
                good=0,
                meh=0,
                bad=0,
                test_case_ids=set(),
                rates=[],
            )
            columns[key] = column

        column.run_count += 1
        if run.created_at >= column.latest_run_at:
            column.latest_run_at = run.created_at
            # A deleted machine has no live name; the newest snapshot is the
            # best one available.
            column.machine_name = run.machine_name

        for result in ok_results:
            # A deleted test case can never be a model-mode row (rows are
            # anchored to live test cases), so it is not part of the coverage
            # count either.
            if result.test_case_id is not None:
                column.test_case_ids.add(result.test_case_id)
            if result.tokens_per_sec is not None:
                column.rates.append(result.tokens_per_sec)
            # Anything unrecognised counts as unrated, never as a verdict — the
            # rating column is plain text, so a legacy value must not vanish
            # into `bad` (nor be invented into `good`).
            if result.rating == "good":
                column.good += 1
            elif result.rating == "meh":
                column.meh += 1
            elif result.rating == "bad":
                column.bad += 1

    return sorted(
        (column.freeze() for column in columns.values()),
        key=lambda column: (column.model_id, column.machine_name),
    )


@dataclass(frozen=True)
class ModelMatrix:
    rows: list[CompareRowView]
    #: Test cases in scope that none of the selected models has answered yet.
    uncovered_test_cases: int


def _is_newer(candidate: CompareCellView, current: CompareCellView | None) -> bool:
    if current is None:
        return True
    if candidate.run_created_at != current.run_created_at:
        return candidate.run_created_at > current.run_created_at
    return candidate.id > current.id


def build_model_matrix(
    column_keys: Sequence[str],
    test_case_rows: Sequence[CompareTestCaseView],
    cells: Sequence[CompareCellView],
) -> ModelMatrix:
    """Pivots results into a test case × model matrix, newest usable result per cell.

    "Usable" means `status == 'ok'`: a newer run whose row errored (endpoint
    down, OOM) must not blank out a good older answer, but it is recorded as
    :attr:`CompareCellView.superseded` so the fallback is never silent. Rows keep
    the order of `test_case_rows`, and test cases nobody answered are counted
    rather than rendered as an all-empty row.
    """
    column_of = {key: index for index, key in enumerate(column_keys)}
    in_scope = {row.id for row in test_case_rows}

    best: dict[tuple[int, int], CompareCellView] = {}
    newest: dict[tuple[int, int], CompareCellView] = {}

    for cell in cells:
        if cell.test_case_id is None or cell.test_case_id not in in_scope:
            continue
        column = column_of.get(cell.column_key)
        if column is None:
            continue

        key = (column, cell.test_case_id)
        if _is_newer(cell, newest.get(key)):
            newest[key] = cell
        if cell.status == "ok" and _is_newer(cell, best.get(key)):
            best[key] = cell

    rows: list[CompareRowView] = []
    uncovered = 0

    for test_case in test_case_rows:
        row_cells: list[CompareCellView | None] = []
        for column in range(len(column_keys)):
            chosen = best.get((column, test_case.id))
            if chosen is None:
                row_cells.append(None)
                continue
            latest = newest.get((column, test_case.id))
            superseded = (
                SupersededAttempt(
                    run_id=latest.run_id,
                    status=latest.status,
                    created_at=latest.run_created_at,
                )
                if latest is not None and latest.id != chosen.id
                else None
            )
            row_cells.append(replace(chosen, superseded=superseded))

        if all(cell is None for cell in row_cells):
            uncovered += 1
            continue

        rows.append(
            CompareRowView(
                key=f"test-case:{test_case.id}",
                test_case_id=test_case.id,
                group_name=test_case.group_name,
                test_case_title=test_case.title,
                test_case_text=test_case.text,
                cells=row_cells,
            )
        )

    return ModelMatrix(rows=rows, uncovered_test_cases=uncovered)


# ---------------------------------------------------------------------------
# Drift
# ---------------------------------------------------------------------------


def _sort_keys(value: Any) -> Any:
    if isinstance(value, list):
        return [_sort_keys(item) for item in value]
    if isinstance(value, dict):
        return {key: _sort_keys(value[key]) for key in sorted(value)}
    return value


def _stable_json(raw: str | None) -> str:
    """Recursively key-sorted JSON, so formatting differences are not drift."""
    if raw is None:
        return ""
    try:
        return json.dumps(_sort_keys(json.loads(raw)), sort_keys=True)
    except ValueError:
        return raw


def describe_row_drift(
    cells: Sequence[CompareCellView | None], live_test_case_text: str | None = None
) -> list[str]:
    """Names the conditions that are *not* held constant across a row.

    The whole point of a comparison row is that only the model differs. Once a
    column is "the latest result" rather than "one run", its cells can come from
    runs with different prompts, tools or temperatures — and a difference in the
    answers would then be config, not model. Rather than hide that, say it.

    `live_test_case_text` (model mode, where the row is anchored to a live test
    case) additionally catches a test case edited after every compared run.
    """
    present = [cell for cell in cells if cell is not None]
    if not present:
        return []

    aspects: list[tuple[str, Callable[[CompareCellView], str]]] = [
        ("test case text", lambda cell: _text_key(cell.test_case_text)),
        ("effective prompt", lambda cell: _text_key(cell.effective_prompt_text or "")),
        ("tools", lambda cell: _stable_json(cell.tools_snapshot)),
        ("tool mode", lambda cell: cell.tool_mode),
        ("tool choice", lambda cell: cell.tool_choice or "(unset)"),
        ("params", lambda cell: _stable_json(cell.run_params)),
    ]

    # max_turns only means anything once tools actually run.
    if any(cell.tool_mode == "execute" for cell in present):
        aspects.append(("max turns", lambda cell: str(cell.max_turns)))

    drift = [
        label for label, of in aspects if len({of(cell) for cell in present}) > 1
    ]

    if (
        live_test_case_text is not None
        and "test case text" not in drift
        and _text_key(present[0].test_case_text) != _text_key(live_test_case_text)
    ):
        drift.append("test case edited since")

    return drift


def annotate_drift(
    rows: Sequence[CompareRowView], *, anchored_to_live_test_case: bool
) -> list[CompareRowView]:
    """Fills every row's `drift`, which is what the matrix renders under its title."""
    return [
        replace(
            row,
            drift=describe_row_drift(
                row.cells, row.test_case_text if anchored_to_live_test_case else None
            ),
        )
        for row in rows
    ]
