"""Optional OIDC sign-in via Authlib's generic provider.

Absent `OIDC_ISSUER`/`OIDC_CLIENT_ID`, this module mounts no routes at all —
`app.api` only includes :data:`router` when :func:`oidc_configured` says yes,
so an install that never asked for SSO gets no `/api/auth/oidc/*` surface and
no "Single sign-on" affordance for the frontend to wire up later (Task 2.3).

Uses Authlib's Starlette integration, which needs `request.session` to stash
the CSRF `state` (and, for OIDC, the `nonce`) between the redirect to the
provider and the callback — `app.main` adds Starlette's `SessionMiddleware`
for exactly that, and only when OIDC is configured. That session is unrelated
to `app.auth.sessions`: a short-lived, provider-round-trip artifact, not how a
signed-in user is recognised afterwards. The callback exchanges it for one of
our own sessions the same way `/auth/login` does.

Only one provider at a time — a single configured issuer, not a menu of them
— matching the old app's ``genericOAuth`` config.
"""

from typing import Any

from authlib.integrations.starlette_client import OAuth
from fastapi import APIRouter, HTTPException, Request, status
from starlette.responses import RedirectResponse

from app.auth import sessions as session_store
from app.auth import users as user_store
from app.auth.guards import DbSession
from app.auth.policy import Role, parse_role
from app.config import Settings, get_settings

#: The name Authlib registers the provider under, and what the callback route
#: is named for `request.url_for`.
PROVIDER_NAME = "oidc"

router = APIRouter(prefix="/auth/oidc", tags=["auth"])

_oauth: OAuth | None = None


def oidc_configured(settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    return bool(settings.oidc_issuer and settings.oidc_client_id)


def _discovery_url(issuer: str) -> str:
    return f"{issuer.rstrip('/')}/.well-known/openid-configuration"


def _client() -> Any:
    """The registered Authlib client, built once against the configured issuer.

    Deferred rather than built at import time: importing this module must
    stay side-effect-free (in particular, safe when OIDC is not configured at
    all) so the pure test suite can still import :mod:`app.auth` freely.
    """
    global _oauth
    if _oauth is None:
        settings = get_settings()
        if not oidc_configured(settings):
            raise HTTPException(status.HTTP_404_NOT_FOUND)
        oauth = OAuth()
        oauth.register(
            name=PROVIDER_NAME,
            server_metadata_url=_discovery_url(settings.oidc_issuer),  # type: ignore[arg-type]
            client_id=settings.oidc_client_id,
            client_secret=settings.oidc_client_secret or "",
            client_kwargs={"scope": settings.oidc_scopes.replace(",", " ")},
        )
        _oauth = oauth
    return getattr(_oauth, PROVIDER_NAME)


def map_profile_to_user(profile: dict[str, Any]) -> tuple[str, str]:
    """`(email, name)` from an OIDC profile.

    Entra ID does not reliably emit `email`; the ID token may only carry
    `preferred_username` or `upn` — the same fallback chain the old app used.
    """
    email = profile.get("email") or profile.get("preferred_username") or profile.get("upn")
    if not email:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "The identity provider did not return an email address.",
        )
    name = profile.get("name") or profile.get("given_name") or email
    return email, name


def provisioned_role(settings: Settings | None = None) -> Role:
    """The role a *new* OIDC account gets.

    Never the "first account is admin" rule — that is `/auth/sign-up`'s
    bootstrap only. Read through `parse_role`, so an unrecognised
    `OIDC_DEFAULT_ROLE` still lands on viewer, never admin.
    """
    settings = settings or get_settings()
    return parse_role(settings.oidc_default_role)


@router.get("/login")
async def oidc_login(request: Request) -> Any:
    client = _client()
    redirect_uri = str(request.url_for("oidc_callback"))
    return await client.authorize_redirect(request, redirect_uri)


@router.get("/callback", name="oidc_callback")
async def oidc_callback(request: Request, session: DbSession) -> RedirectResponse:
    client = _client()
    token = await client.authorize_access_token(request)
    profile = token.get("userinfo") or await client.userinfo(token=token)
    email, name = map_profile_to_user(profile)
    subject = profile.get("sub")

    user = await user_store.find_user_by_oidc_subject(session, subject) if subject else None
    if user is None:
        user = await user_store.find_user_by_email(session, email)
    if user is None:
        user = await user_store.create_user(
            session,
            email=email,
            name=name,
            oidc_subject=subject,
            role=provisioned_role(),
        )
    elif subject and user.oidc_subject != subject:
        # Links the identity the first time this account signs in via OIDC —
        # e.g. an existing email/password account adopting SSO.
        await user_store.update_user(session, user.id, {"oidc_subject": subject})

    session_token = await session_store.create_session(session, user.id)
    await session.commit()

    response = RedirectResponse(url="/")
    session_store.set_session_cookie(response, request, session_token)
    return response
