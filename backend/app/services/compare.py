"""The results matrix — pure logic, no database.

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
`app.api.results`, the same split `app.services.message_assembly` and
`app.services.attribution` draw.

A result row freezes **three texts** — `system_prompt_text`,
`task_prompt_text`, `test_case_text` — and this module compares them
separately, which is the whole point of keeping them apart: a cell that differs
can say *the task prompt changed* instead of merging prompt drift and data
drift into one indistinguishable "user message differs".

Two things carry over from the old implementation unchanged in substance and
renamed in the obvious way (`prompts` are now `test_cases`):

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
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Literal

from app.models import Rating, ResultStatus, ToolChoice, ToolMode

#: Hard cap on how many runs can be compared side by side.
MAX_COMPARE_RUNS = 4
#: One run is a valid selection: a single run-mode column is still the only
#: view that shows that run's rows against the *live* rubric, with its own
#: params/comment in the header — none of which the run detail page does.
MIN_COMPARE_RUNS = 1

MAX_COMPARE_MODELS = 6
#: One model is likewise a valid selection: the same test case × model matrix
#: with a single column is exactly "show me everything this model answered",
#: the cheapest review of a model across all of its runs. Both pivots settle on
#: the same rule — a lone column is a real view, not a degenerate one.
MIN_COMPARE_MODELS = 1

#: Enough to select every group; the cap only bounds a hostile URL.
MAX_GROUP_FILTER = 200

#: Which pivot `/results` is showing.
CompareMode = Literal["runs", "models"]

#: What a run whose endpoint row is gone displays as.
DELETED_ENDPOINT_NAME = "(deleted endpoint)"


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


def _text_key(text: str | None) -> str:
    """Normalizes a frozen text so trivial whitespace differences still match.

    `None` and blank normalize to the same empty string on purpose: an absent
    prompt and a whitespace-only one send the same thing, so they are not drift.
    """
    return _WHITESPACE.sub(" ", text or "").strip()


def snapshot_endpoint_name(raw: str | None) -> str:
    """Endpoint name out of a run's frozen `endpoint_snapshot` JSON.

    Rendering never reads the live endpoint row — that is the snapshot invariant
    — so a deleted or renamed endpoint still shows the name the run was made
    against, and a snapshot that cannot be parsed degrades to a label rather
    than to an exception.
    """
    try:
        parsed: Any = json.loads(raw) if raw else None
    except ValueError:
        return DELETED_ENDPOINT_NAME
    if isinstance(parsed, dict):
        name = parsed.get("name")
        if isinstance(name, str) and name:
            return name
    return DELETED_ENDPOINT_NAME


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CompareRunView:
    """One selectable run, as the picker and the run-mode column headers show it."""

    id: int
    model_id: str
    endpoint_name: str
    status: str
    #: Archived runs are kept out of the picker unless already selected.
    archived: bool
    created_at: datetime
    group_names: list[str]
    #: The run's request parameters as the raw JSON string `runs.params` holds
    #: (null = server defaults). Passed through rather than parsed: the client
    #: already renders the same shape for a run's own page
    #: (`frontend/src/lib/format.ts`'s `formatParams`), and parsing it here
    #: would only mean two renderings of one fact that could disagree.
    params: str | None
    #: Names of the parameter groups selected at creation — the readable label
    #: over `params`: two columns of one model differing only in "no thinking"
    #: should say so by name, not by a raw JSON clip.
    param_group_names: list[str]
    #: The note whoever started the run left on it — "quantization swap",
    #: "temperature A/B". Free text, and the one thing in a column header that
    #: says *why* this run exists.
    comment: str | None
    good: int
    meh: int
    bad: int
    ok: int
    error: int
    avg_rate: float | None
    #: Sum of `duration_ms` over the run's own results — model generation time
    #: only, immune to a run being paused and resumed days later, which is why
    #: this is a sum of the frozen per-result durations and not `finished_at -
    #: started_at`. A high tok/s can still be a slow suite if the model
    #: over-reasons, which is what this sits beside the rate to catch.
    total_duration_ms: int | None


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
    #: The committed version each slot's prompt was at, when that draft was
    #: clean at run creation. Null = a dirty draft, or an empty slot. One per
    #: slot: the two drafts are independent.
    system_prompt_version_id: int | None
    task_prompt_version_id: int | None
    sort_order: int
    group_name: str
    test_case_title: str
    #: The test case's own `content` — the data half of the user message.
    test_case_text: str | None
    #: The system prompt's text as frozen into the row; compared on its own.
    system_prompt_text: str | None
    #: The task prompt's text as frozen into the row; compared on its own,
    #: which is what lets "the instruction changed" and "the data changed" be
    #: two different sentences.
    task_prompt_text: str | None
    #: The rubric as frozen into the row — never sent to a model, and never
    #: rendered per cell: it is row-level information, so the row carries the
    #: copy the cells agree on (:func:`shared_expected_output`) and a row whose
    #: cells disagree says so as drift instead.
    expected_output: str | None
    #: Raw `tools_snapshot` JSON, compared key-insensitively for drift.
    tools_snapshot: str | None
    tool_mode: ToolMode
    tool_choice: ToolChoice | None
    max_turns: int
    #: Raw `runs.params` JSON (temperature / max_tokens), or null for defaults.
    run_params: str | None
    status: ResultStatus
    response_text: str | None
    #: The thinking, when the endpoint gave it its own channel. Carried so a cell
    #: renders it the same way the run detail page does.
    reasoning_text: str | None
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
    test_case_text: str | None
    #: Same length and order as the selected columns; `None` = no result.
    cells: list[CompareCellView | None]
    #: The rubric every cell of this row froze, or `None` when the row has no
    #: rubric *or* its cells disagree about it — see
    #: :func:`shared_expected_output`. Computed here rather than on the client
    #: for the same reason `drift` is: the "identical across the row" question
    #: is answered once, by the same normalization, so a whitespace-only
    #: difference cannot be silently dropped by one mechanism while the other
    #: stays quiet about it.
    expected_output: str | None
    #: The rubric the live test case carries **today**, when that is something
    #: the frozen copy above does not already say: the rubric was edited since
    #: the runs, or there is no frozen copy to show. `None` otherwise (and
    #: always when the test case is gone). Filled by
    #: :func:`annotate_live_rubric`, in both pivots.
    live_expected_output: str | None = None
    #: Whether the live rubric differs from the one the row's cells froze.
    #: Only ever `True` when there *was* a single frozen rubric to differ from,
    #: so "added after the runs" and "the cells disagree" both read as `False`
    #: here — neither is an edit of anything this row displayed.
    rubric_edited_since: bool = False
    #: Conditions that are *not* held constant across the row; see
    #: :func:`describe_row_drift`. Filled by :func:`annotate_drift`.
    drift: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class LiveTexts:
    """What a live test case holds *today* — the three parts it would send,
    plus the rubric it would be graded by.

    Model mode anchors its rows to live test cases, so it can additionally
    report that one of them was edited after every compared run. Separate
    values rather than one, because "the task prompt was rewritten" and "the
    data was corrected" are different findings.

    The rubric is the odd one out and stays named apart for it: it is never
    sent, so an edit to it does not invalidate a past result the way an edit to
    the three sent parts does — it moves the standard those results are graded
    by, which is a finding of its own rather than none.
    """

    test_case_text: str | None = None
    system_prompt_text: str | None = None
    task_prompt_text: str | None = None
    expected_output: str | None = None


@dataclass(frozen=True)
class CompareTestCaseView:
    """A live test case, which is what a model-mode row is anchored to.

    The two prompt texts are the *current drafts* of the prompts its slots
    reference — null for an empty slot. Defaulted so a caller that has not
    resolved them yet degrades to comparing the case text alone rather than
    reporting drift it did not measure.
    """

    id: int
    group_id: int
    group_name: str
    title: str
    text: str | None
    system_prompt_text: str | None = None
    task_prompt_text: str | None = None
    #: The rubric the case carries today — never sent, and defaulted for the
    #: same reason the two prompt texts are: a caller that has not read it
    #: degrades to reporting nothing rather than to reporting a removal.
    expected_output: str | None = None

    def live_texts(self) -> LiveTexts:
        return LiveTexts(
            test_case_text=self.text,
            system_prompt_text=self.system_prompt_text,
            task_prompt_text=self.task_prompt_text,
            expected_output=self.expected_output,
        )


# ---------------------------------------------------------------------------
# Row-level rubric
# ---------------------------------------------------------------------------


def shared_expected_output(cells: Sequence[CompareCellView | None]) -> str | None:
    """The rubric the row's cells all froze, or `None` if there isn't one.

    `None` covers both "no rubric at all" and "the cells disagree", because
    neither may be rendered as *the row's* rubric: a test case edited between
    two runs would otherwise show one cell's rubric as if the whole row had
    been graded by it. A disagreement is reported as drift instead (the
    `"expected output"` entry of :func:`describe_row_drift`), which is where a
    row-level difference belongs — repeating the rubric in every column is the
    duplication this layout exists to remove.

    Identity is `_text_key`, the same normalization drift uses, and not byte
    equality: with byte equality a trailing newline would blank the row header
    while drift stayed silent, and the reader would lose the rubric with
    nothing to explain where it went.
    """
    present = [cell for cell in cells if cell is not None]
    if not present:
        return None
    keys = {_text_key(cell.expected_output) for cell in present}
    if len(keys) != 1 or not keys.pop():
        return None
    return present[0].expected_output


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
    test_case_text: str | None
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
            expected_output=shared_expected_output(self.cells),
        )


def build_compare_matrix(
    run_ids: Sequence[int], results: Sequence[CompareCellView]
) -> list[CompareRowView]:
    """Pivots flat `run_results` rows into a test case × run matrix.

    Row matching is primarily by `test_case_id`. Results whose test case was
    deleted (`test_case_id is None`) fall back to matching on identical
    normalized text *within one workspace*, which lets them line up with rows
    that still carry an id.

    That fallback is **skipped entirely when the text is blank**: `content` is
    nullable now (a case whose task prompt is the whole user message has no data
    of its own), and "these two rows are both empty" is not evidence that they
    are the same test case. Without the skip, one blank deleted case would adopt
    every other blank row in the run.

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
        normalized = _text_key(result.test_case_text)
        # Ids are global, so only the text fallback needs the scope key. A blank
        # text is no key at all — see the docstring.
        key = f"{result.scope_key} {normalized}" if normalized else None

        row: _RowBuilder | None
        if result.test_case_id is None:
            # Deleted test case: the text is all we have to go on.
            row = by_text.get(key) if key is not None else None
        else:
            row = by_test_case.get(result.test_case_id)
            # Adopt a row created by a deleted-test-case result with the same
            # text, so both halves of the pair land in one row.
            text_row = by_text.get(key) if key is not None else None
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
        if key is not None:
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
    """A model column as an (endpoint, model) pair — what a query filters on."""

    endpoint_id: int | None
    model_id: str


def model_column_key(endpoint_id: int | None, model_id: str) -> str:
    """Stable identity of a model column: `<endpoint_id>|<model_id>`.

    The string itself is part of the `/results` URL and did **not** change
    with the machines→endpoints rename: a shared link is a contract, so only
    the identifiers around this format were renamed.

    Keyed on the endpoint *id* rather than its name so renaming an endpoint does
    not split a column, and including the endpoint at all because
    `tokens_per_sec` is a property of the hardware — one model on two boxes must
    stay two columns or the speed numbers become noise. A deleted endpoint
    collapses to id `0`.
    """
    return f"{endpoint_id or 0}|{model_id}"


def split_model_column_key(key: str) -> ModelColumnRef | None:
    """Inverse of :func:`model_column_key`; `None` for anything malformed."""
    separator = key.find("|")
    if separator <= 0:
        return None
    endpoint_id = _parse_int(key[:separator])
    model_id = key[separator + 1 :]
    if endpoint_id is None or endpoint_id < 0 or not model_id:
        return None
    return ModelColumnRef(endpoint_id=endpoint_id or None, model_id=model_id)


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
    endpoint_name: str
    #: Non-archived runs that produced at least one usable result for this pair.
    run_count: int
    latest_run_at: datetime
    #: Distinct test cases this model has a usable result for.
    test_case_count: int
    good: int
    meh: int
    bad: int
    avg_rate: float | None
    #: Sum of `duration_ms` over this column's `ok` results — same generation-
    #: time-only reasoning as :attr:`CompareRunView.total_duration_ms`, tallied
    #: here rather than read from SQL because a model column spans several runs.
    total_duration_ms: int | None


@dataclass(frozen=True)
class ModelColumnRun:
    """The `runs` fields :func:`build_model_columns` needs."""

    id: int
    endpoint_id: int | None
    endpoint_name: str
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
    duration_ms: int | None


@dataclass
class _ColumnBuilder:
    key: str
    model_id: str
    endpoint_name: str
    run_count: int
    latest_run_at: datetime
    good: int
    meh: int
    bad: int
    test_case_ids: set[int]
    rates: list[float]
    durations: list[int]

    def freeze(self) -> ModelColumnView:
        return ModelColumnView(
            key=self.key,
            model_id=self.model_id,
            endpoint_name=self.endpoint_name,
            run_count=self.run_count,
            latest_run_at=self.latest_run_at,
            test_case_count=len(self.test_case_ids),
            good=self.good,
            meh=self.meh,
            bad=self.bad,
            avg_rate=_average(self.rates),
            total_duration_ms=_sum_or_none(self.durations),
        )


def _average(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _sum_or_none(values: Sequence[int]) -> int | None:
    """Mirrors SQL `sum()` over an all-NULL column: `None`, never `0`.

    Rows with a null duration are simply skipped by the caller before this
    ever sees them, so an empty list here means "nothing measured", not "the
    measured total was zero".
    """
    return sum(values) if values else None


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

        key = model_column_key(run.endpoint_id, run.model_id)
        column = columns.get(key)
        if column is None:
            column = _ColumnBuilder(
                key=key,
                model_id=run.model_id,
                endpoint_name=run.endpoint_name,
                run_count=0,
                latest_run_at=run.created_at,
                good=0,
                meh=0,
                bad=0,
                test_case_ids=set(),
                rates=[],
                durations=[],
            )
            columns[key] = column

        column.run_count += 1
        if run.created_at >= column.latest_run_at:
            column.latest_run_at = run.created_at
            # A deleted endpoint has no live name; the newest snapshot is the
            # best one available.
            column.endpoint_name = run.endpoint_name

        for result in ok_results:
            # A deleted test case can never be a model-mode row (rows are
            # anchored to live test cases), so it is not part of the coverage
            # count either.
            if result.test_case_id is not None:
                column.test_case_ids.add(result.test_case_id)
            if result.tokens_per_sec is not None:
                column.rates.append(result.tokens_per_sec)
            if result.duration_ms is not None:
                column.durations.append(result.duration_ms)
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
        key=lambda column: (column.model_id, column.endpoint_name),
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
                # The *frozen* rubric, like the frozen texts beside it — model
                # mode anchors a row's identity to the live test case, but what
                # a cell was graded against is what its own run recorded.
                expected_output=shared_expected_output(row_cells),
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


#: The three frozen texts, each compared on its own and named on its own. The
#: labels are user-visible, and the split is the payoff of freezing the parts
#: separately: "task prompt" and "test case text" are different findings, where
#: the pre-pivot single "effective prompt" could only ever say that *something*
#: in the message changed.
_TEXT_ASPECTS: tuple[tuple[str, Callable[[CompareCellView], str | None]], ...] = (
    ("system prompt", lambda cell: cell.system_prompt_text),
    ("task prompt", lambda cell: cell.task_prompt_text),
    ("test case text", lambda cell: cell.test_case_text),
)


def describe_row_drift(
    cells: Sequence[CompareCellView | None], live: LiveTexts | None = None
) -> list[str]:
    """Names the conditions that are *not* held constant across a row.

    The whole point of a comparison row is that only the model differs. Once a
    column is "the latest result" rather than "one run", its cells can come from
    runs with different prompts, tools or temperatures — and a difference in the
    answers would then be config, not model. Rather than hide that, say it.

    `live` (model mode, where the row is anchored to a live test case)
    additionally catches a part edited after every compared run — three
    comparisons, one per text, each with its own message, so "the task prompt
    was rewritten since" is not reported as "the test case was edited".
    """
    present = [cell for cell in cells if cell is not None]
    if not present:
        return []

    aspects: list[tuple[str, Callable[[CompareCellView], str]]] = [
        *(
            (label, lambda cell, of=of: _text_key(of(cell)))
            for label, of in _TEXT_ASPECTS
        ),
        # Outside `_TEXT_ASPECTS` because it is not one of the three texts sent
        # to the model — but it *is* compared against the live test case too
        # (`_LIVE_ASPECTS` below), on different terms: the model never saw the
        # rubric, so rewriting it does not invalidate a past result the way
        # rewriting a sent part does. What moved is the standard those results
        # were graded by, which is worth one sentence rather than silence.
        # That difference is also why the row header's peek shows *both* copies
        # — the frozen one explains the ratings already on screen, the live one
        # is what to rate by now — instead of the row quietly swapping one text
        # for the other. Across the row it is drift like anything else, and it
        # is what the row header falls back to reporting when
        # `shared_expected_output` refuses to show one cell's rubric as the
        # row's.
        ("expected output", lambda cell: _text_key(cell.expected_output)),
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

    if live is not None:
        first = present[0]
        for label, of in _LIVE_ASPECTS:
            # Already drifting across the row: saying it also drifted from live
            # adds nothing a reader can act on.
            if label in drift:
                continue
            live_text = getattr(live, _LIVE_FIELD[label])
            if _text_key(of(first)) != _text_key(live_text):
                drift.append(f"{label} edited since")

    return drift


#: What model mode additionally compares against the live test case: the three
#: sent texts, plus the rubric — see the note at the `"expected output"` aspect
#: for why the rubric belongs here despite never being sent.
_LIVE_ASPECTS: tuple[tuple[str, Callable[[CompareCellView], str | None]], ...] = (
    *_TEXT_ASPECTS,
    ("expected output", lambda cell: cell.expected_output),
)

#: Which :class:`LiveTexts` field each aspect compares against.
_LIVE_FIELD: dict[str, str] = {
    "system prompt": "system_prompt_text",
    "task prompt": "task_prompt_text",
    "test case text": "test_case_text",
    "expected output": "expected_output",
}


def annotate_drift(
    rows: Sequence[CompareRowView],
    *,
    live_by_test_case: Mapping[int, LiveTexts] | None = None,
) -> list[CompareRowView]:
    """Fills every row's `drift`, which is what the matrix renders under its title.

    `live_by_test_case` is model mode's anchor: the live texts keyed by test
    case id. Run mode passes nothing — its rows are a set of runs, not a claim
    about what the suite says today, so "edited since" would be meaningless
    there.
    """
    return [
        replace(
            row,
            drift=describe_row_drift(
                row.cells,
                None
                if live_by_test_case is None or row.test_case_id is None
                else live_by_test_case.get(row.test_case_id),
            ),
        )
        for row in rows
    ]


def live_texts_by_test_case(
    test_case_rows: Sequence[CompareTestCaseView],
) -> dict[int, LiveTexts]:
    """The map :func:`annotate_drift` takes in model mode."""
    return {row.id: row.live_texts() for row in test_case_rows}


def _live_rubric(
    cells: Sequence[CompareCellView | None], live: str | None
) -> tuple[str | None, bool]:
    """One row's `(live_expected_output, rubric_edited_since)`.

    Blank and absent are the same rubric throughout, `_text_key` deciding it,
    exactly as they are for the sent texts.
    """
    frozen = shared_expected_output(cells)
    present = live if _text_key(live) else None
    if frozen is None:
        # Either no cell froze a rubric, or the cells disagree about theirs.
        # Both collapse to the same answer: there is no single "at run time"
        # rubric an edit could have moved *from*, so the live one is offered
        # without calling it an edit. A rubric written after the runs was never
        # edited, and a disagreement is already reported as `"expected output"`
        # drift — saying it twice in two vocabularies helps nobody.
        return present, False
    edited = _text_key(frozen) != _text_key(live)
    # Unchanged: the row already shows the frozen copy, and a second identical
    # block beside it is noise, not reassurance.
    return (present if edited else None), edited


def annotate_live_rubric(
    rows: Sequence[CompareRowView],
    *,
    live_by_test_case: Mapping[int, str | None],
) -> list[CompareRowView]:
    """Fills every row's `live_expected_output` / `rubric_edited_since`.

    Runs in **both** pivots, unlike :func:`annotate_drift`'s live anchor: the
    rubric is the one frozen text a later edit does not invalidate — the model
    never saw it — so "what would I grade this by today" is a fair question of
    a hand-picked pair of runs just as much as of a model column.

    `live_by_test_case` maps a live test case id to its current rubric.
    **Membership is the signal**: a key absent means the test case itself is
    gone, which is not the same as it having no rubric, and leaves the row
    exactly as it was rather than claiming the rubric was removed.
    """
    annotated: list[CompareRowView] = []
    for row in rows:
        if row.test_case_id is None or row.test_case_id not in live_by_test_case:
            annotated.append(row)
            continue
        live, edited = _live_rubric(row.cells, live_by_test_case[row.test_case_id])
        annotated.append(
            replace(row, live_expected_output=live, rubric_edited_since=edited)
        )
    return annotated


def live_rubrics_by_test_case(
    test_case_rows: Sequence[CompareTestCaseView],
) -> dict[int, str | None]:
    """The map :func:`annotate_live_rubric` takes in model mode.

    Run mode builds the same shape from a read of just the ids on screen
    (`app.repos.test_cases.live_expected_outputs`); it has no reason to load
    the whole suite the way model mode already does.
    """
    return {row.id: row.expected_output for row in test_case_rows}
