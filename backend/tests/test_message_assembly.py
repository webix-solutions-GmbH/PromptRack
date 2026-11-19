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

import json

import pytest

from app.services.message_assembly import (
    MAX_HISTORY_CONTENT_CHARS,
    MAX_HISTORY_TURNS,
    HistoryError,
    HistoryTurn,
    NoUserMessageError,
    assert_user_message,
    decode_history,
    encode_history,
    system_message,
    user_message,
    validate_history,
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


def _turns(*pairs: tuple[str, str]) -> list[dict[str, str]]:
    return [{"role": role, "content": content} for role, content in pairs]


GOOD = _turns(("user", "List open invoices."), ("assistant", "4711, 4712 (DE), 4713 (AT)."))


class TestValidateHistory:
    def test_none_and_empty_mean_no_history(self) -> None:
        assert validate_history(None) == []
        assert validate_history([]) == []

    def test_returns_history_turns_with_content_kept_verbatim(self) -> None:
        turns = _turns(("user", "  padded \r\n"), ("assistant", '{"json": true}'))
        assert validate_history(turns) == [
            HistoryTurn(role="user", content="  padded \r\n"),
            HistoryTurn(role="assistant", content='{"json": true}'),
        ]

    def test_accepts_history_turn_instances_too(self) -> None:
        turns = [HistoryTurn("user", "a"), HistoryTurn("assistant", "b")]
        assert validate_history(turns) == turns

    @pytest.mark.parametrize("role", ["system", "tool", "User", None, 3])
    def test_refuses_any_role_but_user_and_assistant(self, role: object) -> None:
        turns = [{"role": role, "content": "x"}, {"role": "assistant", "content": "y"}]
        with pytest.raises(HistoryError, match="History turn 1 has role"):
            validate_history(turns)

    @pytest.mark.parametrize("content", BLANKS)
    def test_refuses_blank_content(self, content: str | None) -> None:
        with pytest.raises(HistoryError, match=r"History turn 2 \(assistant\) has no content"):
            validate_history(
                [{"role": "user", "content": "x"}, {"role": "assistant", "content": content}]
            )

    def test_refuses_a_nul_byte(self) -> None:
        with pytest.raises(HistoryError, match="NUL"):
            validate_history(_turns(("user", "a\x00b"), ("assistant", "c")))

    def test_refuses_content_past_the_ceiling(self) -> None:
        too_long = "x" * (MAX_HISTORY_CONTENT_CHARS + 1)
        with pytest.raises(HistoryError, match="History turn 1 is"):
            validate_history(_turns(("user", too_long), ("assistant", "c")))
        # Exactly at the ceiling is fine.
        validate_history(_turns(("user", "x" * MAX_HISTORY_CONTENT_CHARS), ("assistant", "c")))

    def test_must_start_with_user(self) -> None:
        with pytest.raises(HistoryError, match='turn 1 is "assistant" but must be "user"'):
            validate_history(_turns(("assistant", "a"), ("user", "b"), ("assistant", "c")))

    def test_must_alternate(self) -> None:
        with pytest.raises(HistoryError, match='turn 2 is "user" but must be "assistant"'):
            validate_history(_turns(("user", "a"), ("user", "b"), ("assistant", "c")))

    def test_must_end_with_assistant_because_the_test_message_is_next(self) -> None:
        with pytest.raises(HistoryError, match='end with an "assistant" turn'):
            validate_history(_turns(("user", "a")))
        with pytest.raises(HistoryError, match='end with an "assistant" turn'):
            validate_history(_turns(("user", "a"), ("assistant", "b"), ("user", "c")))

    def test_caps_the_number_of_turns(self) -> None:
        at_limit = [
            {"role": "user" if index % 2 == 0 else "assistant", "content": str(index)}
            for index in range(MAX_HISTORY_TURNS)
        ]
        assert len(validate_history(at_limit)) == MAX_HISTORY_TURNS
        over = [*at_limit, {"role": "user", "content": "u"}, {"role": "assistant", "content": "a"}]
        with pytest.raises(HistoryError, match=f"the limit is {MAX_HISTORY_TURNS}"):
            validate_history(over)

    @pytest.mark.parametrize("value", ["[]", {"role": "user"}, 42])
    def test_refuses_something_that_is_not_a_list(self, value: object) -> None:
        with pytest.raises(HistoryError, match="must be a list"):
            validate_history(value)  # type: ignore[arg-type]

    def test_refusal_names_the_case_and_is_a_value_error(self) -> None:
        with pytest.raises(ValueError, match='^Test case "Follow-up": '):
            validate_history(_turns(("user", "a")), subject='Test case "Follow-up"')


class TestHistoryColumn:
    def test_round_trips_and_empty_is_null(self) -> None:
        turns = validate_history(GOOD)
        raw = encode_history(turns)
        assert raw is not None
        assert json.loads(raw) == GOOD
        assert decode_history(raw) == turns
        assert encode_history([]) is None
        assert decode_history(None) == []

    @pytest.mark.parametrize(
        "raw",
        ["not json", "{}", '"x"', '[{"role": "system", "content": "x"}, 3, {"role": "user"}]'],
    )
    def test_decoding_degrades_rather_than_raising(self, raw: str) -> None:
        assert decode_history(raw) == []
