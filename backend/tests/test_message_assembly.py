"""`app.services.message_assembly` — pure, no database.

Replaces `test_effective_prompt.py`, which pinned the old `mode`/`custom_text`
splice. Every whitespace case there survives here, because the rule it pinned
is the one that carried over verbatim: **whitespace-only is absent**, on either
side, in either message.

What is new is that there are now two messages rather than one derived string,
and the parts land on *different channels* — so the interesting cases are the
asymmetric ones: a blank system prompt means no system message at all, while a
blank task prompt still leaves a user message as long as the case has content.
"""

from __future__ import annotations

import pytest

from app.services.message_assembly import (
    NoUserMessageError,
    assert_user_message,
    system_message,
    user_message,
)

BLANKS = (None, "", "   ", "\n\n", "\t", " \n\t ")


class TestSystemMessage:
    def test_returns_the_prompt_text_when_there_is_one(self) -> None:
        assert system_message("You are a helpful assistant.") == "You are a helpful assistant."

    def test_trims_surrounding_whitespace(self) -> None:
        assert system_message("  You are terse.  \n") == "You are terse."

    @pytest.mark.parametrize("blank", BLANKS)
    def test_a_blank_prompt_is_no_system_message_at_all(self, blank: str | None) -> None:
        # Not `""`: several providers treat an empty system role as a real turn
        # that behaves differently from having none, so the message is omitted.
        assert system_message(blank) is None


class TestUserMessage:
    def test_joins_both_parts_with_a_blank_line_task_prompt_first(self) -> None:
        assert (
            user_message("Extract the PO number.", "Invoice #4711, PO P00018")
            == "Extract the PO number.\n\nInvoice #4711, PO P00018"
        )

    def test_returns_only_the_task_prompt_when_the_case_has_no_content(self) -> None:
        # "This prompt takes no input" — the task prompt is the whole message.
        assert user_message("Summarise yesterday's tickets.", None) == (
            "Summarise yesterday's tickets."
        )

    def test_returns_only_the_content_when_there_is_no_task_prompt(self) -> None:
        assert user_message(None, "What is 2+2?") == "What is 2+2?"

    def test_trims_each_part_before_joining(self) -> None:
        assert user_message("  task  ", "  data  ") == "task\n\ndata"

    @pytest.mark.parametrize("blank", BLANKS)
    def test_treats_a_whitespace_only_task_prompt_as_absent(self, blank: str | None) -> None:
        # No stray "\n\n" at the head of the message.
        assert user_message(blank, "What is 2+2?") == "What is 2+2?"

    @pytest.mark.parametrize("blank", BLANKS)
    def test_treats_whitespace_only_content_as_absent(self, blank: str | None) -> None:
        assert user_message("Summarise yesterday's tickets.", blank) == (
            "Summarise yesterday's tickets."
        )

    def test_both_blank_is_the_empty_string(self) -> None:
        # The one state `assert_user_message` exists to keep unreachable; it is
        # `""` rather than `None` so the executor's message list stays typed.
        assert user_message(None, None) == ""
        assert user_message("  ", "\n\t") == ""

    def test_the_separator_is_exactly_one_blank_line(self) -> None:
        # Pinned because it is wire format: the model sees this byte for byte.
        assert user_message("a", "b") == "a\n\nb"

    def test_does_not_normalise_whitespace_inside_a_part(self) -> None:
        assert user_message("line 1\n  line 2", "a  b") == "line 1\n  line 2\n\na  b"


class TestAssertUserMessage:
    def test_accepts_a_case_with_only_content(self) -> None:
        assert_user_message(None, "What is 2+2?", subject="Test case \"x\"")

    def test_accepts_a_case_with_only_a_task_prompt(self) -> None:
        assert_user_message("Summarise the tickets.", None, subject="Test case \"x\"")

    def test_accepts_a_case_with_both(self) -> None:
        assert_user_message("task", "data", subject="Test case \"x\"")

    def test_refuses_a_case_with_neither(self) -> None:
        with pytest.raises(NoUserMessageError, match="no user message"):
            assert_user_message(None, None, subject='Test case "Invoice 1"')

    def test_refuses_a_case_whose_parts_are_only_whitespace(self) -> None:
        with pytest.raises(NoUserMessageError):
            assert_user_message("   ", "\n\n", subject='Test case "Invoice 1"')

    def test_the_refusal_names_the_case_that_needs_fixing(self) -> None:
        with pytest.raises(NoUserMessageError, match='Test case "Invoice 1"'):
            assert_user_message(None, None, subject='Test case "Invoice 1"')

    def test_agrees_with_user_message_on_every_blank_combination(self) -> None:
        # The guard is expressed as "would `user_message` produce anything", and
        # this is the property that says so: the rule and the assembly cannot
        # drift into disagreeing about what blank means.
        for task in BLANKS:
            for content in BLANKS:
                with pytest.raises(NoUserMessageError):
                    assert_user_message(task, content, subject="x")
        for task in BLANKS:
            assert_user_message(task, "data", subject="x")
            assert_user_message("task", task, subject="x")
