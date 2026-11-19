"""Turning a result row's three frozen texts into the two messages sent.

Replaces `app.services.effective_prompt`, which resolved a *derived* system
message (a prompt spliced with a test case's `custom_text` by a `mode`). There
is nothing left to derive: a prompt's draft text is sent verbatim, on the
channel its `kind` names, and the only assembly left is

* the **system message** — the system prompt's text, or nothing at all;
* the **user message** — the task prompt's text and the test case's own
  `content`, in that order, joined by a blank line.

The whitespace rule carries over verbatim from `resolve_effective_prompt`:
whitespace-only text is treated as absent on either side, so a prompt someone
blanked out is the same as no prompt rather than a stray "\\n\\n" on the wire.

Assembly happens at **execution** time, from the frozen columns, not at run
creation: keeping the three texts separate in `run_results` is what lets
`/results` report "the task prompt changed" instead of "the user message
changed".

Kept free of the database and of `app.repos`, the same split `app.services.diff`
and `app.services.attribution` draw: resolving a `prompt_id` into text is the
caller's job (a scoped read), and this module only ever sees the strings that
come out of it.

A test case may also carry a **conversation history**: earlier user/assistant
turns sent between the system message and the test's own user message, which
is how a follow-up question ("same list, only Germany") is tested as the
follow-up it is. The history is a fourth frozen part rather than something
spliced into the user message, so the two messages above are untouched by it;
:func:`validate_history` is the one rule for what a history may be, asked at
authoring time the same way :func:`assert_user_message` is.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from app.services.tool_authoring import MAX_TOOL_TEXT_CHARS


def _normalize(value: str | None) -> str | None:
    """Whitespace-only is absent. Carried over from `resolve_effective_prompt`."""
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def system_message(system_prompt_text: str | None) -> str | None:
    """The system message, or `None` when there is no system message at all.

    A blank system prompt is not an empty system message: several providers
    treat an empty system role as a real (and differently-behaving) turn, so
    the message is omitted entirely.
    """
    return _normalize(system_prompt_text)


def user_message(task_prompt_text: str | None, content: str | None) -> str:
    """`task + "\\n\\n" + content` when both are present, otherwise whichever is.

    Concatenation, not templating (decision 3 of the prompt-kinds spec): the
    data lands at the end, which is where these pipelines put it. Returns `""`
    when both parts are blank — a state `assert_user_message` below refuses at
    authoring time and again at run creation, so it can only be reached by a
    row damaged after the fact (a `SET NULL` from a deleted prompt).
    """
    task = _normalize(task_prompt_text)
    data = _normalize(content)

    if task and data:
        return f"{task}\n\n{data}"
    return task or data or ""


class NoUserMessageError(Exception):
    """A test case that would send an empty user message.

    Raised with the sentence a caller can show verbatim, naming the case that
    needs fixing — the same shape as
    `app.services.tool_config.ToolConfigError`, and for the same reason: a
    request with no user message measures nothing, and a case that reaches run
    creation in that state was authorable in it.
    """


def assert_user_message(
    task_prompt_text: str | None, content: str | None, *, subject: str
) -> None:
    """Refuses a test case with neither a task prompt nor content.

    The one shared guard, called at authoring time (`app.repos.test_cases`, on
    create and on the *merged* post-patch state) and again at run creation
    (`app.services.run_create`), exactly the way `assert_tool_config` is — so a
    case saved through the API can never be one a run would later refuse.

    Expressed as "would `user_message` produce anything", so the rule and the
    assembly can never disagree about what blank means.
    """
    if user_message(task_prompt_text, content):
        return
    raise NoUserMessageError(
        f"{subject} has no user message: give it content, or a task prompt with text in it."
    )


# ---------------------------------------------------------------------------
# Conversation history
# ---------------------------------------------------------------------------

HistoryRole = Literal["user", "assistant"]

#: The roles a history turn may take. No `system` (that is the system prompt's
#: slot) and no `tool` (a tool result without the assistant call that asked
#: for it is not a conversation any provider accepts).
HISTORY_ROLES: tuple[HistoryRole, ...] = ("user", "assistant")

#: Past this a case is scripting a transcript rather than asking a follow-up.
MAX_HISTORY_TURNS = 50

#: Per turn — the same ceiling every other hand-authored text sent to the model
#: shares (`app.services.tool_authoring`).
MAX_HISTORY_CONTENT_CHARS = MAX_TOOL_TEXT_CHARS


@dataclass(frozen=True)
class HistoryTurn:
    """One earlier turn of the conversation, sent before the test's own user
    message. The wire shape is exactly `{"role": ..., "content": ...}`.
    """

    role: HistoryRole
    content: str

    def to_json(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


class HistoryError(ValueError):
    """A conversation history that cannot be sent, with the sentence a caller
    can show verbatim.
    """


def _turn_parts(turn: Any) -> tuple[Any, Any]:
    if isinstance(turn, HistoryTurn):
        return turn.role, turn.content
    if isinstance(turn, Mapping):
        return turn.get("role"), turn.get("content")
    role = getattr(turn, "role", None)
    content = getattr(turn, "content", None)
    if role is None and content is None:
        return None, None
    return role, content


def validate_history(
    turns: Sequence[Any] | None, *, subject: str | None = None
) -> list[HistoryTurn]:
    """The one rule for a conversation history; returns it as `HistoryTurn`s.

    * Roles are `user` and `assistant` only.
    * Content is stored **verbatim** — it is what goes on the wire — and only
      refused when blank or when it holds a NUL byte (which Postgres cannot
      store), or past :data:`MAX_HISTORY_CONTENT_CHARS`.
    * It starts with `user`, strictly alternates, and **ends with `assistant`**:
      the test's own user message is the next turn, and several chat templates
      (Mistral, Gemma) answer a non-alternating conversation with a 400 that
      would read in `/results` as the model's failure.
    * At most :data:`MAX_HISTORY_TURNS` turns.

    `None` and `[]` both mean "no history". Accepts `HistoryTurn`s or plain
    `{"role", "content"}` mappings (an MCP caller's JSON). `subject` (e.g.
    `Test case "Follow-up"`) prefixes the message so a refusal names the case.
    """
    prefix = f"{subject}: " if subject else ""

    def refuse(message: str) -> HistoryError:
        return HistoryError(f"{prefix}{message}")

    if turns is None:
        return []
    if isinstance(turns, str | bytes) or not isinstance(turns, Sequence):
        raise refuse(
            'The conversation history must be a list of {"role", "content"} turns.'
        )
    if len(turns) > MAX_HISTORY_TURNS:
        raise refuse(
            f"The conversation history has {len(turns)} turns; the limit is "
            f"{MAX_HISTORY_TURNS}."
        )

    cleaned: list[HistoryTurn] = []
    for position, turn in enumerate(turns, start=1):
        role, content = _turn_parts(turn)
        if role not in HISTORY_ROLES:
            raise refuse(
                f'History turn {position} has role {json.dumps(role)}; only "user" and '
                '"assistant" are allowed.'
            )
        if not isinstance(content, str) or not content.strip():
            raise refuse(f"History turn {position} ({role}) has no content.")
        if "\x00" in content:
            raise refuse(f"History turn {position} may not contain NUL bytes.")
        if len(content) > MAX_HISTORY_CONTENT_CHARS:
            raise refuse(
                f"History turn {position} is {len(content):,} characters; the limit is "
                f"{MAX_HISTORY_CONTENT_CHARS:,}."
            )
        expected: HistoryRole = "user" if position % 2 == 1 else "assistant"
        if role != expected:
            raise refuse(
                f'History turn {position} is "{role}" but must be "{expected}": the '
                'history starts with "user" and alternates user/assistant.'
            )
        cleaned.append(HistoryTurn(role=role, content=content))

    if cleaned and cleaned[-1].role != "assistant":
        raise refuse(
            'The conversation history must end with an "assistant" turn — the test\'s own '
            "user message is the next one."
        )
    return cleaned


def encode_history(turns: Sequence[HistoryTurn]) -> str | None:
    """The `history` column: a JSON array, or NULL for none."""
    return json.dumps([turn.to_json() for turn in turns]) if turns else None


def decode_history(raw: str | None) -> list[HistoryTurn]:
    """The column read back. Degrades rather than raising, like every snapshot
    reader: a malformed value must never keep a run from rendering — an entry
    that is not a `{role, content}` pair of strings is skipped.
    """
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except ValueError:
        return []
    if not isinstance(parsed, list):
        return []
    return [
        HistoryTurn(role=entry["role"], content=entry["content"])
        for entry in parsed
        if isinstance(entry, dict)
        and entry.get("role") in HISTORY_ROLES
        and isinstance(entry.get("content"), str)
    ]
