"""The auth surface end to end: real app, real cookies, real Postgres.

This is where the halves that cannot be tested apart meet — a cookie set by
`/login` signing a later request in, a role gating a dependency, a stale
workspace pointer healing itself. The pure halves (role semantics, argon2, token
hashing) are `tests/test_policy.py` and `tests/test_passwords.py`.

The requests go through the real `app.main.app`, so the exception handlers that
give every error a `message` are exercised too; three probe routes are added to
it below because the guards are dependencies and a dependency needs an endpoint
to guard.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import timedelta

import pytest
import pytest_asyncio
from fastapi import APIRouter, FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import users as user_store
from app.auth.guards import Admin, CurrentScope, CurrentUser, Writer
from app.auth.passwords import hash_password
from app.auth.policy import Role
from app.auth.sessions import SESSION_COOKIE_NAME, hash_token
from app.main import app
from app.models import Session as SessionRow
from app.repos.customers import set_customer_archived
from app.repos.scoped import utc_now
from app.scope import Scope

CreateWorkspace = Callable[[str], Awaitable[tuple[int, Scope]]]

PASSWORD = "correct horse battery staple"


# ---------------------------------------------------------------------------
# Probe routes — the guards are dependencies, so they need something to guard.
# Added to the app itself (rather than to a second one built here) so the
# refusals travel through the same exception handlers the real API uses.
# ---------------------------------------------------------------------------

_probe = APIRouter(prefix="/probe")


@_probe.get("/signed-in")
async def _signed_in(actor: CurrentUser) -> dict[str, object]:
    return {"role": actor.role, "via": actor.via}


@_probe.get("/write")
async def _write(actor: Writer) -> dict[str, object]:
    return {"role": actor.role}


@_probe.get("/admin")
async def _admin(actor: Admin) -> dict[str, object]:
    return {"role": actor.role}


@_probe.get("/scope")
async def _scope(scope: CurrentScope) -> dict[str, object]:
    return {"customer_id": scope.customer_id, "origin": scope.origin}


def _mount_probes(application: FastAPI) -> None:
    """Runs once — pytest imports a test module a single time per session."""
    application.include_router(_probe, prefix="/api")


_mount_probes(app)


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as http:
        yield http


async def make_user(
    session: AsyncSession, email: str, role: Role, password: str = PASSWORD
) -> int:
    """An account created the way an admin would create one — role named
    explicitly, so this never depends on the first-account rule.
    """
    user = await user_store.create_user(
        session,
        email=email,
        name=email,
        password_hash=hash_password(password),
        role=role,
    )
    await session.commit()
    return user.id


async def login(client: AsyncClient, email: str, password: str = PASSWORD) -> None:
    response = await client.post("/api/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text


class TestSignUp:
    async def test_the_first_account_is_the_administrator(self, client: AsyncClient) -> None:
        assert (await client.get("/api/auth/status")).json() == {"signup_open": True}

        response = await client.post(
            "/api/auth/sign-up",
            json={"email": "first@example.com", "password": PASSWORD, "name": "First"},
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["user"]["role"] == "admin"
        assert body["can_write"] is True
        assert body["can_administer"] is True
        # Signed in immediately — the bootstrap must not need a second step.
        assert client.cookies.get(SESSION_COOKIE_NAME)

    async def test_sign_up_closes_after_the_first_account(self, client: AsyncClient) -> None:
        await client.post(
            "/api/auth/sign-up", json={"email": "first@example.com", "password": PASSWORD}
        )
        assert (await client.get("/api/auth/status")).json() == {"signup_open": False}

        second = await client.post(
            "/api/auth/sign-up", json={"email": "second@example.com", "password": PASSWORD}
        )
        assert second.status_code == 403
        assert "closed" in second.json()["message"]

    async def test_a_short_password_is_refused_with_a_message(
        self, client: AsyncClient
    ) -> None:
        response = await client.post(
            "/api/auth/sign-up", json={"email": "first@example.com", "password": "short"}
        )
        assert response.status_code == 422
        assert "password" in response.json()["message"]

    async def test_the_name_falls_back_to_the_address(self, client: AsyncClient) -> None:
        response = await client.post(
            "/api/auth/sign-up", json={"email": "first@example.com", "password": PASSWORD}
        )
        assert response.json()["user"]["name"] == "first@example.com"


class TestLogin:
    async def test_signs_in_and_out(self, client: AsyncClient, session: AsyncSession) -> None:
        await make_user(session, "member@example.com", "member")

        assert (await client.get("/api/auth/me")).status_code == 401
        await login(client, "member@example.com")
        assert (await client.get("/api/auth/me")).json()["user"]["role"] == "member"

        assert (await client.post("/api/auth/logout")).status_code == 204
        assert (await client.get("/api/auth/me")).status_code == 401

    async def test_matches_the_address_case_insensitively(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        # The unique index is on `lower(email)`, so the lookup has to agree
        # with it or an account could become unreachable by its own address.
        await make_user(session, "Member@Example.com", "member")
        await login(client, "member@example.com")

    async def test_a_wrong_password_and_an_unknown_account_read_alike(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        # Distinguishing them would turn the login form into an
        # account-enumeration oracle.
        await make_user(session, "member@example.com", "member")
        wrong = await client.post(
            "/api/auth/login", json={"email": "member@example.com", "password": "nope-nope-nope"}
        )
        unknown = await client.post(
            "/api/auth/login", json={"email": "nobody@example.com", "password": PASSWORD}
        )
        assert wrong.status_code == unknown.status_code == 401
        assert wrong.json()["message"] == unknown.json()["message"]

    async def test_logging_out_twice_is_not_an_error(self, client: AsyncClient) -> None:
        # A stale tab must be able to clear itself.
        assert (await client.post("/api/auth/logout")).status_code == 204

    async def test_the_cookie_is_httponly_and_lax(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await make_user(session, "member@example.com", "member")
        response = await client.post(
            "/api/auth/login", json={"email": "member@example.com", "password": PASSWORD}
        )
        cookie = response.headers["set-cookie"].lower()
        assert "httponly" in cookie
        # Lax is the CSRF defence: no cookie on a cross-site POST.
        assert "samesite=lax" in cookie
        # Plain http in the test client, so no Secure flag — it is derived from
        # the request scheme rather than configured.
        assert "secure" not in cookie

    async def test_the_raw_token_is_never_stored(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await make_user(session, "member@example.com", "member")
        await login(client, "member@example.com")
        raw = client.cookies.get(SESSION_COOKIE_NAME)
        assert raw is not None
        stored = (await session.scalars(select(SessionRow))).one()
        # A database leak yields nothing replayable: only the hash is at rest.
        assert stored.token_hash == hash_token(raw)
        assert stored.token_hash != raw

    async def test_an_expired_session_is_refused(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await make_user(session, "member@example.com", "member")
        await login(client, "member@example.com")
        await session.execute(
            update(SessionRow).values(expires_at=utc_now() - timedelta(seconds=1))
        )
        await session.commit()
        assert (await client.get("/api/auth/me")).status_code == 401

    async def test_a_forged_cookie_is_refused(self, client: AsyncClient) -> None:
        client.cookies.set(SESSION_COOKIE_NAME, "not-a-real-token")
        assert (await client.get("/api/auth/me")).status_code == 401


class TestGuards:
    @pytest.mark.parametrize(
        ("role", "write_status", "admin_status"),
        [("admin", 200, 200), ("member", 200, 403), ("viewer", 403, 403)],
    )
    async def test_each_role_reaches_exactly_what_it_may(
        self,
        client: AsyncClient,
        session: AsyncSession,
        role: Role,
        write_status: int,
        admin_status: int,
    ) -> None:
        await make_user(session, f"{role}@example.com", role)
        await login(client, f"{role}@example.com")

        assert (await client.get("/api/probe/signed-in")).status_code == 200
        assert (await client.get("/api/probe/write")).status_code == write_status
        assert (await client.get("/api/probe/admin")).status_code == admin_status

    async def test_an_unrecognised_stored_role_falls_back_to_viewer(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        # The column is plain text, so a value written by another build (or by
        # hand) must fail closed rather than open.
        user_id = await make_user(session, "odd@example.com", "member")
        await user_store.update_user(session, user_id, {"role": "superuser"})
        await session.commit()

        await login(client, "odd@example.com")
        assert (await client.get("/api/auth/me")).json()["user"]["role"] == "viewer"
        assert (await client.get("/api/probe/write")).status_code == 403

    async def test_refusals_carry_a_message(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        assert (await client.get("/api/probe/write")).json()["message"] == "Sign in to continue."
        await make_user(session, "viewer@example.com", "viewer")
        await login(client, "viewer@example.com")
        assert (await client.get("/api/probe/write")).json()["message"] == (
            "Your account is read-only."
        )


class TestWorkspace:
    async def test_no_workspace_yet_is_a_409_rather_than_an_empty_app(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        # The state between the first sign-up and the first customer.
        await make_user(session, "member@example.com", "member")
        await login(client, "member@example.com")
        assert (await client.get("/api/auth/me")).json()["active_customer"] is None
        assert (await client.get("/api/probe/scope")).status_code == 409

    async def test_lands_in_the_oldest_workspace_and_says_so(
        self,
        client: AsyncClient,
        session: AsyncSession,
        create_workspace: CreateWorkspace,
    ) -> None:
        first_id, _ = await create_workspace("Acme")
        await create_workspace("Globex")
        await session.commit()
        await make_user(session, "member@example.com", "member")
        await login(client, "member@example.com")

        body = (await client.get("/api/auth/me")).json()
        assert body["active_customer"]["id"] == first_id
        assert [customer["name"] for customer in body["customers"]] == ["Acme", "Globex"]
        # And the resolution is what a scoped query would run under.
        assert (await client.get("/api/probe/scope")).json() == {
            "customer_id": first_id,
            "origin": "session",
        }

    async def test_switching_moves_the_user_and_survives_a_new_request(
        self,
        client: AsyncClient,
        session: AsyncSession,
        create_workspace: CreateWorkspace,
    ) -> None:
        await create_workspace("Acme")
        second_id, _ = await create_workspace("Globex")
        await session.commit()
        user_id = await make_user(session, "member@example.com", "member")
        await login(client, "member@example.com")

        switched = await client.post("/api/auth/switch-customer", json={"customer_id": second_id})
        assert switched.status_code == 200
        assert switched.json()["active_customer"]["id"] == second_id
        # On the user row, not in a cookie: unforgeable and it survives.
        assert (await client.get("/api/probe/scope")).json()["customer_id"] == second_id
        session.expire_all()
        assert await user_store.get_active_customer_id(session, user_id) == second_id

    async def test_switching_into_a_workspace_that_is_not_there(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        await create_workspace("Acme")
        await session.commit()
        await make_user(session, "member@example.com", "member")
        await login(client, "member@example.com")

        missing = await client.post("/api/auth/switch-customer", json={"customer_id": 9999})
        assert missing.status_code == 404

    async def test_switching_into_an_archived_workspace_is_refused(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        await create_workspace("Acme")
        archived_id, _ = await create_workspace("Old engagement")
        await set_customer_archived(session, archived_id, utc_now())
        await session.commit()
        await make_user(session, "member@example.com", "member")
        await login(client, "member@example.com")

        response = await client.post(
            "/api/auth/switch-customer", json={"customer_id": archived_id}
        )
        assert response.status_code == 409
        assert "archived" in response.json()["message"]

    async def test_a_stale_pointer_heals_instead_of_emptying_the_app(
        self,
        client: AsyncClient,
        session: AsyncSession,
        create_workspace: CreateWorkspace,
    ) -> None:
        live_id, _ = await create_workspace("Acme")
        archived_id, _ = await create_workspace("Old engagement")
        user_id = await make_user(session, "member@example.com", "member")
        await user_store.set_active_customer_id(session, user_id, archived_id)
        await set_customer_archived(session, archived_id, utc_now())
        await session.commit()

        await login(client, "member@example.com")
        assert (await client.get("/api/auth/me")).json()["active_customer"]["id"] == live_id
        # Healed, not merely masked: the row now says what the user landed in.
        session.expire_all()
        assert await user_store.get_active_customer_id(session, user_id) == live_id
