"""`/api/users` end to end: real app, real cookies, real Postgres.

The refusals are the point of this suite. Two of them protect the install from
its administrators — you cannot act on your own account, and you cannot leave
the install without an admin who can sign in — and both are checked *before*
any write, so a refused request changes nothing.

Assertions read the **stored column** (`users.role`, `users.disabled_at`, the
`sessions` rows) rather than a flag derived from it in the response, per
CLAUDE.md's note on the API-contract seam.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import users as user_store
from app.auth.passwords import hash_password
from app.auth.policy import Role
from app.main import app
from app.models import Session as SessionRow
from app.models import User, UserInvite
from app.repos.scoped import utc_now

PASSWORD = "correct horse battery staple"


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as http:
        yield http


@pytest_asyncio.fixture
async def other_client() -> AsyncIterator[AsyncClient]:
    """A second browser, for the account being acted upon."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as http:
        yield http


async def make_user(
    session: AsyncSession,
    email: str,
    role: Role,
    *,
    password: str | None = PASSWORD,
    oidc_subject: str | None = None,
    disabled: bool = False,
) -> int:
    user = await user_store.create_user(
        session,
        email=email,
        name=email,
        password_hash=hash_password(password) if password else None,
        oidc_subject=oidc_subject,
        role=role,
    )
    if disabled:
        await user_store.set_disabled(session, user.id, utc_now())
    await session.commit()
    return user.id


async def login(client: AsyncClient, email: str, password: str = PASSWORD) -> None:
    response = await client.post("/api/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text


async def stored_role(session: AsyncSession, user_id: int) -> str | None:
    return await session.scalar(select(User.role).where(User.id == user_id))


async def stored_disabled_at(session: AsyncSession, user_id: int) -> object:
    return await session.scalar(select(User.disabled_at).where(User.id == user_id))


class TestListUsers:
    async def test_lists_every_account_and_how_it_signs_in(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await make_user(session, "admin@example.com", "admin")
        await make_user(session, "sso@example.com", "member", password=None, oidc_subject="sub-1")
        await login(client, "admin@example.com")

        rows = (await client.get("/api/users")).json()
        assert [row["email"] for row in rows] == ["admin@example.com", "sso@example.com"]
        assert rows[0]["has_password"] is True
        assert rows[0]["has_oidc"] is False
        assert rows[1]["has_password"] is False
        assert rows[1]["has_oidc"] is True
        # Booleans only: a subject claim is not something a list needs to carry.
        assert "oidc_subject" not in rows[1]
        assert "password_hash" not in rows[1]

    async def test_an_unrecognised_stored_role_is_shown_as_viewer(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        # The page has to show the role the guards will actually enforce.
        await make_user(session, "admin@example.com", "admin")
        odd_id = await make_user(session, "odd@example.com", "member")
        await user_store.update_user(session, odd_id, {"role": "superuser"})
        await session.commit()
        await login(client, "admin@example.com")

        rows = (await client.get("/api/users")).json()
        assert next(row for row in rows if row["id"] == odd_id)["role"] == "viewer"


class TestSetRole:
    async def test_changes_the_stored_role(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await make_user(session, "admin@example.com", "admin")
        target_id = await make_user(session, "viewer@example.com", "viewer")
        await login(client, "admin@example.com")

        response = await client.put(f"/api/users/{target_id}/role", json={"role": "member"})
        assert response.status_code == 200, response.text
        assert response.json()["role"] == "member"
        assert await stored_role(session, target_id) == "member"

    async def test_an_unrecognised_role_is_refused_not_coerced(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        # The opposite of `parse_role`: the caller is *choosing* the value here,
        # so seating them at viewer would be a lie about what happened.
        await make_user(session, "admin@example.com", "admin")
        target_id = await make_user(session, "member@example.com", "member")
        await login(client, "admin@example.com")

        response = await client.put(f"/api/users/{target_id}/role", json={"role": "superuser"})
        assert response.status_code == 400
        assert "not a role" in response.json()["message"]
        assert await stored_role(session, target_id) == "member"

    async def test_an_unknown_user_is_a_404(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await make_user(session, "admin@example.com", "admin")
        await login(client, "admin@example.com")
        assert (await client.put("/api/users/9999/role", json={"role": "member"})).status_code == (
            404
        )


class TestDeactivation:
    async def test_deactivating_stamps_the_column_and_signs_them_out_everywhere(
        self,
        client: AsyncClient,
        other_client: AsyncClient,
        session: AsyncSession,
    ) -> None:
        await make_user(session, "admin@example.com", "admin")
        target_id = await make_user(session, "member@example.com", "member")
        await login(client, "admin@example.com")
        await login(other_client, "member@example.com")
        assert (await other_client.get("/api/auth/me")).status_code == 200

        response = await client.post(f"/api/users/{target_id}/deactivate")
        assert response.status_code == 200, response.text
        assert response.json()["disabled_at"] is not None
        assert await stored_disabled_at(session, target_id) is not None
        # Deleted, not merely ignored: an open tab is signed out on its very
        # next request rather than at the end of its 30-day window.
        remaining = await session.scalar(
            select(func.count()).select_from(SessionRow).where(SessionRow.user_id == target_id)
        )
        assert remaining == 0
        assert (await other_client.get("/api/auth/me")).status_code == 401

    async def test_reactivating_clears_the_column(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await make_user(session, "admin@example.com", "admin")
        target_id = await make_user(session, "member@example.com", "member", disabled=True)
        await login(client, "admin@example.com")

        response = await client.post(f"/api/users/{target_id}/reactivate")
        assert response.status_code == 200, response.text
        assert response.json()["disabled_at"] is None
        assert await stored_disabled_at(session, target_id) is None
        # Sessions are not restored — the account signs in again.
        await login(client, "admin@example.com")


class TestDeleteUser:
    async def test_deletes_the_row(self, client: AsyncClient, session: AsyncSession) -> None:
        await make_user(session, "admin@example.com", "admin")
        target_id = await make_user(session, "member@example.com", "member")
        await login(client, "admin@example.com")

        assert (await client.delete(f"/api/users/{target_id}")).status_code == 204
        assert await session.scalar(select(User.id).where(User.id == target_id)) is None

    async def test_an_unknown_user_is_a_404(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await make_user(session, "admin@example.com", "admin")
        await login(client, "admin@example.com")
        assert (await client.delete("/api/users/9999")).status_code == 404

    async def test_what_the_account_authored_outlives_it(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        # `SET NULL`, not `CASCADE`: deleting the admin who sent an invite must
        # not delete the record of the invite. Its sessions and tokens do go
        # (`CASCADE`), which is the opposite choice for the opposite reason.
        author_id = await make_user(session, "author@example.com", "admin")
        await make_user(session, "admin@example.com", "admin")
        await login(client, "author@example.com")
        assert (await client.post("/api/invites", json={"role": "member"})).status_code == 201

        await login(client, "admin@example.com")
        assert (await client.delete(f"/api/users/{author_id}")).status_code == 204

        session.expire_all()
        invite = (await session.scalars(select(UserInvite))).one()
        assert invite.created_by is None


class TestSelfTarget:
    """An admin acting on their own account is refused, whatever the action.

    Not a safety belt against a typo so much as against an unrecoverable one:
    the only surface that could undo a self-demotion is the one the demotion
    just closed.
    """

    @pytest_asyncio.fixture
    async def own_id(self, client: AsyncClient, session: AsyncSession) -> int:
        user_id = await make_user(session, "admin@example.com", "admin")
        await make_user(session, "second-admin@example.com", "admin")
        await login(client, "admin@example.com")
        return user_id

    async def test_role_change(
        self, client: AsyncClient, session: AsyncSession, own_id: int
    ) -> None:
        response = await client.put(f"/api/users/{own_id}/role", json={"role": "viewer"})
        assert response.status_code == 409
        assert "your own account" in response.json()["message"]
        assert await stored_role(session, own_id) == "admin"

    async def test_deactivate(
        self, client: AsyncClient, session: AsyncSession, own_id: int
    ) -> None:
        assert (await client.post(f"/api/users/{own_id}/deactivate")).status_code == 409
        assert await stored_disabled_at(session, own_id) is None

    async def test_delete(self, client: AsyncClient, session: AsyncSession, own_id: int) -> None:
        assert (await client.delete(f"/api/users/{own_id}")).status_code == 409
        assert await session.scalar(select(User.id).where(User.id == own_id)) is not None


class TestLastAdmin:
    """An install always keeps an administrator who can sign in — and the
    refusal that guarantees that is the **self-target** one, not this.

    An admin may only ever demote, deactivate or delete *someone else*
    (`TestSelfTarget`), so whatever they do, they are themselves still an
    enabled admin when the request finishes. `would_remove_last_admin` is the
    explicit backstop behind that invariant rather than the thing enforcing it,
    and it is unreachable for an *enabled* target by design: the caller is an
    enabled admin other than the target, so an enabled admin target already
    means two of them.

    What is reachable is a **deactivated** admin as the target — and that must
    not be refused. Such an account cannot sign in, `count_admins` already
    leaves it out, and removing it removes no administrator at all.
    """

    @pytest_asyncio.fixture
    async def disabled_admin_id(self, client: AsyncClient, session: AsyncSession) -> int:
        await make_user(session, "admin@example.com", "admin")
        target_id = await make_user(session, "retired@example.com", "admin", disabled=True)
        await login(client, "admin@example.com")
        return target_id

    async def test_a_deactivated_admin_can_be_demoted(
        self, client: AsyncClient, session: AsyncSession, disabled_admin_id: int
    ) -> None:
        response = await client.put(
            f"/api/users/{disabled_admin_id}/role", json={"role": "viewer"}
        )
        assert response.status_code == 200, response.text
        assert await stored_role(session, disabled_admin_id) == "viewer"

    async def test_a_deactivated_admin_can_be_deleted(
        self, client: AsyncClient, session: AsyncSession, disabled_admin_id: int
    ) -> None:
        assert (await client.delete(f"/api/users/{disabled_admin_id}")).status_code == 204
        assert await session.scalar(select(User.id).where(User.id == disabled_admin_id)) is None
        # The caller is still standing, which is the whole guarantee.
        assert await user_store.count_admins(session) == 1

    async def test_acting_on_another_admin_always_leaves_the_caller_standing(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await make_user(session, "admin@example.com", "admin")
        demoted_id = await make_user(session, "co-admin@example.com", "admin")
        deleted_id = await make_user(session, "third-admin@example.com", "admin")
        await login(client, "admin@example.com")

        response = await client.put(f"/api/users/{demoted_id}/role", json={"role": "member"})
        assert response.status_code == 200, response.text
        assert (await client.post(f"/api/users/{deleted_id}/deactivate")).status_code == 200
        assert (await client.delete(f"/api/users/{deleted_id}")).status_code == 204

        assert await stored_role(session, demoted_id) == "member"
        assert await user_store.count_admins(session) == 1


class TestGuards:
    @pytest.mark.parametrize("role", ["member", "viewer"])
    async def test_only_an_admin_reaches_any_of_it(
        self, client: AsyncClient, session: AsyncSession, role: Role
    ) -> None:
        target_id = await make_user(session, "admin@example.com", "admin")
        await make_user(session, f"{role}@example.com", role)
        await login(client, f"{role}@example.com")

        assert (await client.get("/api/users")).status_code == 403
        assert (
            await client.put(f"/api/users/{target_id}/role", json={"role": "viewer"})
        ).status_code == 403
        assert (await client.post(f"/api/users/{target_id}/deactivate")).status_code == 403
        assert (await client.post(f"/api/users/{target_id}/reactivate")).status_code == 403
        assert (await client.delete(f"/api/users/{target_id}")).status_code == 403
        # And nothing was written on the way to any of those refusals.
        assert await stored_role(session, target_id) == "admin"
        assert await stored_disabled_at(session, target_id) is None

    async def test_signed_out_is_a_401(self, client: AsyncClient) -> None:
        assert (await client.get("/api/users")).status_code == 401
