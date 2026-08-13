"""The pure half of `app.services.tool_config` — no database.

`assert_tool_config` itself needs a scoped session (it resolves toolset ids
into their live tools), so it is exercised end to end in
`tests/integration/test_test_cases_api.py` instead. `collect_tool_name_collisions`
is ported from `git show master:src/lib/tools.test.ts`'s
`collectToolNameCollisions` cases; `normalize_max_turns` from the same file's
`normalizeMaxTurns` cases, adapted for an already-`int` wire type (see the
module docstring).
"""

from __future__ import annotations

from app.services.tool_config import (
    DEFAULT_MAX_TURNS,
    MAX_TURNS_LIMIT,
    OfferedTool,
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
