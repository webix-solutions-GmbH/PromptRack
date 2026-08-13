"""`app.services.effective_prompt.resolve_effective_prompt` — pure, no
database. Ported from `git show master:src/lib/system-prompt.test.ts`.
"""

from __future__ import annotations

from app.services.effective_prompt import resolve_effective_prompt


class TestOverrideMode:
    def test_returns_the_custom_text_when_present(self):
        assert (
            resolve_effective_prompt("base content", "override", "custom override")
            == "custom override"
        )

    def test_ignores_base_content_entirely(self):
        assert (
            resolve_effective_prompt("this should never appear", "override", "only this")
            == "only this"
        )

    def test_returns_none_when_custom_text_is_absent(self):
        assert resolve_effective_prompt("base content", "override", None) is None

    def test_returns_none_when_custom_text_is_whitespace_only(self):
        assert resolve_effective_prompt("base content", "override", "   \n\t  ") is None

    def test_returns_none_when_both_base_and_custom_are_absent(self):
        assert resolve_effective_prompt(None, "override", None) is None


class TestAppendMode:
    def test_joins_base_and_custom_with_a_blank_line_when_both_are_present(self):
        assert (
            resolve_effective_prompt(
                "You are a helpful assistant.", "append", "Always answer in French."
            )
            == "You are a helpful assistant.\n\nAlways answer in French."
        )

    def test_returns_only_base_content_when_custom_text_is_absent(self):
        assert (
            resolve_effective_prompt("You are a helpful assistant.", "append", None)
            == "You are a helpful assistant."
        )

    def test_returns_only_custom_text_when_base_content_is_absent(self):
        assert (
            resolve_effective_prompt(None, "append", "Always answer in French.")
            == "Always answer in French."
        )

    def test_returns_none_when_neither_base_nor_custom_is_present(self):
        assert resolve_effective_prompt(None, "append", None) is None

    def test_treats_whitespace_only_base_content_as_absent(self):
        assert (
            resolve_effective_prompt("   ", "append", "Always answer in French.")
            == "Always answer in French."
        )

    def test_treats_whitespace_only_custom_text_as_absent(self):
        assert (
            resolve_effective_prompt("You are a helpful assistant.", "append", "\n\n  ")
            == "You are a helpful assistant."
        )

    def test_returns_none_when_both_base_and_custom_are_whitespace_only(self):
        assert resolve_effective_prompt("  ", "append", "\t") is None

    def test_trims_surrounding_whitespace_from_each_part_before_joining(self):
        assert resolve_effective_prompt("  base  ", "append", "  custom  ") == "base\n\ncustom"
