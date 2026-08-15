"""Per-user API tokens for the MCP endpoint (`app.mcp`).

Like `app.auth.users`/`app.auth.sessions`, this reads the auth tables
directly: `api_tokens` is global infrastructure rather than workspace data, so
it does not go through a scoped repository (see `app.models.auth.ApiToken`'s
own docstring, and `app/auth/__init__.py`).

Hashed the same way a session token is (SHA-256, not argon2): 32 random bytes
have nothing to brute-force, and every MCP request would otherwise pay an
argon2. The raw value exists in exactly two places: wherever the caller who
minted it stores it, and the one response that showed it.
"""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.policy import Role, parse_role
from app.models import ApiToken, User
from app.repos.scoped import utc_now

TOKEN_PREFIX = "prk_"

#: How much of the raw token a list view shows back, to recognise a row
#: without ever storing (or re-showing) the full value.
DISPLAY_PREFIX_LEN = 12

#: 32 bytes of `secrets` randomness, urlsafe-encoded — 43 characters before
#: the product prefix.
TOKEN_BYTES = 32


def mint_token() -> str:
    """A fresh raw token. Never stored — see `hash_token`."""
    return TOKEN_PREFIX + secrets.token_urlsafe(TOKEN_BYTES)


def hash_token(raw: str) -> str:
    """SHA-256 hex of a raw token — what the row stores and lookups match on."""
    return hashlib.sha256(raw.strip().encode("utf-8")).hexdigest()


def display_prefix(raw: str) -> str:
    return raw[:DISPLAY_PREFIX_LEN]


@dataclass(frozen=True)
class TokenOwner:
    """Who a raw token acts as, resolved by `resolve_token`."""

    token_id: int
    user_id: int
    email: str
    name: str
    role: Role


async def resolve_token(session: AsyncSession, raw: str) -> TokenOwner | None:
    """The (unrevoked, unexpired, active) owner of a raw token, or `None`.

    Bumps `last_used_at` and commits — mirrors `app.auth.sessions.resolve_session`:
    this runs from a guard, strictly before any endpoint work, so the only
    thing there is to commit is that bump.

    A **deactivated** owner resolves to `None`, which is what makes
    deactivation cut everything: an MCP client running under that account dies
    with it, without a single token row being touched. Together with
    `resolve_session` this is the whole blast radius of `users.disabled_at` —
    every `Actor` in the app is built by one of the two.
    """
    row = (
        await session.execute(
            select(ApiToken, User)
            .join(User, User.id == ApiToken.user_id)
            .where(ApiToken.token_hash == hash_token(raw))
        )
    ).first()
    if row is None:
        return None

    token, user = row
    now = utc_now()
    if user.disabled_at is not None:
        return None
    if token.revoked_at is not None:
        return None
    if token.expires_at is not None and token.expires_at <= now:
        return None

    await session.execute(update(ApiToken).where(ApiToken.id == token.id).values(last_used_at=now))
    await session.commit()

    return TokenOwner(
        token_id=token.id,
        user_id=user.id,
        email=user.email,
        name=user.name,
        role=parse_role(user.role),
    )


async def list_tokens(session: AsyncSession, user_id: int) -> list[ApiToken]:
    """One user's tokens, newest first — revoked ones included, as history."""
    statement = (
        select(ApiToken).where(ApiToken.user_id == user_id).order_by(ApiToken.created_at.desc())
    )
    return list((await session.scalars(statement)).all())


async def create_token(
    session: AsyncSession, *, user_id: int, name: str, expires_at: datetime | None
) -> tuple[ApiToken, str]:
    """Mints and stores a token, returning the row plus its one-time raw value.

    Does not commit — matches `app.auth.sessions.create_session`: the endpoint
    that issues the token owns that boundary.
    """
    raw = mint_token()
    token = ApiToken(
        user_id=user_id,
        name=name,
        token_hash=hash_token(raw),
        display_prefix=display_prefix(raw),
        expires_at=expires_at,
    )
    session.add(token)
    await session.flush()
    return token, raw


async def revoke_token(session: AsyncSession, *, token_id: int, user_id: int) -> bool:
    """Revokes one of the caller's own tokens.

    The `user_id` predicate is the ownership check, baked into the UPDATE
    itself, so one user can never revoke another's token. Returns whether a
    row actually matched (an already-revoked or foreign token does not).
    """
    result = await session.execute(
        update(ApiToken)
        .where(
            ApiToken.id == token_id,
            ApiToken.user_id == user_id,
            ApiToken.revoked_at.is_(None),
        )
        .values(revoked_at=utc_now())
    )
    return result.rowcount > 0
