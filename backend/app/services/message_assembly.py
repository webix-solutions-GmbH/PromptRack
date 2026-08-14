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
"""


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
