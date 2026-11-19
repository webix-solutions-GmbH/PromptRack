"""What a hand-authored tool is — the one set of rules both write doors ask.

A manual tool can arrive through two doors: `app.api.toolsets` (the toolset
editor's JSON body) and `app.mcp.server` (an agent pushing another repository's
tool definitions in). Both write the same `tools` row, the model is offered
exactly what that row says, and `tools_snapshot` freezes it verbatim — so what a
valid name, description, schema or canned response is cannot be a per-door
decision. A rule that lived in one door only would let the other write a row the
first could never have written, which is the same class of bug
`app.services.documents.clean_document_path` and `normalize_markdown` exist to
prevent for a corpus.

Pure and database-free, like `documents.py`, `diff.py` and `attribution.py`.
Every function raises a plain `ValueError` with a readable sentence; each door
translates it into its own vocabulary (a 422 from a pydantic validator, an
`isError` content block over MCP).

Two refusals apply to every free-text field here, for the reasons
`normalize_markdown` spells out for a document:

* **A NUL byte is refused.** Postgres cannot hold one in a `text` column, so the
  alternative is not a tool description containing a NUL but an unhandled driver
  error — a 500 where every other refusal reads as a sentence.
* **`MAX_TOOL_TEXT_CHARS` is one ceiling** asked of the description and of every
  canned response. Real customer tools carry long, multi-line descriptions (25k
  characters is not unusual) and canned JSON answers twice that, so the ceiling
  sits far above those — it exists to refuse a pasted log file, not a long tool.
"""

from __future__ import annotations

import json
import re
from typing import Any

#: The function-name rule the OpenAI-compatible `tools` array imposes in
#: practice: several providers reject anything outside it with a 400 that would
#: surface as a failed *row*, i.e. as the model's fault.
_TOOL_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")

#: The no-argument schema, spelled out: several servers reject a bare `{}`.
EMPTY_PARAMETERS_JSON = '{"type": "object", "properties": {}}'

#: Per field — the description, and each canned response separately.
MAX_TOOL_TEXT_CHARS = 200_000

#: How many canned responses one tool may answer with, by call order. Past this a
#: suite is scripting a conversation rather than mocking a tool, and the last
#: response repeating is what keeps any longer loop answerable.
MAX_MOCK_RESPONSES = 20


def _refuse_unstorable(text: str, label: str) -> None:
    """The two refusals every free-text field shares — see the module docstring."""
    if "\x00" in text:
        raise ValueError(f"{label} may not contain NUL bytes.")
    if len(text) > MAX_TOOL_TEXT_CHARS:
        raise ValueError(
            f"{label} is {len(text):,} characters; the limit is {MAX_TOOL_TEXT_CHARS:,}."
        )


def clean_tool_name(value: str) -> str:
    """The function name the model calls the tool by, trimmed and checked."""
    cleaned = (value or "").strip()
    if not _TOOL_NAME_RE.match(cleaned):
        raise ValueError(
            "Tool name may only contain letters, digits, underscores and hyphens (max 64)."
        )
    return cleaned


def clean_tool_description(value: str | None) -> str | None:
    """Trimmed; blank is None (the definition then carries no description)."""
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    _refuse_unstorable(cleaned, "The description")
    return cleaned


def clean_parameters_json(value: str | dict[str, Any] | None) -> str:
    """The JSON Schema for the arguments, as the column stores it.

    Accepts the schema as a JSON string (the editor's textarea) or as an object
    (an MCP caller, which has the schema as data and should not have to
    double-encode it). It must be a JSON *object*; empty means "no arguments" and
    becomes :data:`EMPTY_PARAMETERS_JSON`. A string that parses is stored as
    typed, so the editor shows back the formatting its author chose.
    """
    if isinstance(value, dict):
        return json.dumps(value) if value else EMPTY_PARAMETERS_JSON
    cleaned = (value or "").strip()
    if not cleaned:
        return EMPTY_PARAMETERS_JSON
    _refuse_unstorable(cleaned, "The parameters schema")
    try:
        parsed = json.loads(cleaned)
    except ValueError as exc:
        raise ValueError("Parameters must be valid JSON.") from exc
    if not isinstance(parsed, dict):
        raise ValueError("Parameters must be a JSON object (a JSON Schema).")
    return cleaned


def clean_mock_responses(value: str | list[str] | None) -> list[str]:
    """A manual tool's canned responses, in the order calls receive them.

    The first call gets the first response, the second call the second, and the
    last one repeats for every call after that — so a single response is just the
    one-element case, and a string is accepted as exactly that.

    Each entry is trimmed. "No responses" may be said several ways — None, `[]`,
    or one blank string — and all of them mean the tool answers the model with
    "no canned response configured". A blank entry *inside* a list of several is
    refused instead: it would answer one particular call with nothing, and that is
    almost certainly a forgotten field rather than a test.
    """
    if value is None:
        return []
    if not isinstance(value, str | list | tuple):
        # An MCP caller may hand over a JSON object it meant as the answer; the
        # canned response is text sent verbatim, so it has to arrive as text.
        raise ValueError(
            "Canned responses must be a string or a list of strings — pass a JSON answer "
            "as a string."
        )
    entries = [value] if isinstance(value, str) else list(value)
    if len(entries) == 1 and isinstance(entries[0], str) and not entries[0].strip():
        return []
    if len(entries) > MAX_MOCK_RESPONSES:
        raise ValueError(
            f"A tool may have at most {MAX_MOCK_RESPONSES} canned responses "
            f"(got {len(entries)}); the last one repeats for every later call."
        )
    cleaned: list[str] = []
    for position, entry in enumerate(entries, start=1):
        if not isinstance(entry, str):
            raise ValueError(f"Canned response {position} must be a string.")
        text = entry.strip()
        if not text:
            raise ValueError(
                f"Canned response {position} is blank. Remove it, or give that call an answer."
            )
        _refuse_unstorable(text, f"Canned response {position}")
        cleaned.append(text)
    return cleaned


def encode_mock_responses(responses: list[str]) -> str | None:
    """The `tools.mock_responses` column: a JSON array, or NULL for none."""
    return json.dumps(responses) if responses else None


def decode_mock_responses(raw: str | None) -> list[str]:
    """The column read back. Degrades rather than raising, like every snapshot
    reader: a malformed value must not keep a toolset from rendering.
    """
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except ValueError:
        return []
    if not isinstance(parsed, list):
        return []
    return [entry for entry in parsed if isinstance(entry, str)]


def clean_mock_overrides(value: Any) -> dict[str, list[str]]:
    """A test case's per-tool canned-response overrides, `{tool name: [responses]}`.

    The *shape and storability* half of the rule, which needs no database:
    a JSON object keyed by tool name, each value a string or a list of strings
    run through :func:`clean_mock_responses` (so the call-order semantics, the
    blank refusal, `MAX_MOCK_RESPONSES` and the text ceiling are the tool's own).
    An override that cleans to *no* responses is refused rather than stored —
    "override with nothing" is not a test, and deleting the key is how an
    override is removed. `None` and `{}` both mean "no overrides".

    Whether each key names an enabled manual tool of the case's selected
    toolsets is the other half, and lives in
    `app.services.tool_config.assert_tool_config`, which can read the tools.
    """
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(
            "Mock overrides must be an object mapping a tool name to its canned response(s)."
        )
    cleaned: dict[str, list[str]] = {}
    for raw_name, responses in value.items():
        name = raw_name.strip() if isinstance(raw_name, str) else ""
        if not name:
            raise ValueError("A mock override needs a tool name.")
        if name in cleaned:
            raise ValueError(f'The mock override for "{name}" is given twice.')
        try:
            entries = clean_mock_responses(responses)
        except ValueError as exc:
            raise ValueError(f'The mock override for "{name}": {exc}') from exc
        if not entries:
            raise ValueError(
                f'The mock override for "{name}" has no canned response. Give it one, or '
                "remove the override."
            )
        cleaned[name] = entries
    return cleaned


def encode_mock_overrides(overrides: dict[str, list[str]]) -> str | None:
    """The `test_cases.mock_overrides` column: a JSON object, or NULL for none."""
    return json.dumps(overrides) if overrides else None


def decode_mock_overrides(raw: str | None) -> dict[str, list[str]]:
    """The column read back; degrades rather than raising, like
    :func:`decode_mock_responses`.
    """
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    decoded: dict[str, list[str]] = {}
    for name, responses in parsed.items():
        if isinstance(name, str) and isinstance(responses, list):
            entries = [entry for entry in responses if isinstance(entry, str)]
            if entries:
                decoded[name] = entries
    return decoded


__all__ = [
    "EMPTY_PARAMETERS_JSON",
    "MAX_MOCK_RESPONSES",
    "MAX_TOOL_TEXT_CHARS",
    "clean_mock_overrides",
    "clean_mock_responses",
    "clean_parameters_json",
    "clean_tool_description",
    "clean_tool_name",
    "decode_mock_overrides",
    "decode_mock_responses",
    "encode_mock_overrides",
    "encode_mock_responses",
]
