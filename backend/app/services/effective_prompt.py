"""Effective system prompt resolution — pure, ported from
`git show master:src/lib/system-prompt.ts`.

A test case references an optional base prompt plus a mode: `append` sends
`base + "\\n\\n" + custom`, `override` sends the custom text alone. Whitespace-
only text is treated as absent on either side, and an empty result means no
system message at all — the same rule run creation will use to freeze the
snapshot (Task 4.3) and the one `POST /api/test-cases/effective-prompt`
previews live, on every keystroke, without writing anything.

Kept free of the database and of `app.repos`, the same split
`app.services.diff` and `app.services.attribution` draw: resolving a
`prompt_id` into text is the caller's job (a scoped read), and this module
only ever sees the two strings that come out of it.
"""

from app.models import PromptMode


def _normalize(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def resolve_effective_prompt(
    base_content: str | None, mode: PromptMode, custom_text: str | None
) -> str | None:
    """mode='override': the custom text alone (base is ignored).
    mode='append': base + blank line + custom when both are present,
    otherwise whichever single part is present. `None` when nothing remains.
    """
    base = _normalize(base_content)
    custom = _normalize(custom_text)

    if mode == "override":
        return custom

    if base and custom:
        return f"{base}\n\n{custom}"
    return base or custom
