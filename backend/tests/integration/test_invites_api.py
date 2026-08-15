"""Invites end to end: an admin mints a link, a stranger redeems it once.

The admin half is `/api/invites` (admin-only); the redemption half is
`/api/auth/invite/{token}` and lives in `app.auth.router`, unauthenticated for
the obvious reason that whoever opens the link has no account yet.

Assertions read the **stored columns** (`user_invites.redeemed_at`,
`users.role`) rather than a status derived from them in a response, per
CLAUDE.md's note on the API-contract seam.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import users as user_store
from app.auth.passwords import hash_password
from app.auth.policy import Role
from app.auth.sessions import SESSION_COOKIE_NAME
from app.main import app
from app.models import User, UserInvite
from app.repos.scoped import utc_now

PASSWORD = "correct horse battery staple"
INVITEE_PASSWORD = "another perfectly fine passphrase"


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as http:
        yield http


@pytest_asyncio.fixture
async def invitee() -> AsyncIterator[AsyncClient]:
    """The person opening the link — a browser with no session of its own."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as http:
        yield http


async def make_user(session: AsyncSession, email: str, role: Role) -> int:
    user = await user_store.create_user(
        session, email=email, name=email, password_hash=hash_password(PASSWORD), role=role
    )
    await session.commit()
    return user.id


async def login(client: AsyncClient, email: str) -> None:
    response = await client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200, response.text


async def signed_in_admin(client: AsyncClient, session: AsyncSession) -> int:
    admin_id = await make_user(session, "admin@example.com", "admin")
    await login(client, "admin@example.com")
    return admin_id


async def mint(client: AsyncClient, role: str = "member", **body: object) -> dict[str, object]:
    response = await client.post("/api/invites", json={"role": role, **body})
    assert response.status_code == 201, response.text
    return response.json()


class TestCreateInvite:
    async def test_returns_the_link_exactly_once_and_stores_only_its_hash(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await signed_in_admin(client, session)

        created = await mint(client, "viewer")
        raw = str(created["token"])
        assert raw
        assert raw.startswith("pri_")
        assert created["role"] == "viewer"
        assert created["status"] == "pending"
        assert created["created_by_name"] == "admin@example.com"

        stored = (await session.scalars(select(UserInvite))).one()
        # A database leak yields nothing redeemable: only the hash is at rest.
        assert stored.token_hash != raw
        assert stored.display_prefix == raw[:12]
        assert created["display_prefix"] == raw[:12]

        # And `GET` never shows it again.
        listed = (await client.get("/api/invites")).json()
        assert listed[0]["id"] == created["id"]
        assert "token" not in listed[0]

    async def test_expiry_defaults_to_a_week_and_is_bounded(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await signed_in_admin(client, session)

        await mint(client)
        stored = (await session.scalars(select(UserInvite))).one()
        assert timedelta(days=6) < stored.expires_at - utc_now() <= timedelta(days=7)

        for days in (0, 91):
            refused = await client.post(
                "/api/invites", json={"role": "member", "expires_in_days": days}
            )
            assert refused.status_code == 422

    async def test_an_unrecognised_role_is_refused_not_coerced(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await signed_in_admin(client, session)
        refused = await client.post("/api/invites", json={"role": "superuser"})
        assert refused.status_code == 400
        assert "not a role" in refused.json()["message"]
        assert (await session.scalars(select(UserInvite))).first() is None

    async def test_pending_invites_are_listed_before_spent_ones(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await signed_in_admin(client, session)
        spent = await mint(client, "viewer")
        pending = await mint(client, "member")
        assert (await client.delete(f"/api/invites/{spent['id']}")).status_code == 204

        listed = (await client.get("/api/invites")).json()
        assert [row["id"] for row in listed] == [pending["id"], spent["id"]]
        assert [row["status"] for row in listed] == ["pending", "revoked"]


class TestRedeemInvite:
    async def test_creates_the_account_at_the_invites_role_and_signs_it_in(
        self, client: AsyncClient, invitee: AsyncClient, session: AsyncSession
    ) -> None:
        admin_id = await signed_in_admin(client, session)
        created = await mint(client, "viewer")
        raw = str(created["token"])

        offered = await invitee.get(f"/api/auth/invite/{raw}")
        assert offered.status_code == 200, offered.text
        assert offered.json()["role"] == "viewer"

        accepted = await invitee.post(
            f"/api/auth/invite/{raw}/accept",
            json={
                "email": "new@example.com",
                "name": "New Person",
                "password": INVITEE_PASSWORD,
            },
        )
        assert accepted.status_code == 201, accepted.text
        assert accepted.json()["user"]["email"] == "new@example.com"
        assert accepted.json()["can_write"] is False
        # Signed in immediately, exactly as sign-up does.
        assert invitee.cookies.get(SESSION_COOKIE_NAME)
        assert (await invitee.get("/api/auth/me")).status_code == 200

        new_id = await session.scalar(select(User.id).where(User.email == "new@example.com"))
        # The invite decided the role, not `default_role()` — which is
        # `member`, so this would read `member` if `create_user`'s own rule had
        # been left to run.
        assert user_store.default_role() == "member"
        assert await session.scalar(select(User.role).where(User.id == new_id)) == "viewer"

        session.expire_all()
        stored = (await session.scalars(select(UserInvite))).one()
        assert stored.redeemed_at is not None
        assert stored.redeemed_by == new_id
        assert stored.created_by == admin_id

    async def test_a_link_can_only_be_redeemed_once(
        self, client: AsyncClient, invitee: AsyncClient, session: AsyncSession
    ) -> None:
        await signed_in_admin(client, session)
        raw = str((await mint(client))["token"])
        first = await invitee.post(
            f"/api/auth/invite/{raw}/accept",
            json={"email": "first@example.com", "password": INVITEE_PASSWORD},
        )
        assert first.status_code == 201, first.text

        second = await invitee.post(
            f"/api/auth/invite/{raw}/accept",
            json={"email": "second@example.com", "password": INVITEE_PASSWORD},
        )
        assert second.status_code == 410
        assert (await invitee.get(f"/api/auth/invite/{raw}")).status_code == 410
        # And the second person got no account out of it.
        assert (
            await session.scalar(select(User.id).where(User.email == "second@example.com"))
        ) is None

    async def test_two_people_opening_the_same_link_at_once_get_one_account(
        self, client: AsyncClient, invitee: AsyncClient, session: AsyncSession
    ) -> None:
        """The `SELECT … FOR UPDATE` inside the redemption transaction.

        Without the row lock both requests would read a pending invite and both
        would create an account. With it the second waits for the first to
        commit, re-reads a row that is already redeemed, and is refused.
        """
        await signed_in_admin(client, session)
        raw = str((await mint(client))["token"])

        async def accept(email: str) -> int:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://testserver") as http:
                response = await http.post(
                    f"/api/auth/invite/{raw}/accept",
                    json={"email": email, "password": INVITEE_PASSWORD},
                )
                return response.status_code

        statuses = await asyncio.gather(accept("a@example.com"), accept("b@example.com"))
        assert sorted(statuses) == [201, 410]
        assert await session.scalar(select(func.count()).select_from(User)) == 2

    async def test_an_expired_link_is_gone(
        self, client: AsyncClient, invitee: AsyncClient, session: AsyncSession
    ) -> None:
        await signed_in_admin(client, session)
        raw = str((await mint(client))["token"])
        await session.execute(
            update(UserInvite).values(expires_at=utc_now() - timedelta(seconds=1))
        )
        await session.commit()

        assert (await invitee.get(f"/api/auth/invite/{raw}")).status_code == 410
        accepted = await invitee.post(
            f"/api/auth/invite/{raw}/accept",
            json={"email": "late@example.com", "password": INVITEE_PASSWORD},
        )
        assert accepted.status_code == 410
        assert "no longer usable" in accepted.json()["message"]

    async def test_a_revoked_link_is_gone(
        self, client: AsyncClient, invitee: AsyncClient, session: AsyncSession
    ) -> None:
        await signed_in_admin(client, session)
        created = await mint(client)
        raw = str(created["token"])
        assert (await client.delete(f"/api/invites/{created['id']}")).status_code == 204

        assert (await invitee.get(f"/api/auth/invite/{raw}")).status_code == 410
        assert (
            await invitee.post(
                f"/api/auth/invite/{raw}/accept",
                json={"email": "late@example.com", "password": INVITEE_PASSWORD},
            )
        ).status_code == 410

    async def test_a_token_that_matches_no_row_is_a_404(self, invitee: AsyncClient) -> None:
        # "This link is not real" and "this link is no longer usable" are
        # different things for the person holding it to read.
        assert (await invitee.get("/api/auth/invite/pri_nonsense")).status_code == 404
        assert (
            await invitee.post(
                "/api/auth/invite/pri_nonsense/accept",
                json={"email": "nobody@example.com", "password": INVITEE_PASSWORD},
            )
        ).status_code == 404

    async def test_an_address_that_already_has_an_account_is_a_409(
        self, client: AsyncClient, invitee: AsyncClient, session: AsyncSession
    ) -> None:
        await signed_in_admin(client, session)
        raw = str((await mint(client))["token"])

        refused = await invitee.post(
            f"/api/auth/invite/{raw}/accept",
            json={"email": "admin@example.com", "password": INVITEE_PASSWORD},
        )
        assert refused.status_code == 409
        assert "already exists" in refused.json()["message"]
        # The link is not spent by a refused redemption.
        session.expire_all()
        assert (await session.scalars(select(UserInvite))).one().redeemed_at is None
        assert (await invitee.get(f"/api/auth/invite/{raw}")).status_code == 200

    async def test_a_short_password_is_refused(
        self, client: AsyncClient, invitee: AsyncClient, session: AsyncSession
    ) -> None:
        await signed_in_admin(client, session)
        raw = str((await mint(client))["token"])
        refused = await invitee.post(
            f"/api/auth/invite/{raw}/accept",
            json={"email": "new@example.com", "password": "short"},
        )
        assert refused.status_code == 422
        assert "password" in refused.json()["message"]


class TestRevokeInvite:
    async def test_a_redeemed_invite_cannot_be_revoked(
        self, client: AsyncClient, invitee: AsyncClient, session: AsyncSession
    ) -> None:
        # The row is the record of an account that exists; rewriting it would
        # make the list lie about what happened.
        await signed_in_admin(client, session)
        created = await mint(client)
        raw = str(created["token"])
        await invitee.post(
            f"/api/auth/invite/{raw}/accept",
            json={"email": "new@example.com", "password": INVITEE_PASSWORD},
        )

        refused = await client.delete(f"/api/invites/{created['id']}")
        assert refused.status_code == 409
        assert "already been used" in refused.json()["message"]
        session.expire_all()
        assert (await session.scalars(select(UserInvite))).one().revoked_at is None

    async def test_an_unknown_invite_is_a_404(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await signed_in_admin(client, session)
        assert (await client.delete("/api/invites/9999")).status_code == 404


class TestGuards:
    @pytest.mark.parametrize("role", ["member", "viewer"])
    async def test_only_an_admin_may_list_or_mint_invites(
        self, client: AsyncClient, session: AsyncSession, role: Role
    ) -> None:
        await make_user(session, f"{role}@example.com", role)
        await login(client, f"{role}@example.com")

        assert (await client.get("/api/invites")).status_code == 403
        assert (await client.post("/api/invites", json={"role": "admin"})).status_code == 403
        assert (await client.delete("/api/invites/1")).status_code == 403
        assert (await session.scalars(select(UserInvite))).first() is None

    async def test_signed_out_is_a_401(self, client: AsyncClient) -> None:
        assert (await client.get("/api/invites")).status_code == 401
