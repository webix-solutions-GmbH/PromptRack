"""`app.services.diff.unified_diff` — pure text diffing, no database."""

from __future__ import annotations

from app.services.diff import unified_diff


def test_identical_text_produces_no_diff():
    diff = unified_diff("Say hi.\n", "Say hi.\n", from_label="v1", to_label="draft")

    assert diff == []


def test_a_changed_line_is_reported_with_the_given_labels():
    diff = unified_diff(
        "You are a helpful assistant.\nBe concise.\n",
        "You are a helpful assistant.\nBe thorough.\n",
        from_label="v1",
        to_label="v2",
    )

    assert diff[0] == "--- v1"
    assert diff[1] == "+++ v2"
    assert "-Be concise." in diff
    assert "+Be thorough." in diff


def test_lines_carry_no_trailing_newline_characters():
    diff = unified_diff("A\n", "B\n", from_label="draft", to_label="v3")

    assert all(not line.endswith("\n") for line in diff)


def test_an_added_line_is_prefixed_with_a_plus():
    diff = unified_diff("A\n", "A\nB\n", from_label="v1", to_label="draft")

    assert "+B" in diff
    # Nothing was removed, so no `-`-prefixed content line should appear.
    assert not any(line.startswith("-") and not line.startswith("---") for line in diff)
