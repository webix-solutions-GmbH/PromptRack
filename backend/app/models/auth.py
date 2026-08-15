"""`users`, `sessions`, `api_tokens`, `user_invites` — the tables a `Scope` is
derived *from*.

These are global infrastructure rather than workspace data: a user's active
workspace is what produces a scope, so these tables cannot themselves be read
through a scoped repository.

The old app delegated to better-auth (`user` / `session` / `account` /
`verification`, all with text ids). The rewrite owns its sessions, so the shape
collapses to the tables below, with the integer keys the rest of the schema
uses.
"""

from datetime import datetime
from typing import Literal

from sqlalchemy import ForeignKey, Index, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

#: App role. `viewer` is the safe default for anything unrecognised — parsing
#: degrades to it, never to admin (`app.auth.policy`).
UserRole = Literal["admin", "member", "viewer"]


class User(Base):
    """A person with an account. The first one ever created is the admin."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str]
    name: Mapped[str]
    # Argon2id hash, or null for an account that only signs in through OIDC.
    password_hash: Mapped[str | None]
    # Subject claim of the OIDC identity linked to this account, if any.
    oidc_subject: Mapped[str | None] = mapped_column(unique=True)
    role: Mapped[UserRole] = mapped_column(Text, server_default="viewer")
    # When set, the account is deactivated: login is refused, live sessions are
    # revoked and API tokens stop resolving. The presence of the timestamp is
    # the whole definition — no boolean beside it to drift from it, and "since
    # when" comes free. Deliberately not `deleted_at`: deletion here is a real
    # DELETE, and a column implying otherwise would invite a soft-delete path
    # nobody wrote.
    disabled_at: Mapped[datetime | None]
    # The workspace this user is currently in — on the user row rather than in
    # a cookie, so it is unforgeable from the client and survives a session
    # refresh. `SET NULL` so archiving or deleting a workspace drops its users
    # into the fallback (oldest live workspace) instead of breaking them.
    active_customer_id: Mapped[int | None] = mapped_column(
        ForeignKey("customers.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        # Case-insensitive: two accounts differing only in the case of their
        # address are an authentication hazard, not a feature.
        Index("users_email_lower_idx", text("lower(email)"), unique=True),
    )


class Session(Base):
    """A signed-in browser session, keyed by a hash of the cookie value.

    The raw token lives only in the client's HttpOnly cookie; a database leak
    therefore yields nothing that can be replayed.
    """

    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    #: SHA-256 hex of the raw session token.
    token_hash: Mapped[str] = mapped_column(unique=True)
    expires_at: Mapped[datetime]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    #: Bumped as the sliding window is extended.
    last_used_at: Mapped[datetime | None]
    ip_address: Mapped[str | None]
    user_agent: Mapped[str | None]


class ApiToken(Base):
    """A per-user bearer token for the MCP endpoint, hashed at rest.

    32 random bytes prefixed `prk_`, stored as SHA-256 (a 256-bit random secret
    has nothing to brute-force, and every MCP request would otherwise pay an
    argon2), shown exactly once. A token acts as its owner and carries their
    role. There is deliberately no customer column: a call names its workspace.
    """

    __tablename__ = "api_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str]
    #: SHA-256 hex of the raw token.
    token_hash: Mapped[str] = mapped_column(unique=True)
    #: First 12 characters of the raw token, for recognising it in a list.
    display_prefix: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    last_used_at: Mapped[datetime | None]
    expires_at: Mapped[datetime | None]
    revoked_at: Mapped[datetime | None]


class UserInvite(Base):
    """A single-use link that lets exactly one person create an account.

    Sign-up closes forever after the first account, so this is the only way in
    afterwards short of OIDC. **There is no email column**: the invite names a
    role, not a person — the admin does not have to know the address in
    advance, and the redeemer brings their own. "One link lets in one person"
    is therefore a property of the single redemption, not of an identity the
    row is bound to.

    Hashed and prefixed exactly like an `ApiToken`, for the same reason: the
    raw value is shown once, at creation, and what is at rest can only
    recognise a link, never reconstruct one.

    Redeemed and revoked rows are kept rather than deleted, so the invites list
    can still say who accepted which link and when — the same reasoning that
    keeps `endpoint_models` rows around. Both user FKs are `SET NULL`: deleting
    the admin who sent an invite, or the account that redeemed one, must not
    take the record of it along.
    """

    __tablename__ = "user_invites"

    id: Mapped[int] = mapped_column(primary_key=True)
    #: SHA-256 hex of the raw invite token.
    token_hash: Mapped[str] = mapped_column(unique=True)
    #: First 12 characters of the raw token, for recognising it in a list.
    display_prefix: Mapped[str]
    #: The role the redeemed account is created at.
    role: Mapped[UserRole] = mapped_column(Text)
    expires_at: Mapped[datetime]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    redeemed_at: Mapped[datetime | None]
    redeemed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    revoked_at: Mapped[datetime | None]
