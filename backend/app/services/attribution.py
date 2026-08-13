"""Which committed version a piece of draft text is — the pure rule.

``run_results.prompt_version_id`` is **attribution, not selection**: a run
always tests the prompt's current draft, and this is what records which
committed version that draft happened to be. There is no version picker at run
creation and there is not meant to be one — the column answers "what did this
result actually test", after the fact.

The whole rule is byte equality against the committed versions, newest first.
No whitespace normalisation: a trailing newline changes what the model
receives, so it is a different prompt, and a result labelled ``v4`` has to mean
the text of v4 exactly.

Kept free of SQLAlchemy so it can be read and tested without a database; the
repository is what fetches the versions (scoped) and hands them here.
"""

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class VersionRef:
    """Just enough of a committed version to compare a draft against.

    The repository projects ``prompt_versions`` into these rather than passing
    ORM rows, so the rule below has nothing to reach for beyond the three
    fields it actually compares.
    """

    id: int
    version: int
    content: str


def head_version(versions: Iterable[VersionRef]) -> VersionRef | None:
    """The highest-numbered commit, or ``None`` when nothing is committed yet.

    Sorted here rather than trusted from the caller: "newest" is a property of
    the version number, not of whatever order a query returned.
    """
    ordered = _newest_first(versions)
    return ordered[0] if ordered else None


def match_version(draft_text: str | None, versions: Iterable[VersionRef]) -> int | None:
    """The id of the version a draft is byte-identical to, or ``None``.

    ``None`` covers both "the draft is dirty" and "there was no prompt at all",
    which is exactly the distinction ``run_results.prompt_version_id`` refuses
    to make: either way the result tested text that no commit stands behind.

    Matching runs newest first, which only matters after a revert: commit v3
    restoring v1's text makes a run of that draft ``v3`` — the commit that is
    actually the head of the history, not the older identical one.

    Note that this is a laxer question than :func:`is_dirty`: a draft equal to
    some *older* version is still attributable (a clean working tree, just not
    at the head), while the editor calls it dirty.
    """
    if draft_text is None:
        return None
    for version in _newest_first(versions):
        if version.content == draft_text:
            return version.id
    return None


def is_dirty(draft_text: str, versions: Iterable[VersionRef]) -> bool:
    """Whether the draft differs from the head version.

    This is the editor's dirty indicator and, inverted, the commit refusal: a
    commit whose content is byte-identical to the head has nothing to freeze.

    A prompt with no versions at all is dirty. Nothing has ever been frozen, so
    there is nothing for the draft to be clean against, and the first commit
    must always be allowed.
    """
    head = head_version(versions)
    return head is None or head.content != draft_text


def _newest_first(versions: Iterable[VersionRef]) -> list[VersionRef]:
    return sorted(versions, key=lambda version: version.version, reverse=True)
