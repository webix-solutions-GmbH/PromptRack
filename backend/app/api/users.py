"""`/api/users` — the administrator's view of who has an account.

Admin-only throughout, and guard-as-parameter like every other router here: no
router-level `dependencies`, so each route says what it needs where it is read.

Two refusals stand in front of every destructive route, **in this order**:

1. **Acting on your own account** is a 409 — for role change, deactivation and
   deletion alike. An admin who demotes or disables themselves has locked
   themselves out of the only surface that could undo it, and nothing short of
   database access gets them back.
2. **A change that would leave no administrator who can sign in** is a 409.

An unknown id is a 404, raised between the two: the self check needs no row,
and the last-admin question cannot be asked without the target's role.

Deactivation, not deletion, is the reversible answer to "this person is gone".
Deletion is here because an account created by mistake should not have to be
lived with; it takes the user's sessions and API tokens with it (`CASCADE`) and
leaves what they authored in place but unattributed (`SET NULL` on
`prompt_versions.created_by`, `prompts.deployed_by` and both `user_invites`
columns).
"""

from datetime import datetime

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import sessions as session_store
from app.auth import users as user_store
from app.auth.guards import Actor, Admin, DbSession
from app.auth.policy import ROLES, Role, is_self, parse_role, would_remove_last_admin
from app.models import User
from app.repos.scoped import utc_now

router = APIRouter(prefix="/users", tags=["users"])


# --------------------------------------------------------------------------
# Wire shapes
# --------------------------------------------------------------------------


class UserView(BaseModel):
    """One row of the users table.

    `has_password`/`has_oidc` are booleans and never the underlying values: the
    page shows *how* an account signs in, and a subject claim is not something
    a list needs to carry.
    """

    id: int
    email: str
    name: str
    role: Role
    disabled_at: datetime | None
    created_at: datetime
    has_password: bool
    has_oidc: bool


class RoleRequest(BaseModel):
    #: A plain string rather than `Role`, so an unrecognised value becomes this
    #: router's own 400 naming the roles that do exist, rather than a schema
    #: 422 — see `role_or_400`.
    role: str


def _view(user: User) -> UserView:
    return UserView(
        id=user.id,
        email=user.email,
        name=user.name,
        # Through `parse_role` like every other role read: the column is plain
        # text, so a value this build does not recognise fails closed — and the
        # page has to show the role the guards will actually enforce.
        role=parse_role(user.role),
        disabled_at=user.disabled_at,
        created_at=user.created_at,
        has_password=user.password_hash is not None,
        has_oidc=user.oidc_subject is not None,
    )


def role_or_400(value: str) -> Role:
    """A role the *caller chose*, refused rather than coerced when unrecognised.

    Deliberately the opposite of :func:`~app.auth.policy.parse_role`, whose
    degrade-to-viewer is safe precisely because it runs on a value already
    stored. Here the caller is choosing, and quietly seating someone at viewer
    when the request said admin would be a lie about what happened.

    Shared with `app.api.invites`, which lets an admin pick a role the same way
    and owes the same answer.
    """
    if value in ROLES:
        return value  # type: ignore[return-value]
    raise HTTPException(
        status.HTTP_400_BAD_REQUEST,
        f'"{value}" is not a role. Choose one of: {", ".join(ROLES)}.',
    )


async def _get_or_404(session: AsyncSession, user_id: int) -> User:
    user = await user_store.get_user(session, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That user no longer exists.")
    return user


def _refuse_self(actor: Actor, user_id: int) -> None:
    if is_self(actor.user_id, user_id):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "You cannot change your own account here. Ask another administrator.",
        )


async def _refuse_last_admin(
    session: AsyncSession, user_id: int, new_role: Role | None, action: str
) -> None:
    """404 the unknown target, then refuse an install locking itself out.

    Both halves speak of admins who can actually sign in: `count_admins` leaves
    out deactivated accounts, and `target_disabled` says whether the target is
    one of them, so the predicate and the count cannot disagree about who
    counts. The refusal that actually keeps an administrator standing is the
    self-check in front of this one — see `app.auth.policy.is_self`.

    Written as its own function so **the row it reads falls out of scope before
    the caller's UPDATE** — the same reason `app.api.customers._refuse_if_base`
    is, and with the same consequence if it were not: the identity map is
    weakly referenced, and an instance the route kept alive across an
    ORM-enabled UPDATE would be handed straight back by the re-read afterwards.
    """
    user = await _get_or_404(session, user_id)
    if would_remove_last_admin(
        target_role=parse_role(user.role),
        target_disabled=user.disabled_at is not None,
        new_role=new_role,
        admin_count=await user_store.count_admins(session),
    ):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{user.email} is the only administrator who can sign in. "
            f"Promote someone else before {action}.",
        )


async def _view_by_id(session: AsyncSession, user_id: int) -> UserView:
    """Builds the response by id, after the commit, rather than from whatever
    instance the guards happened to load before the write.
    """
    return _view(await _get_or_404(session, user_id))


# --------------------------------------------------------------------------
# Endpoints
# --------------------------------------------------------------------------


@router.get("")
async def list_users(actor: Admin, session: DbSession) -> list[UserView]:
    del actor
    return [_view(user) for user in await user_store.list_users(session)]


@router.put("/{user_id}/role")
async def set_role(user_id: int, body: RoleRequest, actor: Admin, session: DbSession) -> UserView:
    _refuse_self(actor, user_id)
    role = role_or_400(body.role)
    await _refuse_last_admin(session, user_id, role, "changing their role")

    await user_store.set_role(session, user_id, role)
    await session.commit()
    return await _view_by_id(session, user_id)


@router.post("/{user_id}/deactivate")
async def deactivate_user(user_id: int, actor: Admin, session: DbSession) -> UserView:
    """Cuts the account off everywhere, immediately.

    The stored timestamp is what refuses the next login and what stops the
    user's API tokens resolving (`app.auth.tokens.resolve_token`), so an MCP
    client running under this account dies with it. The session rows are
    deleted outright on top of that, so an open browser tab is signed out on
    its very next request rather than at the end of its 30-day window.
    """
    _refuse_self(actor, user_id)
    await _refuse_last_admin(session, user_id, None, "deactivating them")

    await user_store.set_disabled(session, user_id, utc_now())
    await session_store.revoke_user_sessions(session, user_id)
    await session.commit()
    return await _view_by_id(session, user_id)


@router.post("/{user_id}/reactivate")
async def reactivate_user(user_id: int, actor: Admin, session: DbSession) -> UserView:
    """Lets the account sign in again — and nothing more.

    Sessions are not restored: they were deleted, and signing in again is both
    the simpler and the more honest way back. Neither refusal applies here —
    this action removes no administrator, and a deactivated user cannot be the
    one making the request.
    """
    del actor
    await _get_or_404(session, user_id)
    await user_store.set_disabled(session, user_id, None)
    await session.commit()
    return await _view_by_id(session, user_id)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(user_id: int, actor: Admin, session: DbSession) -> None:
    _refuse_self(actor, user_id)
    await _refuse_last_admin(session, user_id, None, "deleting them")

    await user_store.delete_user(session, user_id)
    await session.commit()
