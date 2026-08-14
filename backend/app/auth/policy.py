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
