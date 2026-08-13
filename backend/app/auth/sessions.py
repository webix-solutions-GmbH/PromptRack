"""Browser sessions: a random token in an HttpOnly cookie, its hash in the row.

Database-backed rather than a signed/stateless cookie, for one reason: a role
change, a revoked account or a sign-out has to bite on the very next request.
That is the same trade the old app made by refusing better-auth's
``session.cookieCache`` — one indexed lookup per request is nothing at this
scale, and it is what makes "signed out" mean signed out.

The raw token exists in exactly two places: the client's cookie and the request
that carries it. What is stored is its SHA-256, so a database leak yields
nothing replayable. SHA-256 and not argon2 because the token is 32 random bytes
— there is no low-entropy secret to slow an attacker down over.

The window is **30 days, sliding**: a session in daily use never expires, one
left alone for a month does. It slides at most once a day
(:data:`SESSION_REFRESH_AFTER`) so a request that changes nothing does not
become a write.
"""

import hashlib
import secrets
from datetime import timedelta

from fastapi import Request, Response
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Session as SessionRow
from app.models import User
from app.repos.scoped import utc_now

#: Named for the product, and distinct enough that a second app on the same
#: host cannot collide with it.
SESSION_COOKIE_NAME = "promptrack_session"

#: How long a session lives from its last extension.
SESSION_TTL = timedelta(days=30)

#: The window is only extended once the session is this far along, so ordinary
#: browsing does not write to the table on every request.
SESSION_REFRESH_AFTER = timedelta(days=1)

#: 32 bytes of `secrets` randomness, urlsafe-encoded (43 characters).
TOKEN_BYTES = 32


def mint_session_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_token(raw: str) -> str:
    """SHA-256 hex of a raw token — what the row stores and lookups match on."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def create_session(
    session: AsyncSession,
    user_id: int,
    *,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> str:
    """Opens a session and returns the raw token, which is never stored.

    Does not commit — the endpoint that signed the user in owns that boundary.
    """
    raw = mint_session_token()
    session.add(
        SessionRow(
            user_id=user_id,
            token_hash=hash_token(raw),
            expires_at=utc_now() + SESSION_TTL,
            last_used_at=utc_now(),
            ip_address=ip_address,
            user_agent=user_agent,
        )
    )
    await session.flush()
    return raw


async def resolve_session(session: AsyncSession, raw: str) -> User | None:
    """The signed-in user behind a cookie value, or ``None``.

    An expired row resolves to ``None`` and is left in place for
    :func:`purge_expired_sessions` — deleting it here would turn every
    unauthenticated request into a write.

    Commits when it slides the window. Safe because this runs as a guard,
    strictly before any endpoint work: the only thing there can be to commit is
    the slide itself.
    """
    now = utc_now()
    row = (
        await session.execute(
            select(SessionRow, User)
            .join(User, User.id == SessionRow.user_id)
            .where(SessionRow.token_hash == hash_token(raw))
        )
    ).first()
    if row is None:
        return None

    session_row, user = row
    if session_row.expires_at <= now:
        return None

    if session_row.expires_at - now < SESSION_TTL - SESSION_REFRESH_AFTER:
        await session.execute(
            update(SessionRow)
            .where(SessionRow.id == session_row.id)
            .values(expires_at=now + SESSION_TTL, last_used_at=now)
        )
        await session.commit()

    return user


async def revoke_session(session: AsyncSession, raw: str) -> None:
    """Signing out destroys the row, so the cookie is dead even if it is kept."""
    await session.execute(delete(SessionRow).where(SessionRow.token_hash == hash_token(raw)))


async def revoke_user_sessions(session: AsyncSession, user_id: int) -> None:
    """Signs a user out everywhere — what a password change or a lock-out needs."""
    await session.execute(delete(SessionRow).where(SessionRow.user_id == user_id))


async def purge_expired_sessions(session: AsyncSession) -> None:
    """Housekeeping, run from the sign-in path rather than a scheduler.

    Sign-in is the one moment that is both rare and already writing, so the
    table cannot grow without bound and nothing has to run on a timer.
    """
    await session.execute(delete(SessionRow).where(SessionRow.expires_at <= utc_now()))


def cookie_secure(request: Request) -> bool:
    """Whether to mark the cookie ``Secure``.

    Derived from the request rather than configured, so a developer on
    ``http://localhost`` is not silently signed out by a flag they never set and
    a TLS deployment gets the flag without one. Behind a reverse proxy this
    needs uvicorn's ``--proxy-headers`` for ``X-Forwarded-Proto`` to reach here,
    which the production entrypoint passes.
    """
    return request.url.scheme == "https"


def set_session_cookie(response: Response, request: Request, token: str) -> None:
    """HttpOnly (no script can read it) + SameSite=Lax.

    ``Lax`` is the CSRF defence: a cross-site ``POST``/``PATCH``/``DELETE``
    carries no cookie, so every mutating endpoint is unreachable from another
    origin, while a plain link into the app still arrives signed in.
    """
    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        max_age=int(SESSION_TTL.total_seconds()),
        httponly=True,
        samesite="lax",
        secure=cookie_secure(request),
        path="/",
    )


def clear_session_cookie(response: Response, request: Request) -> None:
    """Attributes have to match :func:`set_session_cookie` or browsers keep it."""
    response.delete_cookie(
        SESSION_COOKIE_NAME,
        httponly=True,
        samesite="lax",
        secure=cookie_secure(request),
        path="/",
    )
