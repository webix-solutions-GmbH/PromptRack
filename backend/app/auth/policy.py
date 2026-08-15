"""What each role may do — the only place role semantics are written down.

Pure on purpose: the request guards, the booleans the frontend hides controls
behind and the MCP read-only gate all ask these two predicates, so the answer
cannot drift between call sites.

The role *vocabulary* comes from :data:`app.models.auth.UserRole` rather than
being repeated here, so a role added to the column cannot be missing from
:data:`ROLES`.
"""

from typing import get_args

from app.models.auth import UserRole as Role

#: Every role, most privileged first. Derived from the column's own `Literal`.
ROLES: tuple[Role, ...] = get_args(Role)

ROLE_LABELS: dict[Role, str] = {
    "admin": "Admin",
    "member": "Member",
    "viewer": "Viewer",
}

ROLE_DESCRIPTIONS: dict[Role, str] = {
    "admin": "Everything a member can do, plus user management, endpoints and toolset credentials.",
    "member": "Prompts, versions, test cases, runs and ratings.",
    "viewer": "Read-only.",
}


def parse_role(value: object) -> Role:
    """Unknown, legacy and missing values degrade to the least privileged role.

    Never to admin: the column is plain text (adding a role needs no migration),
    so a value this build does not recognise has to fail closed.
    """
    if isinstance(value, str) and value in ROLES:
        return value  # type: ignore[return-value]
    return "viewer"


def can_write(role: Role) -> bool:
    """May change content: prompts, versions, test cases, runs, ratings."""
    return role in ("admin", "member")


def can_administer(role: Role) -> bool:
    """May change infrastructure and users: endpoints, toolsets, roles, tokens.

    The line is content vs. credentials — an endpoint is a base URL with an API
    key, a toolset is an MCP URL with headers.
    """
    return role == "admin"


def is_self(actor_id: int, target_id: int) -> bool:
    """Whether an admin is acting on their own account.

    Which the user-management routes refuse outright, for role changes,
    deactivation and deletion alike: an admin who demotes or disables
    themselves has locked themselves out of the only surface that could undo
    it, and nothing short of database access gets them back.

    **This refusal is also what keeps an administrator standing.** An admin can
    only ever act on *someone else*, so whatever they do, they themselves are
    still an enabled admin afterwards — which is the whole of the "an install
    always has an admin who can sign in" guarantee. See
    :func:`would_remove_last_admin` for the backstop behind it.
    """
    return actor_id == target_id


def would_remove_last_admin(
    *, target_role: Role, target_disabled: bool, new_role: Role | None, admin_count: int
) -> bool:
    """Whether an action on this target would leave the install with no
    administrator who can sign in.

    One rule for all three destructive actions: a role change passes the role
    it is moving to, a deactivation or a deletion passes ``None`` — both stop
    the target from being an effective admin, and neither has a new role to
    name. ``admin_count`` is how many *effective* admins there are right now
    (:func:`app.auth.users.count_admins`, which leaves out disabled accounts),
    the target included when they are one of them.

    ``target_disabled`` is what keeps this predicate agreeing with that count:
    an account that cannot sign in is not an effective admin, so removing it
    removes nothing and the answer is always ``False``. Asking by role alone
    would refuse deleting a long-deactivated admin whenever one enabled admin
    was left — a refusal that protects nobody.

    **Unreachable for an enabled target, by design and not by accident.** The
    routes refuse a self-target first (:func:`is_self`), so the caller is
    always an enabled admin other than the target; if the target is an enabled
    admin too, that is already two of them. This stays as the explicit backstop
    to that invariant — the guarantee lives in the self-refusal, and deleting
    either one because the other makes it look dead is how an install locks
    itself out.
    """
    if target_role != "admin":
        return False
    if target_disabled:
        return False
    if new_role == "admin":
        return False
    return admin_count <= 1
