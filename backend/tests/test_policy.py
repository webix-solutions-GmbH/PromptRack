"""Role semantics — the pure half of auth.

Everything a guard decides comes from these three functions, so this suite is
where "a viewer cannot write" and "an unknown role is not an admin" are
actually pinned down. No database, no request.
"""

from typing import get_args

from app.auth.policy import (
    ROLE_DESCRIPTIONS,
    ROLE_LABELS,
    ROLES,
    can_administer,
    can_write,
    parse_role,
)
from app.models.auth import UserRole


class TestVocabulary:
    def test_roles_are_exactly_the_column_literal(self) -> None:
        # One source of truth: a role added to the column cannot go missing
        # from the policy that decides what it may do.
        assert ROLES == get_args(UserRole)

    def test_every_role_has_a_label_and_a_description(self) -> None:
        assert set(ROLE_LABELS) == set(ROLES)
        assert set(ROLE_DESCRIPTIONS) == set(ROLES)


class TestParseRole:
    def test_keeps_every_known_role(self) -> None:
        for role in ROLES:
            assert parse_role(role) == role

    def test_degrades_anything_unrecognised_to_viewer(self) -> None:
        # The column is plain text (adding a role needs no migration), so a
        # value this build does not know has to fail closed — never to admin.
        for value in ("owner", "ADMIN", "Admin", "", "  admin", None, 1, ["admin"]):
            assert parse_role(value) == "viewer"


class TestCanWrite:
    def test_admins_and_members_may_change_content(self) -> None:
        assert can_write("admin")
        assert can_write("member")

    def test_viewers_may_not(self) -> None:
        assert not can_write("viewer")


class TestCanAdminister:
    def test_only_admins_may_touch_credentials_and_users(self) -> None:
        assert can_administer("admin")
        assert not can_administer("member")
        assert not can_administer("viewer")

    def test_administration_implies_write(self) -> None:
        # Not a tautology worth skipping: a guard chain that demanded both would
        # be wrong if these two ever diverged.
        for role in ROLES:
            assert not can_administer(role) or can_write(role)
