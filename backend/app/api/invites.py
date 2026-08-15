"""`/api/invites` — single-use links that create an account.

Admin-only, because an invite is the one thing that can add a user to an
install where sign-up closed after the first account. The redemption half is
public and lives in `app.auth.router` (`/api/auth/invite/{token}`), for the
obvious reason that whoever redeems a link has no account yet.

An invite names a **role, not a person**: the admin does not have to know the
address in advance, and whoever opens the link first supplies their own. The
raw token is returned exactly once, on the `POST` that mints it, the same
one-time reveal an API token gets — so `GET` can only ever show the display
prefix.

Redeemed and revoked rows are listed rather than hidden: "who accepted which
link, and when" is the only record the app keeps of how an account came to
exist.
"""

from datetime import datetime, timedelta
from typing import Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.api.users import role_or_400
from app.auth import invites as invite_store
from app.auth import users as user_store
from app.auth.guards import Admin, DbSession
from app.auth.invites import DEFAULT_EXPIRY
from app.auth.policy import Role, parse_role
from app.models import UserInvite
from app.repos.scoped import utc_now

router = APIRouter(prefix="/invites", tags=["invites"])

#: A link that lets a stranger create an account should not sit in an inbox for
#: a year — this ceiling is far shorter than an API token's, deliberately.
MAX_EXPIRY_DAYS = 90

InviteStatus = Literal["pending", "redeemed", "revoked", "expired"]


# --------------------------------------------------------------------------
# Wire shapes
# --------------------------------------------------------------------------


class InviteView(BaseModel):
    """One row of the invites table.

    `status` is derived server-side rather than left to the client to compute:
    "expired" is a comparison against *now*, and the same rule has to decide
    whether a redemption is allowed (`app.auth.invites.is_valid`).
    """

    id: int
    role: Role
    display_prefix: str
    status: InviteStatus
    expires_at: datetime
    created_at: datetime
    created_by_name: str | None
    redeemed_at: datetime | None
    redeemed_by_name: str | None


class CreatedInviteView(InviteView):
    #: The raw secret — present exactly once, only on the response to `POST`.
    #: Never stored (only its hash is) and never returned again by `GET`. The
    #: frontend assembles the full link from `window.location.origin`, since
    #: that is the host the admin is looking at; the backend's own
    #: `request.base_url` is the wrong host behind the dev proxy or a reverse
    #: proxy that rewrites `Host`.
    token: str


class CreateInviteRequest(BaseModel):
    #: A plain string for the same reason `app.api.users.RoleRequest.role` is:
    #: a role the caller *chose* is refused when unrecognised, never coerced.
    role: str
    expires_in_days: int = Field(default=DEFAULT_EXPIRY.days, ge=1, le=MAX_EXPIRY_DAYS)


def _status(invite: UserInvite, now: datetime) -> InviteStatus:
    """Which of the four states a row is in, in the order they can occur.

    Redeemed first: a spent link is spent whatever its expiry says, and it is
    the fact worth showing.
    """
    if invite.redeemed_at is not None:
        return "redeemed"
    if invite.revoked_at is not None:
        return "revoked"
    if invite.expires_at <= now:
        return "expired"
    return "pending"


def _view(invite: UserInvite, now: datetime, names: dict[int, str]) -> InviteView:
    return InviteView(
        id=invite.id,
        # Through `parse_role` like every other role *read* — `role_or_400`
        # guards the write, so this only fires on a value another build stored,
        # and it has to agree with what redemption will do with the same row
        # (`app.auth.router.accept_invite`, which parses it the same way).
        role=parse_role(invite.role),
        display_prefix=invite.display_prefix,
        status=_status(invite, now),
        expires_at=invite.expires_at,
        created_at=invite.created_at,
        created_by_name=names.get(invite.created_by) if invite.created_by else None,
        redeemed_at=invite.redeemed_at,
        redeemed_by_name=names.get(invite.redeemed_by) if invite.redeemed_by else None,
    )


# --------------------------------------------------------------------------
# Endpoints
# --------------------------------------------------------------------------


@router.get("")
async def list_invites(actor: Admin, session: DbSession) -> list[InviteView]:
    """Pending links first, then everything spent, each newest first.

    The sort is here rather than in SQL because "pending" is a clock-dependent
    status the row does not carry; `list_invites` has already ordered newest
    first, and this sort is stable, so that survives inside each half.
    """
    del actor
    now = utc_now()
    rows = await invite_store.list_invites(session)
    names = await user_store.list_display_names(
        session, [row.created_by for row in rows] + [row.redeemed_by for row in rows]
    )
    views = [_view(row, now, names) for row in rows]
    return sorted(views, key=lambda view: view.status != "pending")


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_invite(
    body: CreateInviteRequest, actor: Admin, session: DbSession
) -> CreatedInviteView:
    now = utc_now()
    invite, raw = await invite_store.create_invite(
        session,
        role=role_or_400(body.role),
        expires_at=now + timedelta(days=body.expires_in_days),
        created_by=actor.user_id,
    )
    await session.commit()
    names = await user_store.list_display_names(session, [invite.created_by])
    return CreatedInviteView(**_view(invite, now, names).model_dump(), token=raw)


@router.delete("/{invite_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_invite(invite_id: int, actor: Admin, session: DbSession) -> None:
    """Withdraws a link that has not been used.

    A redeemed invite is refused rather than quietly marked revoked: the row is
    the record of an account that exists, and rewriting it would make the list
    lie about what happened. Deactivate the account instead.
    """
    del actor
    invite = await invite_store.get_invite(session, invite_id)
    if invite is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That invite no longer exists.")
    if invite.redeemed_at is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "That invite has already been used. Deactivate the account instead.",
        )
    await invite_store.revoke_invite(session, invite_id)
    await session.commit()
