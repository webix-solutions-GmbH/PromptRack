"""Version attribution, database-free.

The rule that decides whether a run tested a *committed* prompt or an
unversioned draft. It is pure and it is the only place that decides, so it is
worth pinning down here rather than through a run: the integration suite then
only has to show that run creation calls it with the right text.
"""

from app.services.attribution import VersionRef, head_version, is_dirty, match_version

V1 = VersionRef(id=11, version=1, content="You are a helpful assistant.")
V2 = VersionRef(id=22, version=2, content="You are a terse assistant.")
V3 = VersionRef(id=33, version=3, content="You are a helpful assistant.")  # a revert of v2

HISTORY = [V1, V2]


class TestHeadVersion:
    def test_has_no_head_before_the_first_commit(self) -> None:
        assert head_version([]) is None

    def test_is_the_highest_numbered_commit(self) -> None:
        assert head_version(HISTORY) is V2

    def test_does_not_trust_the_order_it_is_given(self) -> None:
        # "Newest" is a property of the version number, not of the order a
        # query happened to return.
        assert head_version([V2, V1]) is V2
        assert head_version([V1, V2]) is V2


class TestMatchVersion:
    def test_attributes_a_draft_that_matches_a_commit(self) -> None:
        assert match_version(V2.content, HISTORY) == V2.id

    def test_attributes_an_older_commit_the_draft_still_matches(self) -> None:
        # Clean working tree, just not at the head: the run really did test the
        # text of v1, and that is what the column records.
        assert match_version(V1.content, HISTORY) == V1.id

    def test_leaves_a_dirty_draft_unattributed(self) -> None:
        assert match_version("You are a helpful assistant. Be brief.", HISTORY) is None

    def test_leaves_an_uncommitted_prompt_unattributed(self) -> None:
        assert match_version("anything", []) is None

    def test_treats_no_prompt_at_all_as_unattributed(self) -> None:
        # A test case may reference no prompt; the column deliberately does not
        # distinguish that from a dirty draft.
        assert match_version(None, HISTORY) is None

    def test_prefers_the_newest_of_two_identical_commits(self) -> None:
        # After v3 reverts to v1's text, a run of that draft is v3 — the commit
        # that is actually the head of the history.
        assert match_version(V1.content, [V1, V2, V3]) == V3.id
        assert match_version(V1.content, [V3, V2, V1]) == V3.id

    def test_compares_bytes_and_does_not_normalise_whitespace(self) -> None:
        # A trailing newline changes what the model receives, so it is a
        # different prompt: `v1` has to mean the text of v1 exactly.
        assert match_version(V1.content + "\n", HISTORY) is None
        assert match_version(" " + V1.content, HISTORY) is None
        assert match_version(V1.content.upper(), HISTORY) is None


class TestIsDirty:
    def test_an_uncommitted_prompt_is_dirty(self) -> None:
        # Nothing has been frozen, so there is nothing to be clean against —
        # and the first commit must always be allowed.
        assert is_dirty("a draft", []) is True

    def test_a_draft_equal_to_the_head_is_clean(self) -> None:
        assert is_dirty(V2.content, HISTORY) is False

    def test_an_edited_draft_is_dirty(self) -> None:
        assert is_dirty(V2.content + " Always answer in German.", HISTORY) is True

    def test_a_draft_matching_only_an_older_version_is_dirty(self) -> None:
        # The stricter of the two questions: attribution accepts any commit,
        # the editor's indicator only the head. Restoring v1 into the draft
        # leaves work to commit, and `match_version` still attributes it.
        assert is_dirty(V1.content, HISTORY) is True
        assert match_version(V1.content, HISTORY) == V1.id
