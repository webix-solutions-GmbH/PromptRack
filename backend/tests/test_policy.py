"""Role semantics — the pure half of auth.

Everything a guard decides comes from these functions, so this suite is where
"a viewer cannot write" and "an unknown role is not an admin" are actually
pinned down. The two user-management predicates live here for the same reason:
`/api/users`' refusals are only as trustworthy as the rules behind them, and
neither rule needs a database to be true. No database, no request.
"""

from typing import get_args

from app.auth.policy import (
    ROLE_DESCRIPTIONS,
    ROLE_LABELS,
    ROLES,
    can_administer,
    can_write,
    is_self,
    parse_role,
    would_remove_last_admin,
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


class TestIsSelf:
    def test_recognises_the_actors_own_account(self) -> None:
        assert is_self(7, 7)

    def test_anybody_else_is_not(self) -> None:
        assert not is_self(7, 8)


class TestWouldRemoveLastAdmin:
    def test_demoting_the_last_admin_is_caught(self) -> None:
        for new_role in ("member", "viewer"):
            assert would_remove_last_admin(
                target_role="admin", target_disabled=False, new_role=new_role, admin_count=1
            )

    def test_demoting_one_of_two_admins_is_fine(self) -> None:
        assert not would_remove_last_admin(
            target_role="admin", target_disabled=False, new_role="viewer", admin_count=2
        )

    def test_deactivating_the_last_admin_is_caught(self) -> None:
        # `None` is what a deactivation and a deletion both pass: neither has a
        # new role to name, and both stop the target being an effective admin.
        assert would_remove_last_admin(
            target_role="admin", target_disabled=False, new_role=None, admin_count=1
        )

    def test_deleting_the_last_admin_is_caught(self) -> None:
        assert would_remove_last_admin(
            target_role="admin", target_disabled=False, new_role=None, admin_count=1
        )

    def test_deleting_one_of_two_admins_is_fine(self) -> None:
        assert not would_remove_last_admin(
            target_role="admin", target_disabled=False, new_role=None, admin_count=2
        )

    def test_re_stamping_the_last_admin_as_admin_is_not_a_removal(self) -> None:
        # A no-op role change must not be refused as if it emptied the install.
        assert not would_remove_last_admin(
            target_role="admin", target_disabled=False, new_role="admin", admin_count=1
        )

    def test_a_deactivated_admin_is_not_the_last_administrator(self) -> None:
        # They cannot sign in, so `count_admins` already left them out and
        # removing them removes nothing. Asking by role alone would refuse
        # deleting a long-retired admin whenever one enabled admin was left —
        # a refusal that protects nobody.
        for new_role in ("member", "viewer", None):
            for admin_count in (0, 1, 2):
                assert not would_remove_last_admin(
                    target_role="admin",
                    target_disabled=True,
                    new_role=new_role,
                    admin_count=admin_count,
                )

    def test_no_action_on_a_non_admin_ever_trips_it(self) -> None:
        # Whatever the count says: a member or a viewer was never holding the
        # install open in the first place.
        for target_role in ("member", "viewer"):
            for target_disabled in (False, True):
                for new_role in ("admin", "member", "viewer", None):
                    for admin_count in (0, 1, 5):
                        assert not would_remove_last_admin(
                            target_role=target_role,
                            target_disabled=target_disabled,
                            new_role=new_role,
                            admin_count=admin_count,
                        )

    def test_an_install_already_without_an_enabled_admin_still_refuses(self) -> None:
        # Zero is reachable in principle — every admin deactivated by hand —
        # and an enabled target could then only be an admin whose row says so
        # while the count does not. Refusing is the safe reading.
        assert would_remove_last_admin(
            target_role="admin", target_disabled=False, new_role=None, admin_count=0
        )
