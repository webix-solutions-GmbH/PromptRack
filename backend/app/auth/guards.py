"""The single enforcement point for roles — and for the request's workspace.

Every router declares what it needs as a dependency and nothing else decides:
:data:`CurrentUser` for "signed in", :data:`Writer` for any content mutation,
:data:`Admin` for infrastructure and users. What those roles *mean* is
:mod:`app.auth.policy`; this module only asks.

The workspace lives here too, because it is derived from the same identity:
:data:`CurrentScope` resolves the signed-in user's active customer into the
:class:`~app.scope.Scope` every repository function demands. That keeps
:mod:`app.scope` itself free of the session and the database, so the pure test
suite can import it — the split the old app made between ``src/db/scope.ts`` and
``src/lib/workspace.ts``.

A refusal is a JSON body with a ``message``, which is the envelope the frontend
client reads (see the handlers in :mod:`app.main`).
"""

import re
from dataclasses import dataclass
from typing import Annotated, Literal

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import Headers

from app.auth import sessions as session_store
from app.auth import users as user_store
from app.auth.policy import Role, can_administer, can_write, parse_role
from app.auth.tokens import resolve_token
from app.db import get_session
from app.models import User
from app.repos.customers import list_customer_options
from app.scope import CustomerOption, Scope, resolve_active_customer_id, scope_for_customer

_BEARER_RE = re.compile(r"^bearer\s+(.+)$", re.IGNORECASE)

#: One session per request, from `app.db`.
DbSession = Annotated[AsyncSession, Depends(get_session)]


@dataclass(frozen=True)
class Actor:
    """Who is making this request.

    ``via`` records how they proved it: a browser session today, an API token
    once :mod:`app.auth.tokens` lands. Both act as the same user and carry the
    same role — a token is its owner, which is what gives every automated call
    an accountable actor.
    """

    user_id: int
    email: str
    name: str
    role: Role
    via: Literal["session", "token"]
    token_id: int | None = None

    @property
    def can_write(self) -> bool:
        return can_write(self.role)

    @property
    def can_administer(self) -> bool:
        return can_administer(self.role)


class AuthError(HTTPException):
    """401 or 403, rendered as ``{"message": ...}``."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(status_code=status_code, detail=message)


def actor_from_user(user: User, via: Literal["session", "token"]) -> Actor:
    """The role is read through :func:`parse_role`, never trusted verbatim: the
    column is plain text, so an unrecognised value has to fail closed.
    """
    return Actor(
        user_id=user.id,
        email=user.email,
        name=user.name,
        role=parse_role(user.role),
        via=via,
    )


def presented_token(headers: Headers) -> str | None:
    """The raw API token on a request, or ``None``.

    Read from ``x-api-key`` *first*, ``Authorization: Bearer`` second — so a
    reverse proxy in front of the app that also demands HTTP basic auth can
    put its credential in ``Authorization`` and an MCP client's token in
    ``x-api-key`` in the same request, with neither overwriting the other
    (see CLAUDE.local.md's production note).
    """
    direct = headers.get("x-api-key")
    if direct and direct.strip():
        return direct.strip()

    authorization = headers.get("authorization")
    match = _BEARER_RE.match(authorization.strip()) if authorization else None
    return match.group(1).strip() if match else None


async def optional_actor(request: Request, session: DbSession) -> Actor | None:
    """The caller, or ``None`` — never a refusal.

    The one unauthenticated-tolerant entry point, used by ``GET /auth/status``
    and by everything above it in this module. An API token is tried before
    the session cookie — see :func:`presented_token` — so a request carrying
    one is authenticated as its owner even when a stale cookie is also
    present.
    """
    raw_token = presented_token(request.headers)
    if raw_token:
        owner = await resolve_token(session, raw_token)
        if owner is None:
            return None
        return Actor(
            user_id=owner.user_id,
            email=owner.email,
            name=owner.name,
            role=owner.role,
            via="token",
            token_id=owner.token_id,
        )

    raw = request.cookies.get(session_store.SESSION_COOKIE_NAME)
    if not raw:
        return None
    user = await session_store.resolve_session(session, raw)
    if user is None:
        return None
    return actor_from_user(user, "session")


async def current_user(actor: Annotated[Actor | None, Depends(optional_actor)]) -> Actor:
    if actor is None:
        raise AuthError(status.HTTP_401_UNAUTHORIZED, "Sign in to continue.")
    return actor


CurrentUser = Annotated[Actor, Depends(current_user)]


async def require_writer(actor: CurrentUser) -> Actor:
    """Any content mutation: prompts, versions, test cases, runs, ratings."""
    if not actor.can_write:
        raise AuthError(status.HTTP_403_FORBIDDEN, "Your account is read-only.")
    return actor


async def require_admin(actor: CurrentUser) -> Actor:
    """Endpoints, toolsets, workspaces, user management."""
    if not actor.can_administer:
        raise AuthError(status.HTTP_403_FORBIDDEN, "Administrator access is required.")
    return actor


Writer = Annotated[Actor, Depends(require_writer)]
Admin = Annotated[Actor, Depends(require_admin)]


@dataclass(frozen=True)
class ActiveWorkspace:
    """The workspace a request runs in, plus everything the switcher offers.

    ``customer_id`` is ``None`` only on a brand-new install that has no
    workspace yet — the state between the first sign-up and the first customer
    being created.
    """

    customer_id: int | None
    customers: list[CustomerOption]


async def active_workspace(session: AsyncSession, actor: Actor) -> ActiveWorkspace:
    """Resolves the user's stored pointer, healing it when it is stale.

    A pointer to a workspace that was archived or deleted out from under the
    user must degrade to a working session rather than an empty app, so
    :func:`~app.scope.resolve_active_customer_id` falls back to the oldest live
    workspace and the resolution is written back — one place says which
    workspace a user is in, and it stays true.

    Commits its own write for the same reason :func:`resolve_session` does: this
    runs as a guard, before any endpoint work.
    """
    stored = await user_store.get_active_customer_id(session, actor.user_id)
    customers = await list_customer_options(session)
    resolved = resolve_active_customer_id(stored, customers)
    if resolved != stored:
        await user_store.set_active_customer_id(session, actor.user_id, resolved)
        await session.commit()
    return ActiveWorkspace(customer_id=resolved, customers=customers)


async def current_scope(session: DbSession, actor: CurrentUser) -> Scope:
    """The scope every repository call in a request handler starts from.

    ``scope_for_customer`` is the constructor that means "the signed-in user's
    active workspace" — the only one a request may use; the other two exist for
    background work and for the deliberate system-wide escape hatch.
    """
    workspace = await active_workspace(session, actor)
    if workspace.customer_id is None:
        # Not an authorization failure — the caller is signed in and allowed;
        # there is simply nowhere yet for a scoped query to run. Only reachable
        # between the first sign-up and the first workspace.
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "No customer workspace exists yet. Create one before using the app.",
        )
    return scope_for_customer(workspace.customer_id)


CurrentScope = Annotated[Scope, Depends(current_scope)]
