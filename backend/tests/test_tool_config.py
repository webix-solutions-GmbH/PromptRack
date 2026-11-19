"""The pure half of `app.services.tool_config` — no database.

`assert_tool_config` itself needs a scoped session (it resolves toolset ids
into their live tools), so it is exercised end to end in
`tests/integration/test_test_cases_api.py` instead. `normalize_max_turns` is
adapted for an already-`int` wire type (see the module docstring).
"""

from __future__ import annotations

import pytest

from app.services.tool_config import (
    DEFAULT_MAX_TURNS,
    MAX_TURNS_LIMIT,
    OfferedTool,
    ToolConfigError,
    assert_mock_overrides,
    collect_tool_name_collisions,
    normalize_max_turns,
)


class TestCollectToolNameCollisions:
    def test_returns_nothing_when_every_name_is_unique(self):
        assert collect_tool_name_collisions([OfferedTool("a"), OfferedTool("b")]) == []

    def test_reports_each_duplicated_name_once_sorted(self):
        assert collect_tool_name_collisions(
            [
                OfferedTool("search"),
                OfferedTool("search"),
                OfferedTool("search"),
                OfferedTool("create"),
                OfferedTool("create"),
            ]
        ) == ["create", "search"]

    def test_ignores_disabled_tools_they_are_never_sent_so_cannot_collide(self):
        assert (
            collect_tool_name_collisions(
                [OfferedTool("search"), OfferedTool("search", enabled=False)]
            )
            == []
        )


class TestNormalizeMaxTurns:
    def test_defaults_when_unset(self):
        assert normalize_max_turns(None) == DEFAULT_MAX_TURNS

    def test_clamps_to_at_least_one_turn(self):
        assert normalize_max_turns(0) == 1
        assert normalize_max_turns(-5) == 1

    def test_clamps_to_the_hard_limit(self):
        assert normalize_max_turns(1000) == MAX_TURNS_LIMIT

    def test_leaves_an_in_range_value_untouched(self):
        assert normalize_max_turns(6) == 6
        assert normalize_max_turns(1) == 1
        assert normalize_max_turns(MAX_TURNS_LIMIT) == MAX_TURNS_LIMIT


class TestAssertMockOverrides:
    """The pure half of the override check `assert_tool_config` runs once it
    has read the selected toolsets' tools. The database half (which tools are
    eligible) is covered in `tests/integration/test_test_cases_api.py`.
    """

    def test_accepts_an_override_of_an_eligible_tool(self):
        assert_mock_overrides({"lookup": ["a", "b"]}, ["lookup", "search"], subject="Case")

    def test_refuses_a_name_that_is_not_eligible_listing_the_ones_that_are(self):
        with pytest.raises(ToolConfigError) as raised:
            assert_mock_overrides({"lookpu": ["a"]}, ["lookup", "search"], subject='Test case "X"')
        message = str(raised.value)
        assert message.startswith('Test case "X" overrides the canned response of "lookpu"')
        assert "lookup, search" in message

    def test_says_none_when_nothing_can_be_overridden(self):
        with pytest.raises(ToolConfigError, match="overridden: none"):
            assert_mock_overrides({"lookup": ["a"]}, [], subject="Case")

    def test_values_go_through_the_tools_own_response_rules(self):
        with pytest.raises(ToolConfigError, match='override for "lookup": Canned response 2'):
            assert_mock_overrides({"lookup": ["a", "  "]}, ["lookup"], subject="Case")
        with pytest.raises(ToolConfigError, match="has no canned response"):
            assert_mock_overrides({"lookup": []}, ["lookup"], subject="Case")
