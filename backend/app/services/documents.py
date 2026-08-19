"""The three document tools: their definitions, and every text rule behind them.

A `documents` toolset offers a model exactly three functions — `list_documents`,
`search_documents` and `read_document` — and what they answer with is decided
here. Everything in this module is **pure**: no session, no `app.repos`, no
Postgres, the same split :mod:`app.services.attribution` and
:mod:`app.services.diff` draw. The queries live in :mod:`app.repos.documents`
(which projects its rows into the shapes below, exactly as
`app.repos.prompt_versions` projects into `VersionRef`), and the routing of a
call to a corpus lives in :mod:`app.services.executor`, which is the only layer
holding both a session and a scope.

Three rules are worth reading before changing anything here, because each one is
what makes a *retrieval* measurement mean something:

* **The tool definitions are fixed.** They are synthesized into real `tools`
  rows (`app.repos.toolsets.sync_document_tools`) rather than authored, so every
  documents toolset offers the same three functions with the same schemas — a
  model that navigated one customer's corpus badly cannot be excused by a
  differently-worded description. The descriptions are part of the measurement
  and must change deliberately, for every corpus at once.
* **Character offsets, not tokens or lines.** `read_document` windows the
  markdown by characters because that is the only unit the model, the corpus and
  this code can all agree on without a tokenizer, and it is the unit the reply
  reports back so the model can ask for the next window itself.
* **A snippet without its heading is nearly useless.** A search hit reads as
  "somewhere in refunds.md"; the same hit under *"## Refunds after 30 days"*
  tells the model whether to open the document at all. Resolving that heading is
  :func:`nearest_heading`, and it is why the repository hands the matched
  document's full text over with the `ts_headline` fragment.

Nothing here raises for bad input. A path that matches nothing, an offset past
the end of a document, a query that finds nothing — all of them are answers the
model reads and reacts to, which is the app's standing rule that a tool failure
is data and never a failed row.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

# ---------------------------------------------------------------------------
# The three tools
# ---------------------------------------------------------------------------

#: The function names the model sees. Also the `tools.name` values of the three
#: synthesized rows, which is what makes `assert_tool_config`'s collision check
#: cover them for free: a manual toolset in the same test case that defines its
#: own `read_document` is refused at authoring time like any other collision.
LIST_DOCUMENTS = "list_documents"
SEARCH_DOCUMENTS = "search_documents"
READ_DOCUMENT = "read_document"

#: Snippets per `search_documents` call: the default, and the ceiling a larger
#: request is clamped to. Clamped rather than refused, for the same reason
#: `normalize_max_turns` clamps — an over-eager argument should cost the model a
#: smaller answer, not a turn spent reading an error.
DEFAULT_SEARCH_LIMIT = 5
MAX_SEARCH_LIMIT = 20

#: `read_document`'s window, in characters. The default is a comfortable few
#: pages; the ceiling exists so one call cannot bury the rest of the
#: conversation in a 400 kB handbook.
DEFAULT_READ_LIMIT = 6000
MAX_READ_LIMIT = 40000


@dataclass(frozen=True)
class DocumentToolDefinition:
    """One synthesized tool, in the three fields a `tools` row needs.

    Not a wire `ToolDefinition`: the row is what gets frozen into a run, and
    `app.services.run_create` already knows how to turn a row into the
    OpenAI-compatible entry. Keeping this shape row-shaped is what lets the
    three tools travel through snapshotting, `tools_snapshot`, the toolset
    detail UI and the `enabled` flag without any of them learning a new case.
    """

    name: str
    description: str
    parameters: dict[str, Any]

    @property
    def parameters_json(self) -> str:
        """The `tools.parameters_json` column.

        Serialized from a literal dict in a fixed key order, so re-asserting the
        three rows writes the same bytes every time and a sync is genuinely
        idempotent rather than bumping `updated_at` on every call.
        """
        return json.dumps(self.parameters)


def _string(description: str) -> dict[str, Any]:
    return {"type": "string", "description": description}


DOCUMENT_TOOLS: tuple[DocumentToolDefinition, ...] = (
    DocumentToolDefinition(
        name=LIST_DOCUMENTS,
        description=(
            "List every document available to you, with its path, title and size in "
            "characters. Call this first when you do not yet know what the corpus "
            "contains. Takes no arguments."
        ),
        parameters={"type": "object", "properties": {}},
    ),
    DocumentToolDefinition(
        name=SEARCH_DOCUMENTS,
        description=(
            "Full-text search across every document. Returns the best matching "
            "passages, each with the path of the document it came from and the "
            "markdown heading it sits under, so you can decide what to read. "
            "Search for the words you expect in the documentation, not for a "
            "question."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": _string(
                    "Words to look for. Quoted phrases and a leading - to exclude a "
                    "word are supported."
                ),
                "limit": {
                    "type": "integer",
                    "description": (
                        f"How many passages to return (1-{MAX_SEARCH_LIMIT}, "
                        f"default {DEFAULT_SEARCH_LIMIT})."
                    ),
                    "minimum": 1,
                    "maximum": MAX_SEARCH_LIMIT,
                    "default": DEFAULT_SEARCH_LIMIT,
                },
            },
            "required": ["query"],
        },
    ),
    DocumentToolDefinition(
        name=READ_DOCUMENT,
        description=(
            "Read a document's markdown by its exact path, as reported by "
            "list_documents or search_documents. Long documents come back one "
            "window at a time: the reply says how many characters there are in "
            "total and, when more remain, the offset to continue from."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": _string(
                    "The document's path, exactly as listed, e.g. guides/refunds.md."
                ),
                "offset": {
                    "type": "integer",
                    "description": (
                        "Character offset to start at. Omit for the beginning of the "
                        "document."
                    ),
                    "minimum": 0,
                    "default": 0,
                },
                "limit": {
                    "type": "integer",
                    "description": (
                        f"How many characters to return (1-{MAX_READ_LIMIT}, default "
                        f"{DEFAULT_READ_LIMIT})."
                    ),
                    "minimum": 1,
                    "maximum": MAX_READ_LIMIT,
                    "default": DEFAULT_READ_LIMIT,
                },
            },
            "required": ["path"],
        },
    ),
)

#: The three names, for a caller that only needs to recognise them.
DOCUMENT_TOOL_NAMES: tuple[str, ...] = tuple(tool.name for tool in DOCUMENT_TOOLS)


def document_tool(name: str) -> DocumentToolDefinition | None:
    """The definition behind a tool name, or None for anything else.

    The executor dispatches on `SnapshotTool.source == "documents"` rather than
    on the name, so this is a lookup and not a gate: a `documents`-sourced call
    the corpus does not implement is reported to the model by name, which is
    what a run created before a fourth tool existed needs.
    """
    return next((tool for tool in DOCUMENT_TOOLS if tool.name == name), None)


def normalize_search_limit(value: int | None) -> int:
    """Clamps a `search_documents` limit to `[1, MAX_SEARCH_LIMIT]`."""
    if value is None:
        return DEFAULT_SEARCH_LIMIT
    if value < 1:
        return 1
    return min(value, MAX_SEARCH_LIMIT)


# ---------------------------------------------------------------------------
# Highlighting and the nearest preceding heading
# ---------------------------------------------------------------------------

#: What `ts_headline` wraps the matched words in. Deliberately not `**`: the
#: corpus is markdown and already contains its own emphasis, and the fragment
#: has to be strippable back to text that is byte-identical to a substring of
#: the document — which is what :func:`locate_snippet` needs to find the hit and
#: therefore the heading above it. A sentinel nothing writes by hand keeps
#: "remove the markers" from also removing the author's.
HIGHLIGHT_START = "[[hl]]"
HIGHLIGHT_END = "[[/hl]]"

#: The options string for `ts_headline`. One fragment, because the snippet is
#: located inside the document afterwards and a fragment stitched together from
#: two distant passages sits under no single heading.
HEADLINE_OPTIONS = (
    f'StartSel="{HIGHLIGHT_START}", StopSel="{HIGHLIGHT_END}", '
    "MaxWords=40, MinWords=15, MaxFragments=1"
)

#: An ATX heading: up to three leading spaces, one to six `#`, then the text.
_ATX_HEADING = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.*?)[ \t]*#*[ \t]*$")
#: A fence opening or closing a code block, where a `#` is a comment and not a
#: heading. Tildes count too — plenty of real documentation uses them precisely
#: because the block contains backticks.
_CODE_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
#: The underline of a setext heading. Both forms, because a corpus written by
#: hand uses whichever its author learned.
_SETEXT_UNDERLINE = re.compile(r"^ {0,3}(=+|-+)[ \t]*$")


def strip_highlight(headline: str) -> str:
    """A `ts_headline` fragment with the markers removed and nothing else.

    Exactly the inverse of what Postgres inserted, so the result is a substring
    of the document again — which is the whole point, see
    :func:`locate_snippet`. Whitespace is left alone here; shaping the fragment
    for the model is :func:`shape_snippet`'s job.
    """
    return headline.replace(HIGHLIGHT_START, "").replace(HIGHLIGHT_END, "")


def shape_snippet(headline: str) -> str:
    """A `ts_headline` fragment as the model should read it.

    The sentinels become markdown emphasis (the corpus's own language for "look
    here"), and the fragment collapses to a single line: a passage torn out of a
    table or a list otherwise arrives as a ragged block the model has to parse
    before it can even judge relevance.
    """
    text = headline.replace(HIGHLIGHT_START, "**").replace(HIGHLIGHT_END, "**")
    return re.sub(r"\s+", " ", text).strip()


def locate_snippet(content: str, snippet: str) -> int | None:
    """Where a stripped fragment sits in the document, or None.

    Three attempts, narrowing as they go, because the heading is worth a little
    effort and a wrong offset is worse than none:

    1. The fragment verbatim — what `ts_headline` produces for ordinary prose.
    2. The fragment with every run of whitespace allowed to differ, which is
       what a hit spanning a line break or an indented list item needs.
    3. Its first substantial word, case-insensitively.

    None means "give up", and the caller reports no heading rather than the
    document's first one, which would be a confident lie about where the match
    was.
    """
    text = snippet.strip()
    if not text:
        return None

    exact = content.find(text)
    if exact != -1:
        return exact

    loose = re.compile(r"\s+".join(re.escape(part) for part in text.split()))
    match = loose.search(content)
    if match is not None:
        return match.start()

    for word in text.split():
        if len(word) >= 3:
            found = content.lower().find(word.lower())
            return found if found != -1 else None
    return None


def nearest_heading(content: str, offset: int | None) -> str | None:
    """The markdown heading a character offset sits under, or None.

    "Nearest preceding" means the last heading that *begins* at or before the
    offset, so a hit inside the heading line itself belongs to that heading.
    Both markdown spellings count, and headings inside a fenced code block do
    not: a shell transcript full of `# install the client` would otherwise
    become the heading of everything below it, which is the failure mode that
    makes a citation untrustworthy.
    """
    if offset is None or offset < 0:
        return None

    heading: str | None = None
    fence: str | None = None
    position = 0
    previous_line = ""

    for line in content.splitlines(keepends=True):
        if position > offset:
            break
        stripped = line.rstrip("\r\n")

        fence_match = _CODE_FENCE.match(stripped)
        if fence_match is not None:
            marker = fence_match.group(1)[0]
            if fence is None:
                fence = marker
            elif fence == marker:
                fence = None
            previous_line = stripped
            position += len(line)
            continue

        if fence is None:
            atx = _ATX_HEADING.match(stripped)
            if atx is not None and atx.group(2):
                heading = atx.group(2)
            elif _SETEXT_UNDERLINE.match(stripped) and _is_setext_text(previous_line):
                heading = previous_line.strip()

        previous_line = stripped
        position += len(line)

    return heading


def _is_setext_text(line: str) -> bool:
    """Whether a line can be the *text* of a setext heading.

    A lone `---` is a thematic break far more often than it is an underline, and
    under a list item or another heading it is never one. Excluding those keeps
    a bullet list from silently becoming a heading.
    """
    text = line.strip()
    if not text:
        return False
    if _ATX_HEADING.match(line) or _SETEXT_UNDERLINE.match(line):
        return False
    return not re.match(r"^ {0,3}([-*+>]|\d+[.)])[ \t]", line)


def first_highlighted(headline: str) -> str:
    """The document text inside the first highlight pair, or `""`.

    The surface form, not the lexeme: `ts_headline` wraps the words as the
    document spells them, which is what makes it findable in `content` again.
    """
    start = headline.find(HIGHLIGHT_START)
    if start == -1:
        return ""
    start += len(HIGHLIGHT_START)
    end = headline.find(HIGHLIGHT_END, start)
    if end == -1:
        return ""
    return strip_highlight(headline[start:end]).strip()


def heading_for_snippet(content: str, headline: str) -> str | None:
    """The heading the *match* inside a `ts_headline` fragment sits under, or None.

    The composition the repository calls: strip the markers, find the fragment,
    then read the heading above the first highlighted word rather than above the
    fragment's own first character. That distinction is not a refinement, it is
    the difference between a citation and a wrong one: `MinWords` pads a fragment
    backwards until it is long enough, so a match near the top of a section
    routinely arrives inside a fragment that *begins* in the previous one — and
    frequently with the real heading sitting in the middle of the fragment. Read
    from the fragment's start, such a hit is reported under the heading before
    the one it is actually under, which is worse than reporting none (see
    :func:`nearest_heading`).

    The highlighted word is looked up from the fragment's offset onwards rather
    than trusted as an arithmetic offset into it, because :func:`locate_snippet`
    has three ways of finding a fragment and only the first is character-exact.
    A fragment with no highlight in it at all (`ts_headline` returns the head of
    the document when nothing matched) falls back to its own position.
    """
    plain = strip_highlight(headline)
    fragment_offset = locate_snippet(content, plain)
    if fragment_offset is None:
        return None

    word = first_highlighted(headline)
    if word:
        found = content.find(word, fragment_offset)
        if found != -1:
            return nearest_heading(content, found)
    return nearest_heading(content, fragment_offset)


# ---------------------------------------------------------------------------
# How a document is stored: its key, its text and its label
# ---------------------------------------------------------------------------
#
# A corpus has **two write doors** — `app.api.toolsets` (a JSON body or a
# multipart upload) and `app.mcp.server` (an agent pushing another repo's `docs/`
# in, which is the primary way a corpus gets filled) — and they write the same
# table. `read_document` matches `path` exactly and `UNIQUE (toolset_id, path)`
# is the only thing keeping one document from being two, so if the two doors
# disagreed about what a key or a line ending is, the same file arriving through
# both would land twice, `list_documents` would offer the model a choice between
# two spellings of one document, and the `chars` it reports would depend on which
# editor last saved the file. The rules therefore live here, once, and both doors
# call them.
#
# All three raise `ValueError` rather than an HTTP or MCP error: this module
# knows nothing about either transport, and each door already turns a `ValueError`
# into its own vocabulary — a 422 from a Pydantic validator, an `ok: false` row
# in an upload's results, an `isError` content block over MCP. That is the same
# split the rest of the module keeps, and it is the *authoring* boundary, not the
# retrieval one: nothing a **model** passes at run time raises anything at all.

#: A path is a key, not a filesystem name, so the ceiling only exists to keep an
#: accidental paste out of the column. A title is a label in a table.
MAX_PATH_LENGTH = 300
MAX_TITLE_LENGTH = 200

#: The ceiling on one document's markdown, asked by **every** write door because
#: `normalize_markdown` asks it. The multipart route additionally refuses on
#: *bytes* before it decodes anything, which is a different concern in a different
#: unit — keeping a dropped video out of memory rather than merely out of the
#: column — so the two coexist deliberately. What must not come back is a
#: per-door character limit: the JSON route, the upload route and MCP write the
#: same table, and a corpus holding a row only one of them could have written is
#: the same class of bug as the two spellings of one path.
MAX_DOCUMENT_CHARS = 1_000_000

#: The corpus format, v1, and the extensions a title is derived through. Markdown
#: only: a PDF or a `.docx` would have to be converted somewhere, and converting
#: it silently would make the text the model retrieved from something nobody in
#: the engagement had ever read.
MARKDOWN_SUFFIXES = (".md", ".markdown")


def clean_document_path(value: str) -> str:
    """The `read_document` key, in the one spelling a corpus stores.

    `path` names nothing on a filesystem — the lookup is `toolset_id` plus the
    caller's scope predicate — so this is not a traversal defence and there is
    nothing to defend: no code anywhere opens a file for it. It is a *key*
    normaliser. Windows separators become slashes and a leading `./` or `/` goes,
    because "guides/refunds.md" and "/guides/refunds.md" resolving to two
    different documents would make the path the model quotes back out of
    `list_documents` a coin flip. `..` is refused for exactly that reason too: it
    is a second spelling of a key that already has one.
    """
    text = value.strip().replace("\\", "/")
    segments = [segment.strip() for segment in text.split("/")]
    segments = [segment for segment in segments if segment not in ("", ".")]
    if not segments:
        raise ValueError("A document needs a path, e.g. guides/refunds.md")
    if ".." in segments:
        raise ValueError('A document path may not contain ".." — it is a key, not a file path.')
    cleaned = "/".join(segments)
    if any(character < " " for character in cleaned):
        raise ValueError("A document path may not contain control characters.")
    if len(cleaned) > MAX_PATH_LENGTH:
        raise ValueError(f"A document path may be at most {MAX_PATH_LENGTH} characters.")
    return cleaned


def normalize_markdown(text: str) -> str:
    """A document's markdown as the corpus stores it.

    Two changes, and both are what "verbatim" has to mean once a file has come
    off a Windows disk or out of a Windows checkout. A leading byte-order mark is
    dropped — it would otherwise sit in front of the first `#` and cost the
    document its title *and* its first heading. CRLF becomes LF, which matters
    more than it looks: `read_document` windows by characters and reports those
    offsets back to the model so it can ask for the next window itself, and
    `search_documents` resolves a hit's heading by locating the fragment in this
    same text — so a corpus mixing line endings hands out windows whose length,
    and a `chars` count, depend on which editor last touched the file.

    Then two refusals, both raised here so that all three write doors inherit
    them rather than each remembering:

    - **A NUL byte is refused.** Postgres cannot store one in a `text` column, so
      the alternative to refusing it is not "a document with a NUL in it" but an
      unhandled driver error \u2014 which in the multipart route would discard the
      other twenty-nine files in the request that route promises to isolate, and
      elsewhere a 500 where every other content refusal reads as a 422.
    - **`MAX_DOCUMENT_CHARS` is the ceiling**, so the JSON route cannot accept a
      document that the upload route and MCP would both have refused.
    """
    cleaned = text.removeprefix("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    if "\x00" in cleaned:
        raise ValueError(
            "A document may not contain NUL bytes \u2014 is this really a markdown file?"
        )
    if len(cleaned) > MAX_DOCUMENT_CHARS:
        raise ValueError(
            f"That document is {len(cleaned):,} characters; one document may hold "
            f"{MAX_DOCUMENT_CHARS:,}. Split it into the sections a reader would open "
            "separately — that split is itself part of what a retrieval test measures."
        )
    return cleaned


def derive_document_title(content: str, path: str) -> str:
    """A label for a document that arrived without one.

    The markdown's own first heading, falling back to the path's file stem: a
    folder of fifty guides should not need fifty titles typed in by hand, and the
    heading its author wrote is a better label than anything this could invent —
    which is also why an agent walking a repository need only send a path and the
    text. Reuses :func:`nearest_heading` at offset 0 rather than re-implementing
    "is this line a heading", so an opening `#` inside a code fence is not
    mistaken for a title, exactly as it is not mistaken for a citation.

    One consequence of that reuse, and the reason it is preferred to a second
    heading parser: a document whose title is a *setext* heading falls through to
    the stem, because `nearest_heading` only recognises one once it has seen the
    underline and at offset 0 nothing precedes the cursor. A stem is a fine
    label; two subtly different answers to "is this line a heading", one of which
    a search hit's citation depends on, would not be.
    """
    heading = nearest_heading(content, 0)
    if heading:
        return heading[:MAX_TITLE_LENGTH]
    stem = path.rsplit("/", 1)[-1]
    for suffix in MARKDOWN_SUFFIXES:
        if stem.lower().endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    return (stem or path)[:MAX_TITLE_LENGTH]


# ---------------------------------------------------------------------------
# What the repository projects its rows into
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DocumentSummary:
    """One document as `list_documents` reports it.

    `chars` and not "bytes" or "words": it is the same unit `read_document`
    windows in, so a model can read the size and plan its calls in one currency.
    """

    path: str
    title: str
    chars: int


@dataclass(frozen=True)
class DocumentMatch:
    """One `search_documents` hit, already shaped.

    `rank` is `ts_rank`'s score and is the ordering the repository applied; it is
    deliberately *not* in the payload the model reads. A number it cannot
    calibrate would only invite it to treat 0.061 as a confidence, when the only
    honest reading is "these came back in this order".
    """

    document_id: int
    path: str
    title: str
    heading: str | None
    snippet: str
    rank: float


# ---------------------------------------------------------------------------
# read_document's window
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DocumentWindow:
    """A slice of one document, plus what the model needs to ask for the rest."""

    text: str
    #: The clamped offset the slice actually starts at, which is not necessarily
    #: the one that was asked for.
    offset: int
    #: The clamped limit that was applied.
    limit: int
    total_chars: int
    #: Where to continue, or None when the window reached the end of the
    #: document. A single key that answers "is there more, and from where",
    #: because two keys invite a model to trust one and ignore the other.
    next_offset: int | None

    @property
    def chars(self) -> int:
        return len(self.text)

    @property
    def truncated(self) -> bool:
        return self.next_offset is not None


def window_document(
    content: str, offset: int | None = None, limit: int | None = None
) -> DocumentWindow:
    """Windows a document's markdown, clamping both arguments into range.

    Clamping rather than refusing, throughout: a negative offset reads from the
    start, an oversized limit reads the ceiling, and an offset past the end
    returns an empty window that still reports `total_chars` — which is the one
    answer that lets a model that lost its place recover on the next call
    instead of spending a turn on an error message.
    """
    total = len(content)
    start = 0 if offset is None or offset < 0 else min(offset, total)
    size = DEFAULT_READ_LIMIT if limit is None else max(1, min(limit, MAX_READ_LIMIT))

    end = min(start + size, total)
    return DocumentWindow(
        text=content[start:end],
        offset=start,
        limit=size,
        total_chars=total,
        next_offset=end if end < total else None,
    )


# ---------------------------------------------------------------------------
# The payloads the model reads
# ---------------------------------------------------------------------------


def list_documents_payload(documents: Sequence[DocumentSummary]) -> dict[str, Any]:
    """`list_documents`' answer. An empty corpus says so in words.

    A bare `{"documents": []}` reads to a model as a malfunction it should retry;
    saying the corpus is empty is the fact that stops it from burning its turn
    budget searching for something nobody uploaded.
    """
    payload: dict[str, Any] = {
        "document_count": len(documents),
        "documents": [
            {"path": document.path, "title": document.title, "chars": document.chars}
            for document in documents
        ],
    }
    if not documents:
        payload["note"] = "This corpus contains no documents."
    return payload


def search_documents_payload(query: str, matches: Sequence[DocumentMatch]) -> dict[str, Any]:
    """`search_documents`' answer, hits in the order the ranking put them.

    A miss is a normal answer with a usable next step in it, not an error: the
    words a customer's documentation uses are frequently not the words the
    question used, and "try list_documents" is exactly the recovery this workload
    is meant to measure a model on.
    """
    payload: dict[str, Any] = {
        "query": query,
        "match_count": len(matches),
        "matches": [
            {
                "path": match.path,
                "title": match.title,
                "heading": match.heading,
                "snippet": match.snippet,
            }
            for match in matches
        ],
    }
    if not matches:
        payload["note"] = (
            "No document matched. Try fewer or different words, or call "
            f"{LIST_DOCUMENTS} to see what the corpus covers."
        )
    return payload


def read_document_payload(
    document: DocumentSummary, window: DocumentWindow
) -> dict[str, Any]:
    """`read_document`'s answer for one window of one document."""
    payload: dict[str, Any] = {
        "path": document.path,
        "title": document.title,
        "offset": window.offset,
        "chars": window.chars,
        "total_chars": window.total_chars,
        "truncated": window.truncated,
        "content": window.text,
    }
    if window.next_offset is not None:
        payload["next_offset"] = window.next_offset
    return payload


def unknown_path_message(path: str, known_paths: Sequence[str]) -> str:
    """The sentence a `read_document` call with no such path gets back.

    Wrong paths are a *measured* behavior here — a model that invents
    `docs/refunds.md` because it looks plausible should be seen recovering — so
    the message names the paths that do exist rather than only saying no. It is
    the caller that wraps this in `app.services.tool_loop.error_payload`, which
    keeps every tool error in this app one shape.
    """
    if not known_paths:
        return (
            f'There is no document at "{path}". This corpus contains no documents at all.'
        )
    listed = ", ".join(known_paths[:MAX_SEARCH_LIMIT])
    more = "" if len(known_paths) <= MAX_SEARCH_LIMIT else ", …"
    return (
        f'There is no document at "{path}". Available paths: {listed}{more}. '
        f"Use one of those exactly, or call {LIST_DOCUMENTS} for the full list."
    )


def unknown_tool_message(name: str) -> str:
    """A `documents`-sourced call this version does not implement.

    Reachable only from a run frozen by a build that offered a fourth document
    tool, which is exactly the case a snapshot has to survive: the model is told
    what happened instead of the row dying.
    """
    return (
        f'"{name}" is not a document tool this corpus provides. '
        f"Available: {', '.join(DOCUMENT_TOOL_NAMES)}."
    )


__all__ = [
    "DEFAULT_READ_LIMIT",
    "DEFAULT_SEARCH_LIMIT",
    "DOCUMENT_TOOLS",
    "DOCUMENT_TOOL_NAMES",
    "HEADLINE_OPTIONS",
    "HIGHLIGHT_END",
    "HIGHLIGHT_START",
    "LIST_DOCUMENTS",
    "MARKDOWN_SUFFIXES",
    "MAX_PATH_LENGTH",
    "MAX_READ_LIMIT",
    "MAX_SEARCH_LIMIT",
    "MAX_TITLE_LENGTH",
    "READ_DOCUMENT",
    "SEARCH_DOCUMENTS",
    "DocumentMatch",
    "DocumentSummary",
    "DocumentToolDefinition",
    "DocumentWindow",
    "clean_document_path",
    "derive_document_title",
    "document_tool",
    "first_highlighted",
    "heading_for_snippet",
    "list_documents_payload",
    "locate_snippet",
    "nearest_heading",
    "normalize_markdown",
    "normalize_search_limit",
    "read_document_payload",
    "search_documents_payload",
    "shape_snippet",
    "strip_highlight",
    "unknown_path_message",
    "unknown_tool_message",
    "window_document",
]
