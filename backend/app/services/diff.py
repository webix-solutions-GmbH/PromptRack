"""Unified text diffs between two prompt texts — stdlib `difflib`, no new
dependency (the plan rules out a Monaco/`vue-diff` dependency on the frontend
side too; the backend renders the diff, the frontend only displays it).

Kept free of the database and of `app.repos`, the same split
`app.services.attribution` makes for the version-matching rule: resolving a
`from`/`to` reference (a version id or the literal `"draft"`) into plain text
is `app.api.prompts`'s job, and this module only ever sees the two strings
that come out of that.
"""

import difflib


def unified_diff(before: str, after: str, *, from_label: str, to_label: str) -> list[str]:
    """A unified diff, one line per list entry.

    `lineterm=""` keeps `difflib`'s own `---`/`+++`/`@@` header lines free of
    the trailing newline it would otherwise append to every line — content
    lines never carried one in the first place, since the inputs are split
    with `splitlines()` rather than `splitlines(keepends=True)`. The result is
    a clean list of strings, exactly what a client renders one row at a time.
    """
    return list(
        difflib.unified_diff(
            before.splitlines(),
            after.splitlines(),
            fromfile=from_label,
            tofile=to_label,
            lineterm="",
        )
    )
