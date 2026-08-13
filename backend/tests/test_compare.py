"""The results matrix, database-free — ported from
`git show master:src/lib/compare.test.ts`.

Every case there survives under the pivoted names (`prompts` → `test_cases`,
the frozen system message → the effective prompt), because what each one pins
is a rule about *matching and fallback*, not about what the rows are called:
which results land in one row, which cell a column shows when several results
compete, and what a difference between cells is allowed to mean.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from itertools import count
from typing import Any

from app.services.compare import (
    CompareCellView,
    CompareTestCaseView,
    ModelColumnResult,
    ModelColumnRun,
    annotate_drift,
    build_compare_matrix,
    build_model_columns,
    build_model_matrix,
    describe_row_drift,
    model_column_key,
    parse_compare_mode,
    parse_model_column_keys,
    parse_run_ids,
    serialize_run_ids,
    snapshot_machine_name,
    split_model_column_key,
)

_ids = count(1)

EPOCH = datetime(2026, 1, 1, tzinfo=UTC)


def at(offset: int) -> datetime:
    """A run timestamp; only its ordering matters to any of this."""
    return EPOCH + timedelta(seconds=offset)


def cell(run_id: int, **overrides: Any) -> CompareCellView:
    defaults: dict[str, Any] = {
        "id": next(_ids),
        "run_id": run_id,
        "run_created_at": at(1000),
        "scope_key": "",
        "test_case_id": None,
        "prompt_version_id": None,
        "sort_order": 0,
        "group_name": "group",
        "test_case_title": "title",
        "test_case_text": "text",
        "effective_prompt_text": None,
        "tools_snapshot": None,
        "tool_mode": "none",
        "tool_choice": None,
        "max_turns": 6,
        "run_params": None,
        "status": "ok",
        "response_text": "response",
        "error": None,
        "duration_ms": 1000,
        "ttft_ms": 100,
        "completion_tokens": 10,
        "tokens_per_sec": 10.0,
        "tokens_estimated": False,
        "rating": None,
        "rating_note": None,
        "turn_count": None,
        "tool_call_count": None,
    }
    return CompareCellView(**{**defaults, **overrides})


def live_case(case_id: int, **overrides: Any) -> CompareTestCaseView:
    defaults: dict[str, Any] = {
        "id": case_id,
        "group_id": 1,
        "group_name": "group",
        "title": f"test case {case_id}",
        "text": f"text {case_id}",
    }
    return CompareTestCaseView(**{**defaults, **overrides})


# ---------------------------------------------------------------------------
# Selection parsing
# ---------------------------------------------------------------------------


class TestParseRunIds:
    def test_parses_a_comma_separated_list(self) -> None:
        assert parse_run_ids("1,5,7") == [1, 5, 7]

    def test_returns_an_empty_list_for_missing_or_empty_input(self) -> None:
        assert parse_run_ids(None) == []
        assert parse_run_ids("") == []

    def test_skips_junk_zero_and_negative_ids(self) -> None:
        assert parse_run_ids("1,abc,,0,-3, 4 ") == [1, 4]

    def test_reads_only_plain_ascii_digits(self) -> None:
        # `int()` on its own would accept both of these.
        assert parse_run_ids("1_0,١٢") == []

    def test_de_duplicates_while_keeping_the_given_order(self) -> None:
        assert parse_run_ids("3,1,3,1") == [3, 1]

    def test_truncates_to_the_maximum(self) -> None:
        assert parse_run_ids("1,2,3,4,5,6") == [1, 2, 3, 4]
        assert parse_run_ids("1,2,3", 2) == [1, 2]

    def test_joins_repeated_query_params(self) -> None:
        assert parse_run_ids(["1", "2"]) == [1, 2]

    def test_round_trips_through_serialize_run_ids(self) -> None:
        assert parse_run_ids(serialize_run_ids([2, 9])) == [2, 9]


class TestParseCompareMode:
    def test_accepts_the_two_pivots_and_rejects_anything_else(self) -> None:
        assert parse_compare_mode("runs") == "runs"
        assert parse_compare_mode("models") == "models"
        assert parse_compare_mode("nonsense") is None
        assert parse_compare_mode(None) is None


class TestModelColumnKey:
    def test_round_trips_machine_id_and_model_id(self) -> None:
        parsed = split_model_column_key(model_column_key(3, "qwen3:32b"))
        assert parsed is not None
        assert (parsed.machine_id, parsed.model_id) == (3, "qwen3:32b")

    def test_keeps_a_model_id_containing_separator_adjacent_characters(self) -> None:
        parsed = split_model_column_key(model_column_key(7, "Qwen/Qwen3-32B-AWQ"))
        assert parsed is not None
        assert parsed.model_id == "Qwen/Qwen3-32B-AWQ"

    def test_maps_a_deleted_machine_to_id_zero_and_back_to_none(self) -> None:
        assert model_column_key(None, "m") == "0|m"
        parsed = split_model_column_key("0|m")
        assert parsed is not None
        assert (parsed.machine_id, parsed.model_id) == (None, "m")

    def test_rejects_malformed_keys(self) -> None:
        assert split_model_column_key("nope") is None
        assert split_model_column_key("|m") is None
        assert split_model_column_key("1|") is None


class TestParseModelColumnKeys:
    def test_reads_repeated_params_de_duplicating_and_keeping_order(self) -> None:
        assert parse_model_column_keys(["2|b", "1|a", "2|b"]) == ["2|b", "1|a"]

    def test_accepts_a_single_value_and_drops_junk(self) -> None:
        assert parse_model_column_keys("1|a") == ["1|a"]
        assert parse_model_column_keys(["junk", "1|a"]) == ["1|a"]
        assert parse_model_column_keys(None) == []

    def test_truncates_to_the_maximum(self) -> None:
        assert parse_model_column_keys(["1|a", "2|b", "3|c"], 2) == ["1|a", "2|b"]


class TestSnapshotMachineName:
    def test_reads_the_frozen_name(self) -> None:
        assert snapshot_machine_name('{"name": "box", "base_url": "x"}') == "box"

    def test_degrades_rather_than_raising(self) -> None:
        # A run whose snapshot is missing or unreadable still has to render.
        assert snapshot_machine_name(None) == "(deleted machine)"
        assert snapshot_machine_name("not json") == "(deleted machine)"
        assert snapshot_machine_name('{"name": ""}') == "(deleted machine)"


# ---------------------------------------------------------------------------
# Run mode
# ---------------------------------------------------------------------------


class TestBuildCompareMatrix:
    def test_matches_rows_across_runs_by_test_case_id(self) -> None:
        rows = build_compare_matrix(
            [1, 2],
            [
                cell(1, test_case_id=10, sort_order=0, test_case_title="A"),
                cell(1, test_case_id=11, sort_order=1, test_case_title="B"),
                cell(2, test_case_id=11, sort_order=0, test_case_title="B"),
                cell(2, test_case_id=10, sort_order=1, test_case_title="A"),
            ],
        )

        assert len(rows) == 2
        assert [row.test_case_title for row in rows] == ["A", "B"]
        assert all(all(cell is not None for cell in row.cells) for row in rows)

    def test_leaves_an_empty_cell_for_test_cases_missing_from_a_run(self) -> None:
        rows = build_compare_matrix(
            [1, 2],
            [
                cell(1, test_case_id=10, sort_order=0, test_case_title="shared"),
                cell(1, test_case_id=12, sort_order=1, test_case_title="only in A"),
                cell(2, test_case_id=10, sort_order=0, test_case_title="shared"),
                cell(2, test_case_id=13, sort_order=1, test_case_title="only in B"),
            ],
        )

        assert [row.test_case_title for row in rows] == [
            "shared",
            "only in A",
            "only in B",
        ]
        assert rows[1].cells[1] is None
        assert rows[2].cells[0] is None

    def test_matches_a_deleted_test_case_against_identical_text(self) -> None:
        rows = build_compare_matrix(
            [1, 2],
            [
                cell(1, test_case_id=None, test_case_text="What is 2+2?"),
                cell(2, test_case_id=42, test_case_text="What is 2+2?"),
            ],
        )

        assert len(rows) == 1
        assert rows[0].cells[0] is not None
        assert rows[0].cells[1] is not None

    def test_matches_in_the_other_direction_too(self) -> None:
        rows = build_compare_matrix(
            [1, 2],
            [
                cell(1, test_case_id=42, test_case_text="What is 2+2?"),
                cell(2, test_case_id=None, test_case_text="What is 2+2?"),
            ],
        )

        assert len(rows) == 1
        assert len([c for c in rows[0].cells if c is not None]) == 2

    def test_ignores_insignificant_whitespace_when_matching_by_text(self) -> None:
        rows = build_compare_matrix(
            [1, 2],
            [
                cell(1, test_case_id=None, test_case_text="hello   world"),
                cell(2, test_case_id=None, test_case_text=" hello world\n"),
            ],
        )

        assert len(rows) == 1

    def test_keeps_different_ids_apart_even_with_identical_text(self) -> None:
        rows = build_compare_matrix(
            [1, 2],
            [
                cell(1, test_case_id=1, test_case_text="same"),
                cell(2, test_case_id=2, test_case_text="same"),
            ],
        )

        assert len(rows) == 2

    def test_produces_one_cell_slot_per_selected_run_in_column_order(self) -> None:
        rows = build_compare_matrix(
            [3, 1, 2], [cell(2, test_case_id=10), cell(3, test_case_id=10)]
        )

        assert len(rows[0].cells) == 3
        assert rows[0].cells[0] is not None and rows[0].cells[0].run_id == 3
        assert rows[0].cells[1] is None
        assert rows[0].cells[2] is not None and rows[0].cells[2].run_id == 2

    def test_drops_results_of_runs_that_are_not_selected(self) -> None:
        rows = build_compare_matrix(
            [1], [cell(1, test_case_id=10), cell(99, test_case_id=11)]
        )

        assert len(rows) == 1
        assert len(rows[0].cells) == 1

    def test_keeps_the_first_result_when_a_run_maps_two_onto_one_row(self) -> None:
        first = cell(1, test_case_id=10, sort_order=0, response_text="first")
        second = cell(1, test_case_id=10, sort_order=1, response_text="second")
        rows = build_compare_matrix([1], [second, first])

        assert len(rows) == 1
        assert rows[0].cells[0] is not None
        assert rows[0].cells[0].response_text == "first"

    def test_returns_no_rows_when_there_are_no_results(self) -> None:
        assert build_compare_matrix([1, 2], []) == []


class TestScopeIsolation:
    def test_never_matches_two_deleted_test_cases_by_text_across_workspaces(self) -> None:
        rows = build_compare_matrix(
            [1, 2],
            [
                cell(1, test_case_id=None, test_case_text="same text", scope_key="a"),
                cell(2, test_case_id=None, test_case_text="same text", scope_key="b"),
            ],
        )

        assert len(rows) == 2
        assert len([c for c in rows[0].cells if c is not None]) == 1
        assert len([c for c in rows[1].cells if c is not None]) == 1

    def test_still_matches_them_within_one_workspace(self) -> None:
        rows = build_compare_matrix(
            [1, 2],
            [
                cell(1, test_case_id=None, test_case_text="same text", scope_key="a"),
                cell(2, test_case_id=None, test_case_text="same text", scope_key="a"),
            ],
        )

        assert len(rows) == 1
        assert len([c for c in rows[0].cells if c is not None]) == 2


# ---------------------------------------------------------------------------
# Model mode
# ---------------------------------------------------------------------------


def model_cell(run_id: int, column_key: str, **overrides: Any) -> CompareCellView:
    return cell(run_id, column_key=column_key, **overrides)


def run(run_id: int, **overrides: Any) -> ModelColumnRun:
    defaults: dict[str, Any] = {
        "id": run_id,
        "machine_id": 1,
        "machine_name": "box",
        "model_id": "model-a",
        "created_at": at(1000),
        "archived": False,
    }
    return ModelColumnRun(**{**defaults, **overrides})


def result(run_id: int, **overrides: Any) -> ModelColumnResult:
    defaults: dict[str, Any] = {
        "run_id": run_id,
        "test_case_id": 1,
        "status": "ok",
        "rating": None,
        "tokens_per_sec": None,
    }
    return ModelColumnResult(**{**defaults, **overrides})


class TestBuildModelColumns:
    def test_groups_runs_of_the_same_model_and_machine_into_one_column(self) -> None:
        columns = build_model_columns(
            [run(1, created_at=at(1)), run(2, created_at=at(2))],
            [result(1), result(2, test_case_id=2)],
        )

        assert len(columns) == 1
        assert columns[0].key == "1|model-a"
        assert columns[0].run_count == 2
        assert columns[0].test_case_count == 2
        assert columns[0].latest_run_at == at(2)

    def test_keeps_the_same_model_on_two_machines_apart(self) -> None:
        columns = build_model_columns(
            [run(1, machine_id=1), run(2, machine_id=2, machine_name="other")],
            [result(1), result(2)],
        )

        assert [column.key for column in columns] == ["1|model-a", "2|model-a"]

    def test_ignores_archived_runs_and_runs_without_a_usable_result(self) -> None:
        columns = build_model_columns(
            [
                run(1, archived=True),
                run(2, model_id="model-b"),
                run(3, model_id="model-c"),
            ],
            [result(1), result(2, status="error"), result(3, status="ok")],
        )

        assert [column.key for column in columns] == ["1|model-c"]

    def test_averages_speed_and_tallies_ratings_over_usable_results_only(self) -> None:
        columns = build_model_columns(
            [run(1)],
            [
                result(1, test_case_id=1, rating="good", tokens_per_sec=10.0),
                result(1, test_case_id=2, rating="bad", tokens_per_sec=20.0),
                result(
                    1,
                    test_case_id=3,
                    status="error",
                    rating="bad",
                    tokens_per_sec=999.0,
                ),
            ],
        )

        assert columns[0].avg_rate == 15
        assert (columns[0].good, columns[0].meh, columns[0].bad) == (1, 0, 1)

    def test_names_a_column_after_its_most_recent_run_snapshot(self) -> None:
        columns = build_model_columns(
            [
                run(1, created_at=at(1), machine_name="old name"),
                run(2, created_at=at(2), machine_name="new name"),
            ],
            [result(1), result(2)],
        )

        assert columns[0].machine_name == "new name"


class TestBuildModelMatrix:
    def test_fills_each_cell_with_the_most_recent_result_of_that_model(self) -> None:
        matrix = build_model_matrix(
            ["1|a", "1|b"],
            [live_case(10)],
            [
                model_cell(
                    1, "1|a", test_case_id=10, run_created_at=at(1), response_text="old"
                ),
                model_cell(
                    2, "1|a", test_case_id=10, run_created_at=at(2), response_text="new"
                ),
                model_cell(3, "1|b", test_case_id=10, response_text="other model"),
            ],
        )

        assert len(matrix.rows) == 1
        cells = matrix.rows[0].cells
        assert cells[0] is not None and cells[0].response_text == "new"
        assert cells[1] is not None and cells[1].response_text == "other model"

    def test_falls_back_to_the_newest_usable_result_and_reports_the_skip(self) -> None:
        matrix = build_model_matrix(
            ["1|a"],
            [live_case(10)],
            [
                model_cell(
                    1,
                    "1|a",
                    test_case_id=10,
                    run_created_at=at(1),
                    response_text="good one",
                ),
                model_cell(
                    2,
                    "1|a",
                    test_case_id=10,
                    run_created_at=at(2),
                    status="error",
                    response_text=None,
                    error="connection refused",
                ),
            ],
        )

        shown = matrix.rows[0].cells[0]
        assert shown is not None
        assert shown.response_text == "good one"
        assert shown.superseded is not None
        assert (
            shown.superseded.run_id,
            shown.superseded.status,
            shown.superseded.created_at,
        ) == (2, "error", at(2))

    def test_leaves_no_superseded_marker_when_the_newest_is_the_one_shown(self) -> None:
        matrix = build_model_matrix(
            ["1|a"], [live_case(10)], [model_cell(1, "1|a", test_case_id=10)]
        )

        shown = matrix.rows[0].cells[0]
        assert shown is not None and shown.superseded is None

    def test_keeps_test_case_order_and_counts_the_unanswered(self) -> None:
        matrix = build_model_matrix(
            ["1|a"],
            [live_case(10), live_case(11), live_case(12)],
            [model_cell(1, "1|a", test_case_id=12)],
        )

        assert [row.test_case_id for row in matrix.rows] == [12]
        assert matrix.uncovered_test_cases == 2

    def test_uses_the_live_text_for_the_row_not_the_snapshot(self) -> None:
        matrix = build_model_matrix(
            ["1|a"],
            [live_case(10, text="live version")],
            [model_cell(1, "1|a", test_case_id=10, test_case_text="old version")],
        )

        assert matrix.rows[0].test_case_text == "live version"
        shown = matrix.rows[0].cells[0]
        assert shown is not None and shown.test_case_text == "old version"

    def test_ignores_unselected_columns_out_of_scope_and_deleted_test_cases(self) -> None:
        matrix = build_model_matrix(
            ["1|a"],
            [live_case(10)],
            [
                model_cell(1, "9|z", test_case_id=10),
                model_cell(2, "1|a", test_case_id=99),
                model_cell(3, "1|a", test_case_id=None, test_case_text="text 10"),
            ],
        )

        assert matrix.rows == []

    def test_produces_one_cell_slot_per_column_in_the_given_order(self) -> None:
        matrix = build_model_matrix(
            ["1|a", "1|b", "1|c"],
            [live_case(10)],
            [model_cell(7, "1|c", test_case_id=10)],
        )

        assert [
            None if cell is None else cell.run_id for cell in matrix.rows[0].cells
        ] == [None, None, 7]


# ---------------------------------------------------------------------------
# Drift
# ---------------------------------------------------------------------------


class TestDescribeRowDrift:
    def test_reports_nothing_when_the_cells_only_differ_by_model(self) -> None:
        assert (
            describe_row_drift(
                [
                    cell(1, test_case_text="same", effective_prompt_text="sys"),
                    cell(2, test_case_text="same", effective_prompt_text="sys"),
                ]
            )
            == []
        )

    def test_names_each_condition_that_is_not_held_constant(self) -> None:
        drift = describe_row_drift(
            [
                cell(
                    1,
                    test_case_text="a",
                    effective_prompt_text="sys",
                    run_params='{"temperature":0}',
                ),
                cell(
                    2,
                    test_case_text="b",
                    effective_prompt_text=None,
                    run_params='{"temperature":1}',
                ),
            ]
        )

        assert drift == ["test case text", "effective prompt", "params"]

    def test_ignores_whitespace_and_json_key_order(self) -> None:
        assert (
            describe_row_drift(
                [
                    cell(1, test_case_text="hello  world", run_params='{"a":1,"b":2}'),
                    cell(2, test_case_text=" hello world\n", run_params='{"b":2, "a":1}'),
                ]
            )
            == []
        )

    def test_reports_tools_but_max_turns_only_when_tools_actually_run(self) -> None:
        assert (
            describe_row_drift(
                [
                    cell(1, tool_mode="none", max_turns=6),
                    cell(2, tool_mode="none", max_turns=9),
                ]
            )
            == []
        )

        assert describe_row_drift(
            [
                cell(1, tool_mode="execute", max_turns=6, tools_snapshot='[{"a":1}]'),
                cell(2, tool_mode="execute", max_turns=9, tools_snapshot='[{"a":2}]'),
            ]
        ) == ["tools", "max turns"]

    def test_flags_a_test_case_edited_after_every_compared_run(self) -> None:
        assert describe_row_drift(
            [cell(1, test_case_text="v1"), cell(2, test_case_text="v1")], "v2"
        ) == ["test case edited since"]

    def test_does_not_flag_an_edit_when_the_live_text_matches(self) -> None:
        assert describe_row_drift([cell(1, test_case_text="v1")], "v1") == []

    def test_ignores_empty_columns(self) -> None:
        assert describe_row_drift([None, None]) == []
        assert describe_row_drift([None, cell(1)]) == []


class TestAnnotateDrift:
    def test_anchors_to_the_live_text_only_in_model_mode(self) -> None:
        # The same row, described twice: run mode has no live test case to
        # compare against, so only model mode can report an edit.
        rows = build_model_matrix(
            ["1|a"],
            [live_case(10, text="live")],
            [model_cell(1, "1|a", test_case_id=10, test_case_text="frozen")],
        ).rows

        assert annotate_drift(rows, anchored_to_live_test_case=True)[0].drift == [
            "test case edited since"
        ]
        assert annotate_drift(rows, anchored_to_live_test_case=False)[0].drift == []
