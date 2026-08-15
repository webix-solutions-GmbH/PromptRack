"""`/api/auth` — sign-up, sign-in, sign-out, who am I, redeeming an invite, and
switching workspace.

The only rules worth stating twice:

* **The first account is the administrator, then sign-up closes forever.**
  ``/sign-up`` is the app's bootstrap and nothing else; afterwards accounts are
  created by redeeming an invite (``/invite/{token}/accept`` below, which is
  why that route is public), or provisioned by OIDC.
* **A workspace switch is a write to the user row**, never a cookie the client
  could forge — which is also why ``/me`` is the one call that tells the
  frontend which workspace it is in.

``/oidc-status`` lives here rather than in :mod:`app.auth.oidc` for one reason:
that router is only mounted when OIDC *is* configured, and the question this
answers is whether it is.

The endpoints here own their commit boundary, the same way the repository
convention hands that decision to the caller.
"""

from datetime import datetime

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import invites as invite_store
from app.auth import passwords
from app.auth import sessions as session_store
from app.auth import users as user_store
from app.auth.guards import (
    Actor,
    Admin,
    AuthError,
    CurrentUser,
    DbSession,
    active_workspace,
    actor_from_user,
)
from app.auth.oidc import oidc_configured
from app.auth.policy import Role, parse_role
from app.config import get_settings
from app.repos.customers import get_customer
from app.repos.scoped import utc_now

router = APIRouter(prefix="/auth", tags=["auth"])


# --------------------------------------------------------------------------
# Wire shapes
# --------------------------------------------------------------------------


class StatusResponse(BaseModel):
    """What an unauthenticated visitor may know: whether this install still
    needs its first account. That is what sends the frontend to `/setup`
    instead of `/login`.
    """

    signup_open: bool


class UserView(BaseModel):
    id: int
    email: str
    name: str
    role: Role


class CustomerView(BaseModel):
    id: int
    name: str
    archived: bool


class MeResponse(BaseModel):
    """Everything the SPA needs to render its shell.

    ``can_write``/``can_administer`` are sent rather than derived in the client
    because the UX contract is that a role is never *offered* a control it
    cannot use — and the answer has to come from the same predicates the guards
    ask, not from a second copy of the rules in TypeScript.
    """

    user: UserView
    can_write: bool
    can_administer: bool
    active_customer: CustomerView | None
    customers: list[CustomerView]


class SignUpRequest(BaseModel):
    email: str
    password: str = Field(min_length=passwords.MIN_PASSWORD_LENGTH)
    #: Falls back to the address, so the bootstrap form can be two fields.
    name: str | None = None

    @field_validator("email")
    @classmethod
    def _looks_like_an_address(cls, value: str) -> str:
        cleaned = value.strip()
        # Deliberately not RFC 5322: the address is an identifier here, and the
        # only thing that has to be true is that it is one non-empty token.
        if "@" not in cleaned or cleaned.startswith("@") or cleaned.endswith("@"):
            raise ValueError("Enter an email address.")
        return cleaned


class AcceptInviteRequest(SignUpRequest):
    """Exactly the bootstrap sign-up's body, deliberately: the invitee supplies
    the same three things the first account's owner did. What it cannot supply
    is a role — the invite already decided that.
    """


class InviteOfferResponse(BaseModel):
    """What the invite page may know before anyone is signed in: which role the
    link grants and how long it lasts. Never who created it, and never the
    address it will end up on — the link is bound to a redemption, not to a
    person.
    """

    role: Role
    expires_at: datetime


class OidcStatusResponse(BaseModel):
    """The identity provider as configured, read-only.

    The environment is the source of truth and stays that way (changing SSO
    means a redeploy), so this reports rather than round-trips. The client
    secret is never serialized — `secret_set` is the only thing said about it.
    `default_role` is reported through `parse_role`, so the page shows the role
    a provisioned account will actually get rather than the raw env string.
    """

    configured: bool
    issuer: str | None
    client_id: str | None
    scopes: list[str]
    default_role: Role
    secret_set: bool


class LoginRequest(BaseModel):
    email: str
    password: str


class SwitchCustomerRequest(BaseModel):
    customer_id: int


# --------------------------------------------------------------------------
# Endpoints
# --------------------------------------------------------------------------


@router.get("/status")
async def auth_status(session: DbSession) -> StatusResponse:
    return StatusResponse(signup_open=await user_store.signup_open(session))


@router.post("/sign-up", status_code=status.HTTP_201_CREATED)
async def sign_up(
    body: SignUpRequest,
    request: Request,
    response: Response,
    session: DbSession,
) -> MeResponse:
    """Creates the first (and only ever, through this route) account.

    The advisory lock and the emptiness check are one transaction, so two
    simultaneous sign-ups cannot both be stamped ``admin``: the second finds a
    non-empty table and is refused.
    """
    await user_store.lock_bootstrap(session)
    if not await user_store.signup_open(session):
        raise AuthError(
            status.HTTP_403_FORBIDDEN,
            "Sign-up is closed. Ask an administrator for an account.",
        )

    user = await user_store.create_user(
        session,
        email=body.email,
        name=(body.name or "").strip() or body.email,
        password_hash=passwords.hash_password(body.password),
    )
    token = await session_store.create_session(session, user.id, **_client(request))
    await session.commit()

    session_store.set_session_cookie(response, request, token)
    return await _me(session, actor_from_user(user, "session"))


@router.post("/login")
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    session: DbSession,
) -> MeResponse:
    """One refusal message for both halves of a wrong login.

    "No such account" and "wrong password" are the same 401 on purpose:
    distinguishing them turns the login form into an account-enumeration oracle.
    :func:`~app.auth.passwords.verify_password` pays the same argon2 cost for an
    address that does not exist, so the timing does not leak it either.
    """
    user = await user_store.find_user_by_email(session, body.email)
    verified = passwords.verify_password(user.password_hash if user else None, body.password)
    if user is None or not verified:
        raise AuthError(status.HTTP_401_UNAUTHORIZED, "Wrong email address or password.")

    if user.disabled_at is not None:
        # The one place the single-message rule above is relaxed, and only
        # *after* the password check: refusing earlier would turn the form into
        # an oracle for which accounts are deactivated, while refusing here
        # reaches nobody but the person who already holds the credential — who
        # needs to learn that retrying will not help.
        raise AuthError(status.HTTP_403_FORBIDDEN, "This account has been deactivated.")

    # The one moment the plaintext is at hand to upgrade a hash made with
    # weaker parameters than today's.
    if user.password_hash and passwords.needs_rehash(user.password_hash):
        await user_store.update_user(
            session, user.id, {"password_hash": passwords.hash_password(body.password)}
        )

    await session_store.purge_expired_sessions(session)
    token = await session_store.create_session(session, user.id, **_client(request))
    await session.commit()

    session_store.set_session_cookie(response, request, token)
    return await _me(session, actor_from_user(user, "session"))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, response: Response, session: DbSession) -> None:
    """Destroys the row as well as the cookie, so a copied cookie is dead too.

    Unauthenticated on purpose: signing out something that is already signed out
    must succeed, or a stale tab can never clear itself.
    """
    raw = request.cookies.get(session_store.SESSION_COOKIE_NAME)
    if raw:
        await session_store.revoke_session(session, raw)
        await session.commit()
    session_store.clear_session_cookie(response, request)


@router.get("/invite/{token}")
async def read_invite(token: str, session: DbSession) -> InviteOfferResponse:
    """What the invite page renders before there is an account to sign in as.

    A token matching no row at all is a 404; one that is expired, spent or
    withdrawn is a **410 Gone**. Worth the distinction: "this link is no longer
    usable" and "this link is not real" are different things for the person
    holding it to read, and anyone presenting a real token already knows it
    existed.
    """
    invite = await invite_store.find_invite_by_token(session, token)
    if invite is None:
        raise AuthError(status.HTTP_404_NOT_FOUND, "That invite link is not valid.")
    if not invite_store.is_valid(invite, utc_now()):
        raise AuthError(
            status.HTTP_410_GONE,
            "That invite link is no longer usable. Ask an administrator for a new one.",
        )
    return InviteOfferResponse(role=parse_role(invite.role), expires_at=invite.expires_at)


@router.post("/invite/{token}/accept", status_code=status.HTTP_201_CREATED)
async def accept_invite(
    token: str,
    body: AcceptInviteRequest,
    request: Request,
    response: Response,
    session: DbSession,
) -> MeResponse:
    """Redeems a link: creates the account at the invite's role and signs it in.

    The row lock is what makes "one link, one person" true under concurrency:
    ``SELECT … FOR UPDATE`` is taken *before* validity is re-checked and held
    until the commit below, so two people opening the same link at the same
    moment cannot both come out of it with an account — the second waits, then
    reads a row that is already redeemed. Same class of problem as the
    bootstrap sign-up, one step narrower: :func:`user_store.lock_bootstrap`
    takes an advisory lock because its check counts rows, and here there is a
    real row to lock.

    The role is passed to `create_user` explicitly, which bypasses its "first
    account is admin, otherwise `default_role()`" rule — correct, because the
    invite already decided.
    """
    invite = await invite_store.find_invite_by_token(session, token, for_update=True)
    if invite is None:
        raise AuthError(status.HTTP_404_NOT_FOUND, "That invite link is not valid.")
    if not invite_store.is_valid(invite, utc_now()):
        raise AuthError(
            status.HTTP_410_GONE,
            "That invite link is no longer usable. Ask an administrator for a new one.",
        )

    existing = await user_store.find_user_by_email(session, body.email)
    if existing is not None:
        # Named, unlike a failed login: the caller holds a valid invite, so
        # there is no account to enumerate that they were not already being
        # handed one way into.
        raise AuthError(
            status.HTTP_409_CONFLICT,
            f"An account for {existing.email} already exists. Sign in instead.",
        )

    user = await user_store.create_user(
        session,
        email=body.email,
        name=(body.name or "").strip() or body.email,
        password_hash=passwords.hash_password(body.password),
        role=parse_role(invite.role),
    )
    await invite_store.redeem_invite(session, invite.id, user.id)
    session_token = await session_store.create_session(session, user.id, **_client(request))
    await session.commit()

    session_store.set_session_cookie(response, request, session_token)
    return await _me(session, actor_from_user(user, "session"))


@router.get("/oidc-status")
async def oidc_status(actor: Admin) -> OidcStatusResponse:
    """Admin-only despite being read-only: an issuer URL and a client id
    describe the identity provider's topology, and there is no reason for a
    viewer to hold them.

    Reads no database — the environment is the whole answer.
    """
    del actor
    settings = get_settings()
    return OidcStatusResponse(
        configured=oidc_configured(settings),
        issuer=settings.oidc_issuer,
        client_id=settings.oidc_client_id,
        # Stored comma-separated (`OIDC_SCOPES`), sent as the list it means.
        scopes=[scope.strip() for scope in settings.oidc_scopes.split(",") if scope.strip()],
        default_role=parse_role(settings.oidc_default_role),
        secret_set=bool(settings.oidc_client_secret),
    )


@router.get("/me")
async def me(actor: CurrentUser, session: DbSession) -> MeResponse:
    return await _me(session, actor)


@router.post("/switch-customer")
async def switch_customer(
    body: SwitchCustomerRequest, actor: CurrentUser, session: DbSession
) -> MeResponse:
    """Every signed-in user may switch into every workspace.

    A workspace is a label, not a tenant: customers never log in, and the
    separation it buys is that one engagement's endpoints — i.e. base URLs with
    API keys — stay out of another's. So this needs no role beyond being signed
    in.
    """
    customer = await get_customer(session, body.customer_id)
    if customer is None:
        raise AuthError(status.HTTP_404_NOT_FOUND, "That workspace does not exist.")
    if customer.archived_at is not None:
        # Storing it would be a no-op anyway: `resolve_active_customer_id`
        # ignores an archived pointer and falls back, so a silent switch that
        # did not take is the worse answer.
        raise AuthError(
            status.HTTP_409_CONFLICT,
            f"“{customer.name}” is archived. Unarchive it before switching into it.",
        )

    await user_store.set_active_customer_id(session, actor.user_id, customer.id)
    await session.commit()
    return await _me(session, actor)


# --------------------------------------------------------------------------
# Shared bits
# --------------------------------------------------------------------------


def _client(request: Request) -> dict[str, str | None]:
    """Where a session was opened from — for the sessions list, not for auth.

    Nothing is ever *authorized* by these: an IP or a user agent that changed
    mid-session is a laptop moving between networks far more often than it is
    an attacker.
    """
    return {
        "ip_address": request.client.host if request.client else None,
        "user_agent": request.headers.get("user-agent"),
    }


async def _me(session: AsyncSession, actor: Actor) -> MeResponse:
    workspace = await active_workspace(session, actor)
    customers = [
        CustomerView(id=option.id, name=option.name, archived=option.archived)
        for option in workspace.customers
    ]
    active = next(
        (customer for customer in customers if customer.id == workspace.customer_id), None
    )
    return MeResponse(
        user=UserView(id=actor.user_id, email=actor.email, name=actor.name, role=actor.role),
        can_write=actor.can_write,
        can_administer=actor.can_administer,
        active_customer=active,
        customers=customers,
    )
