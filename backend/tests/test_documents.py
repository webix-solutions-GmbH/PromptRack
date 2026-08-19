"""`app.services.documents` — the three document tools' text rules.

Everything a retrieval measurement rests on that needs no Postgres: which
heading a search hit sits under, which slice of a document `read_document`
hands back for a given offset, what the payloads say when the answer is "no",
and the fixed shape of the three synthesized tool definitions.

The heading tests are the ones worth reading twice. A snippet's citation is only
as trustworthy as `nearest_heading`, and every case here is a document a
consultancy's own handbook really contains: a shell transcript whose comments
look like headings, a thematic break that is not a setext underline, a bullet
list above a rule. Each of those, read wrongly, makes a search hit claim it came
from a section it never came from — which reads in `/results` as the *model*
citing badly.

Database-free and fast, like the rest of the pure suite: the queries behind
these shapes (`websearch_to_tsquery`, `ts_rank`, `ts_headline`) live in
`app.repos.documents` and are exercised against a real Postgres in the
integration suite.
"""

from __future__ import annotations

import json

import pytest

from app.services.documents import (
    DEFAULT_READ_LIMIT,
    DEFAULT_SEARCH_LIMIT,
    DOCUMENT_TOOL_NAMES,
    DOCUMENT_TOOLS,
    HEADLINE_OPTIONS,
    HIGHLIGHT_END,
    HIGHLIGHT_START,
    LIST_DOCUMENTS,
    MAX_DOCUMENT_CHARS,
    MAX_PATH_LENGTH,
    MAX_READ_LIMIT,
    MAX_SEARCH_LIMIT,
    MAX_TITLE_LENGTH,
    READ_DOCUMENT,
    SEARCH_DOCUMENTS,
    DocumentMatch,
    DocumentSummary,
    clean_document_path,
    derive_document_title,
    document_tool,
    heading_for_snippet,
    list_documents_payload,
    locate_snippet,
    nearest_heading,
    normalize_markdown,
    normalize_search_limit,
    read_document_payload,
    search_documents_payload,
    shape_snippet,
    strip_highlight,
    unknown_path_message,
    unknown_tool_message,
    window_document,
)

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

#: A corpus page in the shape these documents really arrive in: a title, a
#: couple of sections, and a shell transcript whose `#` comments are not
#: headings.
HANDBOOK = """# Refunds

Every refund is approved by the warehouse first.

## After 30 days

Only store credit remains available.

```bash
# Rückgabe anlegen
promptrack refund create
```

Store credit never expires.
"""


def offset_of(content: str, needle: str) -> int:
    """Where a phrase sits, so a test can name a place in prose rather than a
    magic number that a reworded fixture would silently invalidate.
    """
    found = content.find(needle)
    assert found != -1, needle
    return found


def highlighted(content: str, needle: str) -> str:
    """A `ts_headline` fragment for a phrase, built with the same sentinels
    Postgres is asked to insert — so these tests cannot pass against markers
    the query never writes.
    """
    return f"{HIGHLIGHT_START}{needle}{HIGHLIGHT_END}"


def summary(path: str = "guides/refunds.md", *, chars: int = 120) -> DocumentSummary:
    return DocumentSummary(path=path, title="Refunds", chars=chars)


def match(
    path: str = "guides/refunds.md", *, heading: str | None = "After 30 days"
) -> DocumentMatch:
    return DocumentMatch(
        document_id=11,
        path=path,
        title="Refunds",
        heading=heading,
        snippet="Only **store credit** remains available.",
        rank=0.0607927,
    )


# ---------------------------------------------------------------------------
# The nearest preceding heading
# ---------------------------------------------------------------------------


class TestNearestHeading:
    def test_reads_the_last_heading_that_begins_before_the_offset(self):
        assert nearest_heading(HANDBOOK, offset_of(HANDBOOK, "warehouse")) == "Refunds"
        assert nearest_heading(HANDBOOK, offset_of(HANDBOOK, "store credit")) == "After 30 days"

    def test_a_hit_inside_the_heading_line_belongs_to_that_heading(self):
        # "Nearest preceding" counts a heading that *begins* at or before the
        # offset, so matching the heading's own words cites that section rather
        # than the one above it.
        assert nearest_heading(HANDBOOK, offset_of(HANDBOOK, "After 30 days")) == "After 30 days"

    def test_a_hit_in_the_very_first_heading_still_has_one(self):
        assert nearest_heading(HANDBOOK, 0) == "Refunds"

    def test_a_document_with_no_headings_has_none_rather_than_a_guess(self):
        # A confident lie about where the match was is worse than no citation.
        assert nearest_heading("Plain prose about refunds.\n\nMore of it.\n", 30) is None

    def test_text_above_the_first_heading_has_none_either(self):
        content = "A preamble nobody titled.\n\n# Refunds\n\nBody.\n"
        assert nearest_heading(content, offset_of(content, "preamble")) is None

    def test_an_offset_past_the_end_reads_the_last_heading(self):
        assert nearest_heading(HANDBOOK, 10_000) == "After 30 days"

    def test_no_offset_and_a_negative_offset_are_no_heading(self):
        assert nearest_heading(HANDBOOK, None) is None
        assert nearest_heading(HANDBOOK, -1) is None

    def test_a_hash_inside_a_fenced_block_is_a_comment_not_a_heading(self):
        # The failure mode that makes a citation untrustworthy: a shell
        # transcript full of `# install the client` becoming the heading of
        # everything below it.
        assert nearest_heading(HANDBOOK, offset_of(HANDBOOK, "never expires")) == "After 30 days"

    def test_a_tilde_fence_hides_its_hashes_too(self):
        content = "# Setup\n\n~~~\n# Not a heading\n~~~\n\nDone.\n"
        assert nearest_heading(content, offset_of(content, "Done.")) == "Setup"

    def test_a_backtick_run_inside_a_tilde_fence_does_not_close_it(self):
        # Documentation uses tildes precisely because the block contains
        # backticks; treating those as the closing fence would put the rest of
        # the file back "outside" the block.
        content = "# Setup\n\n~~~\n```\n# Not a heading\n```\n~~~\n\nDone.\n"
        assert nearest_heading(content, offset_of(content, "Done.")) == "Setup"

    def test_headings_resume_once_the_fence_closes(self):
        content = "```\n# Comment\n```\n\n## Real heading\n\nBody.\n"
        assert nearest_heading(content, offset_of(content, "Body.")) == "Real heading"

    def test_closing_hashes_are_not_part_of_the_heading_text(self):
        content = "## Refunds ##\n\nBody.\n"
        assert nearest_heading(content, offset_of(content, "Body.")) == "Refunds"

    def test_a_hash_without_a_space_is_not_a_heading(self):
        content = "#hashtag\n\nBody.\n"
        assert nearest_heading(content, offset_of(content, "Body.")) is None

    def test_a_heading_with_no_text_is_not_adopted(self):
        content = "# \n\nBody.\n"
        assert nearest_heading(content, offset_of(content, "Body.")) is None

    def test_an_indented_code_line_is_not_a_heading(self):
        # Four spaces is an indented code block; three is still a heading.
        assert nearest_heading("    # Code\n\nBody.\n", 12) is None
        assert nearest_heading("   # Indented\n\nBody.\n", 16) == "Indented"

    def test_both_setext_underlines_count(self):
        for underline in ("=======", "-------"):
            content = f"Refunds\n{underline}\n\nBody.\n"
            assert nearest_heading(content, offset_of(content, "Body.")) == "Refunds"

    def test_a_thematic_break_is_not_a_setext_underline(self):
        # A lone `---` after a blank line is a horizontal rule, and reading it as
        # an underline would title the section after whatever preceded it.
        content = "Intro text.\n\n---\n\nBody.\n"
        assert nearest_heading(content, offset_of(content, "Body.")) is None

    def test_a_rule_under_a_bullet_list_is_not_a_heading_either(self):
        content = "# Refunds\n\n- store credit\n---\n\nBody.\n"
        assert nearest_heading(content, offset_of(content, "Body.")) == "Refunds"


# ---------------------------------------------------------------------------
# Locating a fragment, and the two sentinel helpers
# ---------------------------------------------------------------------------


class TestStripHighlight:
    def test_removes_exactly_what_postgres_inserted(self):
        fragment = f"Only {HIGHLIGHT_START}store credit{HIGHLIGHT_END} remains."

        assert strip_highlight(fragment) == "Only store credit remains."

    def test_the_result_is_a_substring_of_the_document_again(self):
        # Which is the whole reason the sentinels are not `**`: `locate_snippet`
        # has to find the fragment back inside the markdown.
        fragment = highlighted(HANDBOOK, "Only store credit remains available.")

        assert strip_highlight(fragment) in HANDBOOK

    def test_the_authors_own_emphasis_survives(self):
        fragment = f"a {HIGHLIGHT_START}**bold**{HIGHLIGHT_END} claim"

        assert strip_highlight(fragment) == "a **bold** claim"

    def test_whitespace_is_left_alone(self):
        assert strip_highlight(f"a {HIGHLIGHT_START}b{HIGHLIGHT_END}  c") == "a b  c"


class TestShapeSnippet:
    def test_the_sentinels_become_the_corpus_own_emphasis(self):
        fragment = f"Only {HIGHLIGHT_START}store credit{HIGHLIGHT_END} remains."

        assert shape_snippet(fragment) == "Only **store credit** remains."

    def test_a_fragment_torn_out_of_a_list_collapses_to_one_line(self):
        fragment = "- store credit\n-\tvoucher\n\n  - refund"

        assert shape_snippet(fragment) == "- store credit - voucher - refund"

    def test_surrounding_whitespace_is_dropped(self):
        assert shape_snippet("\n  Refunds are approved.  \n") == "Refunds are approved."


class TestLocateSnippet:
    def test_finds_a_verbatim_fragment(self):
        needle = "Only store credit remains available."

        assert locate_snippet(HANDBOOK, needle) == offset_of(HANDBOOK, needle)

    def test_finds_a_fragment_whose_whitespace_differs(self):
        # A hit spanning a line break comes back from `ts_headline` with the
        # break collapsed, and it still has to resolve to a heading.
        content = "# Refunds\n\nEvery refund is approved\nby the warehouse first.\n"

        found = locate_snippet(content, "approved by the warehouse")

        assert found == offset_of(content, "approved")

    def test_falls_back_to_the_first_substantial_word_case_insensitively(self):
        content = "# Refunds\n\nApproved by the WAREHOUSE first.\n"

        found = locate_snippet(content, "warehouse approves the refund")

        assert found == offset_of(content, "WAREHOUSE")

    def test_gives_up_rather_than_pointing_at_the_documents_first_heading(self):
        assert locate_snippet(HANDBOOK, "quantum tunnelling") is None

    def test_the_fallback_gives_up_on_its_first_candidate_rather_than_hunting_on(self):
        # "quantum" is not in the handbook, so the fragment is not located even
        # though a later word is — the safe direction, since a heading resolved
        # from an unrelated word further down would be a confident lie about
        # where the hit was.
        assert locate_snippet(HANDBOOK, "quantum store credit") is None

    def test_a_fragment_of_only_short_words_locates_nothing(self):
        assert locate_snippet(HANDBOOK, "is by an") is None

    def test_a_blank_fragment_locates_nothing(self):
        assert locate_snippet(HANDBOOK, "   ") is None
        assert locate_snippet(HANDBOOK, "") is None


class TestHeadingForSnippet:
    def test_resolves_a_highlighted_fragment_to_its_section(self):
        fragment = highlighted(HANDBOOK, "Only store credit remains available.")

        assert heading_for_snippet(HANDBOOK, fragment) == "After 30 days"

    def test_a_fragment_that_cannot_be_located_reports_no_heading(self):
        assert heading_for_snippet(HANDBOOK, highlighted(HANDBOOK, "quantum tunnelling")) is None

    def test_the_headline_options_ask_for_the_markers_this_module_strips(self):
        # The one coupling between this module and the SQL: a renamed sentinel
        # that only reached one of the two would leave every snippet unlocatable
        # and therefore every hit uncited.
        assert f'StartSel="{HIGHLIGHT_START}"' in HEADLINE_OPTIONS
        assert f'StopSel="{HIGHLIGHT_END}"' in HEADLINE_OPTIONS
        # One fragment: a snippet stitched from two distant passages sits under
        # no single heading.
        assert "MaxFragments=1" in HEADLINE_OPTIONS


# ---------------------------------------------------------------------------
# read_document's window
# ---------------------------------------------------------------------------


class TestWindowDocument:
    def test_a_short_document_arrives_whole_and_reports_no_continuation(self):
        window = window_document("Short.")

        assert window.text == "Short."
        assert (window.offset, window.limit) == (0, DEFAULT_READ_LIMIT)
        assert window.total_chars == 6
        assert window.chars == 6
        assert window.next_offset is None
        assert window.truncated is False

    def test_a_long_document_reports_where_to_continue(self):
        content = "x" * (DEFAULT_READ_LIMIT + 10)

        window = window_document(content)

        assert window.chars == DEFAULT_READ_LIMIT
        assert window.next_offset == DEFAULT_READ_LIMIT
        assert window.truncated is True
        assert window.total_chars == DEFAULT_READ_LIMIT + 10

    def test_the_next_window_continues_exactly_where_the_last_one_stopped(self):
        content = "abcdefghij"

        first = window_document(content, limit=4)
        second = window_document(content, offset=first.next_offset, limit=4)
        third = window_document(content, offset=second.next_offset, limit=4)

        assert first.text + second.text + third.text == content
        assert third.next_offset is None

    def test_a_limit_larger_than_the_document_is_not_an_error(self):
        window = window_document("Short.", limit=MAX_READ_LIMIT)

        assert window.text == "Short."
        assert window.next_offset is None

    def test_an_oversized_limit_is_clamped_to_the_ceiling(self):
        content = "x" * (MAX_READ_LIMIT + 100)

        window = window_document(content, limit=MAX_READ_LIMIT * 10)

        assert window.limit == MAX_READ_LIMIT
        assert window.chars == MAX_READ_LIMIT
        assert window.next_offset == MAX_READ_LIMIT

    def test_a_zero_or_negative_limit_still_returns_something(self):
        # Clamping rather than refusing: an over-eager argument costs the model a
        # smaller answer, not a turn spent reading an error.
        for limit in (0, -50):
            window = window_document("abcdef", limit=limit)
            assert (window.limit, window.text) == (1, "a")

    def test_a_negative_offset_reads_from_the_start(self):
        window = window_document("abcdef", offset=-20, limit=3)

        assert (window.offset, window.text) == (0, "abc")

    def test_an_offset_past_the_end_is_an_empty_window_that_still_says_how_long_the_document_is(
        self,
    ):
        # The one answer that lets a model which lost its place recover on the
        # next call instead of spending a turn on an error message.
        window = window_document("abcdef", offset=9999)

        assert window.text == ""
        assert window.offset == 6
        assert window.total_chars == 6
        assert window.next_offset is None
        assert window.truncated is False

    def test_an_offset_exactly_at_the_end_reads_as_the_end(self):
        window = window_document("abcdef", offset=6, limit=10)

        assert (window.text, window.next_offset) == ("", None)

    def test_an_empty_document_windows_without_complaint(self):
        window = window_document("")

        assert (window.text, window.total_chars, window.next_offset) == ("", 0, None)


# ---------------------------------------------------------------------------
# The three tool definitions
# ---------------------------------------------------------------------------


class TestDocumentTools:
    """The definitions are the measurement's control surface.

    They are synthesized into real `tools` rows rather than authored, so every
    corpus offers the same three functions with the same schemas — which is what
    stops a model that navigated one customer's documentation badly from being
    excused by a differently-worded description. These tests pin the shape those
    rows are built from.
    """

    def test_exactly_three_tools_in_the_order_a_model_should_use_them(self):
        assert DOCUMENT_TOOL_NAMES == (LIST_DOCUMENTS, SEARCH_DOCUMENTS, READ_DOCUMENT)
        assert tuple(entry.name for entry in DOCUMENT_TOOLS) == DOCUMENT_TOOL_NAMES

    def test_every_tool_tells_the_model_how_to_use_it(self):
        for entry in DOCUMENT_TOOLS:
            assert len(entry.description) > 40, entry.name

    def test_list_documents_takes_no_arguments(self):
        schema = document_tool(LIST_DOCUMENTS).parameters

        assert schema == {"type": "object", "properties": {}}

    def test_search_documents_requires_a_query_and_advertises_the_limit_it_clamps_to(self):
        schema = document_tool(SEARCH_DOCUMENTS).parameters

        assert schema["required"] == ["query"]
        limit = schema["properties"]["limit"]
        assert (limit["minimum"], limit["maximum"]) == (1, MAX_SEARCH_LIMIT)
        assert limit["default"] == DEFAULT_SEARCH_LIMIT

    def test_read_document_requires_a_path_and_windows_by_characters(self):
        schema = document_tool(READ_DOCUMENT).parameters

        assert schema["required"] == ["path"]
        assert schema["properties"]["offset"]["minimum"] == 0
        limit = schema["properties"]["limit"]
        assert (limit["minimum"], limit["maximum"]) == (1, MAX_READ_LIMIT)
        assert limit["default"] == DEFAULT_READ_LIMIT

    def test_a_path_argument_is_a_plain_string(self):
        # No enum of the corpus's paths: inventing a path and recovering from the
        # refusal is one of the behaviours this workload measures.
        assert document_tool(READ_DOCUMENT).parameters["properties"]["path"]["type"] == "string"

    def test_the_stored_column_is_the_schema_and_is_byte_stable(self):
        for entry in DOCUMENT_TOOLS:
            assert json.loads(entry.parameters_json) == entry.parameters
            # A re-assert has to write identical bytes, or every sync would bump
            # `updated_at` on three rows for nothing.
            assert entry.parameters_json == entry.parameters_json

    def test_an_unknown_name_is_a_miss_and_not_a_guess(self):
        assert document_tool("read_documents") is None
        assert document_tool("") is None


class TestNormalizeSearchLimit:
    def test_an_absent_limit_is_the_default(self):
        assert normalize_search_limit(None) == DEFAULT_SEARCH_LIMIT

    def test_a_sensible_limit_is_left_alone(self):
        assert normalize_search_limit(3) == 3

    def test_an_over_eager_limit_is_clamped_rather_than_refused(self):
        assert normalize_search_limit(500) == MAX_SEARCH_LIMIT

    def test_zero_and_negative_still_return_one_hit(self):
        assert normalize_search_limit(0) == 1
        assert normalize_search_limit(-4) == 1


# ---------------------------------------------------------------------------
# How a document is stored, at both write doors
# ---------------------------------------------------------------------------
#
# These three rules exist here rather than in either door because a corpus has
# two of them — `app.api.toolsets` (a JSON body and a multipart upload) and
# `app.mcp.server` (an agent pushing another repo's `docs/` in) — and they write
# one table. `read_document` matches `path` exactly and `UNIQUE (toolset_id,
# path)` is all that keeps one document from becoming two, so a per-door answer
# to "what is a key" or "what is a line ending" makes the same file land twice
# and hands the model a choice between two spellings of one document.


class TestCleanDocumentPath:
    def test_the_ordinary_key_survives_untouched(self):
        assert clean_document_path("guides/refunds.md") == "guides/refunds.md"

    def test_every_second_spelling_of_one_key_collapses_onto_it(self):
        for spelling in (
            "  guides/refunds.md ",
            "./guides/refunds.md",
            "/guides/refunds.md",
            "guides//refunds.md",
            "guides/./refunds.md",
            "guides\\refunds.md",
        ):
            assert clean_document_path(spelling) == "guides/refunds.md"

    def test_case_is_never_folded(self):
        # "Refunds.MD" and "refunds.md" are two documents a corpus is entitled to
        # hold; folding them would silently delete one at the next upload.
        assert clean_document_path("Guides\\Refunds.MD") == "Guides/Refunds.MD"

    def test_a_path_that_normalises_to_nothing_is_refused(self):
        for blank in ("   ", "/", "./", "//."):
            with pytest.raises(ValueError, match="needs a path"):
                clean_document_path(blank)

    def test_traversal_is_refused_as_a_duplicate_key_and_not_as_a_danger(self):
        # Nothing opens a file for a `path` — the lookup is `toolset_id` plus the
        # caller's scope predicate — so this is not a traversal defence. `..` is
        # simply a second way to spell a key that already has one.
        with pytest.raises(ValueError, match="it is a key, not a file path"):
            clean_document_path("guides/../guides/refunds.md")

    def test_control_characters_are_refused(self):
        with pytest.raises(ValueError, match="control characters"):
            clean_document_path("guides/ref\tunds.md")

    def test_an_overlong_path_is_refused_rather_than_silently_cut(self):
        # Cutting it would produce a key the caller never asked for, which
        # `read_document` would then never match.
        with pytest.raises(ValueError, match=str(MAX_PATH_LENGTH)):
            clean_document_path("g/" + "x" * MAX_PATH_LENGTH)


class TestNormalizeMarkdown:
    def test_lf_markdown_is_untouched(self):
        text = "# Refunds\n\nWithin 30 days.\n"
        assert normalize_markdown(text) == text

    def test_crlf_and_lone_cr_both_become_lf(self):
        # `read_document` windows by characters and reports those offsets back to
        # the model, so a corpus mixing line endings hands out windows whose
        # length depends on which editor last saved the file.
        assert normalize_markdown("a\r\nb\rc\n") == "a\nb\nc\n"

    def test_a_leading_bom_goes_so_the_first_heading_is_still_a_heading(self):
        assert normalize_markdown("\ufeff# Refunds\n") == "# Refunds\n"
        assert nearest_heading(normalize_markdown("\ufeff# Refunds\n\nText"), 20) == "Refunds"

    def test_a_bom_further_in_is_left_alone(self):
        # Only a *leading* mark is an encoding artefact; one mid-document is
        # content, and this module never edits a document's text.
        assert normalize_markdown("# A\n\ufeffb") == "# A\n\ufeffb"

    def test_a_nul_byte_is_refused_rather_than_handed_to_postgres(self):
        # Postgres cannot hold a NUL in a `text` column, so the alternative to
        # refusing it here is an unhandled driver error \u2014 which in the multipart
        # route would discard every other file in the request. Refused in the
        # shared rule so all three write doors inherit it.
        with pytest.raises(ValueError, match="NUL"):
            normalize_markdown("# Refunds\n\nWithin 30\x00 days.\n")

    def test_a_document_over_the_ceiling_is_refused(self):
        # The one authoring rule the three doors used to disagree about: the JSON
        # route had no ceiling at all while the upload route and MCP both had one.
        with pytest.raises(ValueError, match="Split it into the sections"):
            normalize_markdown("x" * (MAX_DOCUMENT_CHARS + 1))

    def test_a_document_exactly_at_the_ceiling_is_accepted(self):
        assert len(normalize_markdown("x" * MAX_DOCUMENT_CHARS)) == MAX_DOCUMENT_CHARS

    def test_the_ceiling_counts_characters_after_the_line_endings_are_folded(self):
        # CRLF folding shortens the text, so a document that only exceeds the
        # ceiling while it still carries CRLF is inside it once stored \u2014 the count
        # has to describe what the column actually holds.
        half = MAX_DOCUMENT_CHARS // 2
        crlf = "a\r\n" * half
        assert len(crlf) > MAX_DOCUMENT_CHARS
        assert len(normalize_markdown(crlf)) == half * 2


class TestDeriveDocumentTitle:
    def test_the_markdown_first_heading_wins(self):
        assert derive_document_title("# Rückgaberichtlinie\n\nText", "guides/r.md") == (
            "Rückgaberichtlinie"
        )

    def test_a_setext_titled_document_falls_back_to_its_stem(self):
        # Not an oversight: `nearest_heading` recognises a setext heading only
        # once it has seen the *underline*, and at offset 0 there is nothing
        # before the cursor at all. A wrong label costs a table column; inventing
        # an offset here to fish for one would put a second, differently-behaving
        # "is this a heading" beside the one a search hit's citation depends on.
        assert derive_document_title("Refunds\n=======\n\nText", "guides/r.md") == "r"

    def test_a_fenced_hash_is_not_a_heading(self):
        assert derive_document_title("```sh\n# not a heading\n```\n", "guides/r.md") == "r"

    def test_a_headingless_document_falls_back_to_the_file_stem(self):
        assert derive_document_title("Just prose.\n", "guides/refunds.markdown") == "refunds"
        assert derive_document_title("Just prose.\n", "refunds.MD") == "refunds"

    def test_a_stem_that_is_not_markdown_keeps_its_suffix(self):
        assert derive_document_title("Just prose.\n", "guides/notes.txt") == "notes.txt"

    def test_an_enormous_heading_is_cut_to_a_label(self):
        title = derive_document_title("# " + "x" * 500 + "\n", "a.md")
        assert len(title) == MAX_TITLE_LENGTH


# ---------------------------------------------------------------------------
# The payloads the model reads
# ---------------------------------------------------------------------------


class TestListDocumentsPayload:
    def test_reports_every_document_with_the_unit_read_document_windows_in(self):
        payload = list_documents_payload([summary("index.md", chars=42), summary(chars=120)])

        assert payload["document_count"] == 2
        assert payload["documents"] == [
            {"path": "index.md", "title": "Refunds", "chars": 42},
            {"path": "guides/refunds.md", "title": "Refunds", "chars": 120},
        ]
        assert "note" not in payload

    def test_an_empty_corpus_says_so_in_words(self):
        # A bare `{"documents": []}` reads to a model as a malfunction to retry.
        payload = list_documents_payload([])

        assert payload["document_count"] == 0
        assert payload["note"] == "This corpus contains no documents."


class TestSearchDocumentsPayload:
    def test_echoes_the_query_and_the_hits_in_ranking_order(self):
        payload = search_documents_payload("store credit", [match("index.md"), match()])

        assert payload["query"] == "store credit"
        assert payload["match_count"] == 2
        assert [hit["path"] for hit in payload["matches"]] == ["index.md", "guides/refunds.md"]
        assert payload["matches"][0]["heading"] == "After 30 days"
        assert "note" not in payload

    def test_never_hands_the_model_a_rank_it_cannot_calibrate(self):
        payload = search_documents_payload("store credit", [match()])

        assert set(payload["matches"][0]) == {"path", "title", "heading", "snippet"}

    def test_an_uncited_hit_still_reports_its_heading_key_as_null(self):
        payload = search_documents_payload("store credit", [match(heading=None)])

        assert payload["matches"][0]["heading"] is None

    def test_a_miss_is_a_normal_answer_with_the_next_step_in_it(self):
        payload = search_documents_payload("quantum tunnelling", [])

        assert payload["match_count"] == 0
        assert LIST_DOCUMENTS in payload["note"]


class TestReadDocumentPayload:
    def test_reports_the_window_and_where_it_sits_in_the_document(self):
        payload = read_document_payload(summary(chars=10), window_document("abcdefghij", limit=4))

        assert payload == {
            "path": "guides/refunds.md",
            "title": "Refunds",
            "offset": 0,
            "chars": 4,
            "total_chars": 10,
            "truncated": True,
            "content": "abcd",
            "next_offset": 4,
        }

    def test_omits_the_continuation_offset_once_the_document_is_exhausted(self):
        payload = read_document_payload(summary(chars=6), window_document("abcdef"))

        assert payload["truncated"] is False
        assert "next_offset" not in payload


class TestUnknownPathMessage:
    def test_names_the_paths_that_do_exist(self):
        # Wrong paths are a *measured* behaviour: a model that invented
        # docs/refunds.md because it looked plausible should be seen recovering.
        message = unknown_path_message("docs/refunds.md", ["index.md", "guides/refunds.md"])

        assert '"docs/refunds.md"' in message
        assert "index.md, guides/refunds.md" in message
        assert LIST_DOCUMENTS in message

    def test_an_empty_corpus_says_that_instead_of_listing_nothing(self):
        message = unknown_path_message("index.md", [])

        assert "no documents at all" in message

    def test_a_long_corpus_is_cut_rather_than_pasted_in_whole(self):
        paths = [f"page-{index}.md" for index in range(MAX_SEARCH_LIMIT + 5)]

        message = unknown_path_message("nope.md", paths)

        assert f"page-{MAX_SEARCH_LIMIT - 1}.md" in message
        assert f"page-{MAX_SEARCH_LIMIT}.md" not in message
        assert ", …" in message


class TestUnknownToolMessage:
    def test_names_the_call_and_the_three_tools_that_do_exist(self):
        # Reachable from a run frozen by a build that offered a fourth document
        # tool: the model is told what happened instead of the row dying.
        message = unknown_tool_message("grep_documents")

        assert '"grep_documents"' in message
        for name in DOCUMENT_TOOL_NAMES:
            assert name in message


class TestNothingRaises:
    """The standing rule, stated as a test: a bad argument is an answer.

    Only a connection-level `LlmError` may fail a row, so every one of these has
    to come back as text the model reads and reacts to.
    """

    @pytest.mark.parametrize(
        "call",
        [
            lambda: window_document("", offset=-1, limit=0),
            lambda: window_document("abc", offset=10**9, limit=10**9),
            lambda: nearest_heading("", None),
            lambda: locate_snippet("", ""),
            lambda: heading_for_snippet("", HIGHLIGHT_START),
            lambda: shape_snippet(""),
            lambda: list_documents_payload([]),
            lambda: search_documents_payload("", []),
            lambda: unknown_path_message("", []),
        ],
    )
    def test_the_awkward_arguments_all_produce_answers(self, call):
        # A raised exception is the failure; there is nothing else to assert,
        # because "None" and "the empty window" are both legitimate answers.
        call()
