"""The `users` table.

Not a repository: ``users`` carries no ``customer_id`` and never will — a user's
active workspace is what *produces* a :class:`~app.scope.Scope`, so reading the
row through a scoped query would be circular. That is the same exemption
:mod:`app.repos.customers` documents, and the reason these queries live under
``app/auth`` instead.

Sessions are handed in by the caller and **never committed here**, matching the
repository convention: the request boundary decides where the unit of work ends
(:mod:`app.auth.router` is the caller that commits).
"""

from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.policy import Role, parse_role
from app.config import get_settings
from app.models import User


def default_role() -> Role:
    """The role an account gets when nobody names one and it is not the first.

    Reads `OIDC_DEFAULT_ROLE` (default `member`) through `parse_role`, so an
    unrecognised value still lands on viewer, never admin — the rule every
    other role read in this app follows. A function rather than a
    module-level constant so it is resolved at call time against whatever
    `get_settings()` currently returns, rather than frozen at import.
    """
    return parse_role(get_settings().oidc_default_role)

#: Advisory-lock key for the "first account is the administrator" decision.
#: An arbitrary constant; it only has to be unique among the app's own locks
#: (run execution takes `pg_try_advisory_lock(run_id)`, so small integers are
#: spoken for and this one is deliberately far away from them).
_BOOTSTRAP_LOCK_KEY = 0x70726B5F75736572  # "prk_user"


async def count_users(session: AsyncSession) -> int:
    return await session.scalar(select(func.count()).select_from(User)) or 0


async def count_admins(session: AsyncSession, *, exclude_disabled: bool = True) -> int:
    """How many administrators this install has.

    Disabled accounts are left out by default because the question every caller
    is really asking is "how many admins can still sign in": a deactivated
    admin cannot, so counting them as the last remaining one would let an
    install lock itself out while appearing protected. Only the exact stored
    value counts — anything else is not an admin (:func:`parse_role` degrades
    it to viewer), so no parsing is needed to ask this in SQL.
    """
    statement = select(func.count()).select_from(User).where(User.role == "admin")
    if exclude_disabled:
        statement = statement.where(User.disabled_at.is_(None))
    return await session.scalar(statement) or 0


async def signup_open(session: AsyncSession) -> bool:
    """Whether the bootstrap sign-up is still available.

    Open exactly while the table is empty: the first account is the
    administrator, and sign-up closes forever after it. Every later account is
    created by an admin or provisioned by OIDC.
    """
    return await count_users(session) == 0


async def lock_bootstrap(session: AsyncSession) -> None:
    """Serialises the first-account decision against a concurrent sign-up.

    Two requests arriving together would otherwise both read an empty table and
    both be stamped ``admin``. A transaction-scoped advisory lock is enough: it
    is taken by the one code path that reads the count in order to write it, and
    it is released by the commit that closes sign-up.
    """
    await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _BOOTSTRAP_LOCK_KEY})


async def get_user(session: AsyncSession, user_id: int) -> User | None:
    return await session.get(User, user_id)


async def list_users(session: AsyncSession) -> list[User]:
    """Every account, oldest first — the admin page's whole table.

    Ordered by id rather than by name so the first account, the one the app's
    bootstrap made an administrator, stays at the top where it explains itself.
    """
    return list((await session.scalars(select(User).order_by(User.id))).all())


async def find_user_by_email(session: AsyncSession, email: str) -> User | None:
    """The account for an address, matched case-insensitively.

    Case-insensitively because the unique index is on ``lower(email)``: two
    accounts differing only in case are an authentication hazard, so the lookup
    has to agree with the constraint that prevents them.
    """
    statement = select(User).where(func.lower(User.email) == func.lower(email))
    return (await session.scalars(statement)).first()


async def list_display_names(
    session: AsyncSession, user_ids: Iterable[int | None]
) -> dict[int, str]:
    """The name to show for a batch of users, keyed by id — one query rather
    than one per row, for a version list's "author" column or similar.

    Falls back to the address when the name is blank. A ``None`` id (no
    author, or the user's row is gone — ``created_by``/``deployed_by`` are
    both ``SET NULL`` on delete) is simply skipped; the caller reads a
    missing key as "no name to show".
    """
    ids = {user_id for user_id in user_ids if user_id is not None}
    if not ids:
        return {}
    statement = select(User.id, User.name, User.email).where(User.id.in_(ids))
    return {
        user_id: (name.strip() or email)
        for user_id, name, email in (await session.execute(statement)).all()
    }


async def find_user_by_oidc_subject(session: AsyncSession, subject: str) -> User | None:
    """The account already linked to an OIDC identity, if any.

    Tried before :func:`find_user_by_email` on every OIDC sign-in
    (:mod:`app.auth.oidc`): the subject claim is what stays stable across a
    provider-side email change, where the address would not.
    """
    return await session.scalar(select(User).where(User.oidc_subject == subject))


async def create_user(
    session: AsyncSession,
    *,
    email: str,
    name: str,
    password_hash: str | None = None,
    oidc_subject: str | None = None,
    role: Role | None = None,
) -> User:
    """Creates an account, stamping the role when the caller does not name one.

    The first account ever created is the administrator — the app's bootstrap,
    and the reason no role is configured anywhere before the first login. Every
    creation path goes through here so that rule cannot be forgotten by one of
    them; :func:`lock_bootstrap` is what makes it safe under concurrency.
    """
    if role is None:
        role = "admin" if await count_users(session) == 0 else default_role()
    user = User(
        email=email,
        name=name,
        password_hash=password_hash,
        oidc_subject=oidc_subject,
        role=role,
    )
    session.add(user)
    await session.flush()
    return user


async def update_user(session: AsyncSession, user_id: int, values: Mapping[str, Any]) -> None:
    """Patches the named columns only."""
    if not values:
        return
    await session.execute(update(User).where(User.id == user_id).values(**values))


async def set_role(session: AsyncSession, user_id: int, role: Role) -> None:
    """Named rather than inlined as an :func:`update_user` call, so the refusal
    rules in front of it (:func:`~app.auth.policy.is_self`,
    :func:`~app.auth.policy.would_remove_last_admin`) have one obvious thing to
    guard and the call site reads as the intent it is.
    """
    await update_user(session, user_id, {"role": role})


async def set_disabled(session: AsyncSession, user_id: int, disabled_at: datetime | None) -> None:
    """Deactivates (a timestamp) or reactivates (``None``) an account.

    The timestamp alone is the state — see `app.models.auth.User.disabled_at`.
    Revoking the user's live sessions is the caller's second step rather than
    part of this one: the write and the sign-out belong to the same request,
    but only one of them is a change to the row.
    """
    await update_user(session, user_id, {"disabled_at": disabled_at})


async def delete_user(session: AsyncSession, user_id: int) -> None:
    """Really deletes the row. Sessions and API tokens go with it (`CASCADE`);
    what the account authored does not — `prompt_versions.created_by`,
    `prompts.deployed_by` and both `user_invites` columns are `SET NULL`, so
    history survives its author and simply becomes unattributed.
    """
    await session.execute(delete(User).where(User.id == user_id))


async def get_active_customer_id(session: AsyncSession, user_id: int) -> int | None:
    return await session.scalar(select(User.active_customer_id).where(User.id == user_id))


async def set_active_customer_id(
    session: AsyncSession, user_id: int, customer_id: int | None
) -> None:
    """Moves the user into a workspace.

    The pointer lives on the user row rather than in a cookie: it cannot be
    forged from the client, and it survives a session refresh. ``SET NULL`` on
    the FK means archiving or deleting a workspace drops its users into the
    fallback rather than breaking them.
    """
    await session.execute(
        update(User).where(User.id == user_id).values(active_customer_id=customer_id)
    )
