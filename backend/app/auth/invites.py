"""Single-use invite links — the only way into an install after the first
account, short of OIDC.

Structurally a sibling of :mod:`app.auth.tokens`, because it is the same
problem: a secret shown exactly once, stored as a SHA-256 (32 random bytes have
nothing to brute-force, and an argon2 would buy nothing), recognised later in a
list by a short display prefix. The raw value exists in two places only —
wherever the admin pasted the link, and the one response that showed it.

Like `tokens.py` this lives under ``app/auth/`` rather than ``app/repos/``:
invites carry no ``customer_id`` and take no :class:`~app.scope.Scope`. They are
org structure, not workspace data.

Sessions are handed in by the caller and **never committed here**, matching the
repository convention — the request boundary decides where the unit of work
ends.
"""

import hashlib
import secrets
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.policy import Role
from app.models import UserInvite
from app.repos.scoped import utc_now

#: Distinct from `prk_` (an API token) so a pasted secret says what it is.
INVITE_PREFIX = "pri_"

#: How much of the raw token the invites list shows back, to recognise a row
#: without ever storing (or re-showing) the full value.
DISPLAY_PREFIX_LEN = 12

#: 32 bytes of `secrets` randomness, urlsafe-encoded — 43 characters before
#: the product prefix.
TOKEN_BYTES = 32

#: What an invite is worth when nobody names an expiry. Short on purpose: a
#: link that lets one stranger create an account should not sit in an inbox
#: for a quarter.
DEFAULT_EXPIRY = timedelta(days=7)


def mint_invite_token() -> str:
    """A fresh raw invite token. Never stored — see `hash_token`."""
    return INVITE_PREFIX + secrets.token_urlsafe(TOKEN_BYTES)


def hash_token(raw: str) -> str:
    """SHA-256 hex of a raw token — what the row stores and lookups match on."""
    return hashlib.sha256(raw.strip().encode("utf-8")).hexdigest()


def display_prefix(raw: str) -> str:
    return raw[:DISPLAY_PREFIX_LEN]


def is_valid(invite: UserInvite, now: datetime) -> bool:
    """Whether this link can still be redeemed.

    ``now`` is a parameter rather than read from the clock so the rule is
    unit-testable without a database and without freezing time. Pure for the
    same reason :mod:`app.auth.policy` is: redemption and the list view both
    ask it, and they must not be able to disagree about what "pending" means.
    """
    return invite.revoked_at is None and invite.redeemed_at is None and invite.expires_at > now


async def create_invite(
    session: AsyncSession, *, role: Role, expires_at: datetime, created_by: int | None
) -> tuple[UserInvite, str]:
    """Mints and stores an invite, returning the row plus its one-time raw token.

    Does not commit — matches `create_token`/`create_session`: the endpoint
    that issues the secret owns that boundary.
    """
    raw = mint_invite_token()
    invite = UserInvite(
        token_hash=hash_token(raw),
        display_prefix=display_prefix(raw),
        role=role,
        expires_at=expires_at,
        created_by=created_by,
    )
    session.add(invite)
    await session.flush()
    return invite, raw


async def list_invites(session: AsyncSession) -> list[UserInvite]:
    """Every invite, newest first — redeemed and revoked ones included.

    They are history, not clutter: "who accepted which link, and when" is the
    only record this app keeps of how an account came to exist.
    """
    return list((await session.scalars(select(UserInvite).order_by(UserInvite.id.desc()))).all())


async def get_invite(session: AsyncSession, invite_id: int) -> UserInvite | None:
    return await session.get(UserInvite, invite_id)


async def find_invite_by_token(
    session: AsyncSession, raw: str, *, for_update: bool = False
) -> UserInvite | None:
    """The invite a raw link names, valid or not — the caller decides.

    ``for_update`` takes a row lock, held until the caller's commit, which is
    what makes redemption safe against two people opening the same link at the
    same moment: the second waits, then re-reads a row that is already
    redeemed. Narrower than the advisory lock the bootstrap sign-up takes,
    because here there is a real row to lock.
    """
    statement = select(UserInvite).where(UserInvite.token_hash == hash_token(raw))
    if for_update:
        statement = statement.with_for_update()
    return (await session.scalars(statement)).first()


async def redeem_invite(session: AsyncSession, invite_id: int, user_id: int) -> None:
    """Marks the invite spent by the account it just created.

    The ``redeemed_at IS NULL`` predicate is the single-use rule baked into the
    UPDATE itself, so it holds even if a caller ever reaches here without the
    row lock.
    """
    await session.execute(
        update(UserInvite)
        .where(UserInvite.id == invite_id, UserInvite.redeemed_at.is_(None))
        .values(redeemed_at=utc_now(), redeemed_by=user_id)
    )


async def revoke_invite(session: AsyncSession, invite_id: int) -> None:
    """Withdraws a link that has not been used. Already-revoked is a no-op."""
    await session.execute(
        update(UserInvite)
        .where(UserInvite.id == invite_id, UserInvite.revoked_at.is_(None))
        .values(revoked_at=utc_now())
    )
