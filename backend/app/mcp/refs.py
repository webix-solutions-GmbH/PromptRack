"""Referring to a row by name, and refusing the ambiguous ones.

Ported from `git show master:src/lib/mcp/args.ts`, minus everything the Python
SDK already does: FastMCP validates and coerces a tool's arguments against the
signature's type hints, so the old `requireString` / `optionalInteger` family
has no work left to do. What does *not* come for free is the part that made the
old surface pleasant to drive from an agent:

* **Everything relatable by name is** — a group, a prompt, a toolset or an
  endpoint may be named instead of numbered, because a client that has just
  created a group knows its name and would otherwise have to look the id up
  again. A numeric string is always an id (`"12"` is never a sensible name, and
  treating it as one would silently create a second group called "12").
* **An ambiguous name is refused, never guessed**, and a miss reports what was
  available — which doubles as the discovery path for a caller that guessed
  wrong.

Resolution runs over rows a *scoped* read already returned, which is what keeps
a name from ever reaching another workspace's row: the candidate list is the
scope's own, so the "Known: …" hint can only ever name rows this caller may
see.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol


class McpToolError(Exception):
    """A refusal the calling model should read as the tool's answer.

    The SDK turns any exception raised inside a tool into `isError` content
    rather than a JSON-RPC error, which is the behaviour the old hand-rolled
    server implemented by hand and the same reasoning `tool_loop` uses when it
    feeds a tool failure back to the model: the caller reads the message and
    fixes its arguments instead of retrying blindly.
    """


class Named(Protocol):
    """Anything with an id and a name — every row a `RowRef` can point at."""

    @property
    def id(self) -> int: ...

    @property
    def name(self) -> str: ...


@dataclass(frozen=True)
class RowRef:
    """A reference to one row, by id or by name. Exactly one is set."""

    row_id: int | None
    name: str | None

    @classmethod
    def by_id(cls, row_id: int) -> RowRef:
        return cls(row_id, None)

    @classmethod
    def by_name(cls, name: str) -> RowRef:
        return cls(None, name)

    def __str__(self) -> str:
        return str(self.row_id) if self.row_id is not None else f'"{self.name}"'


def parse_row_ref(value: Any, label: str) -> RowRef:
    """Reads one `name or id` argument.

    `bool` is excluded explicitly: it is an `int` in Python, and `True` would
    otherwise resolve as id 1.
    """
    if isinstance(value, bool):
        raise McpToolError(f"{label} must be a name (string) or an id (number).")
    if isinstance(value, int):
        return RowRef.by_id(value)
    if isinstance(value, float):
        if not value.is_integer():
            raise McpToolError(f"{label} must be a name or a whole-number id.")
        return RowRef.by_id(int(value))
    if isinstance(value, str):
        trimmed = value.strip()
        if not trimmed:
            raise McpToolError(f"{label} must not be empty.")
        if trimmed.isdigit():
            return RowRef.by_id(int(trimmed))
        return RowRef.by_name(trimmed)
    raise McpToolError(f"{label} must be a name (string) or an id (number).")


def optional_row_ref(value: Any, label: str) -> RowRef | None:
    """The same, tolerating an omitted argument."""
    if value is None:
        return None
    return parse_row_ref(value, label)


def parse_row_refs(value: Any, label: str) -> list[RowRef]:
    """A list of refs; a single value is accepted as a one-element list."""
    if value is None:
        return []
    entries = value if isinstance(value, list) else [value]
    return [parse_row_ref(entry, f'each entry of "{label}"') for entry in entries]


def resolve_row_ref[T: Named](ref: RowRef, rows: Sequence[T], label: str) -> T:
    """Finds a ref among rows, reporting what was available when it misses."""
    if ref.row_id is not None:
        for row in rows:
            if row.id == ref.row_id:
                return row
        raise McpToolError(f"No {label} with id {ref.row_id}.")

    wanted = (ref.name or "").strip().lower()
    matches = [row for row in rows if row.name.strip().lower() == wanted]
    if not matches:
        known = ", ".join(f"{row.name} ({row.id})" for row in rows)
        suffix = f" Known: {known}." if known else ""
        raise McpToolError(f'No {label} named "{ref.name}".{suffix}')
    if len(matches) > 1:
        ids = ", ".join(str(row.id) for row in matches)
        raise McpToolError(
            f'Several {label} entries are named "{ref.name}" (ids {ids}). Use the id.'
        )
    return matches[0]


def has_key(args: Mapping[str, Any] | None, key: str) -> bool:
    """True when the caller mentioned the key at all — `null` counts.

    What patch semantics rest on: `update_test_case` may only touch the fields
    actually named, and `set_rating` must leave an existing note alone unless
    the caller said something about it. The SDK hands a tool its *validated*
    arguments, where an omitted optional and an explicit `null` are both
    `None`, so presence is read off the raw `tools/call` arguments instead (see
    `app.mcp.server.raw_arguments`).
    """
    return args is not None and key in args


@dataclass(frozen=True)
class Truncated:
    """Shortened text, and whether shortening happened."""

    text: str | None
    truncated: bool


def truncate(value: str | None, limit: int) -> Truncated:
    """Shortens long text for list-style responses, marking that it was cut.

    A limit of 0 (or less) means no limit — the same convention the old tools
    advertised, so `max_content_chars: 0` really does return everything.
    """
    if value is None:
        return Truncated(None, False)
    if limit <= 0 or len(value) <= limit:
        return Truncated(value, False)
    return Truncated(f"{value[:limit]}…", True)
