"""`/api/customers` end to end: real app, real Postgres.

Covers what `tests/integration/test_workspaces.py` deliberately left for this
task — the composed delete-guard refusal message and the "last workspace"
refusal — plus the name-clash guard and role gating (create/rename/archive
are `Writer`, delete alone is `Admin`, matching
`git show legacy-nextjs:src/actions/customers.ts`).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import users as user_store
from app.auth.passwords import hash_password
from app.auth.policy import Role
from app.main import app
from app.models import Customer
from app.repos.endpoints import create_endpoint
from app.scope import Scope

CreateWorkspace = Callable[[str], Awaitable[tuple[int, Scope]]]

PASSWORD = "correct horse battery staple"


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as http:
        yield http


async def make_user(
    session: AsyncSession,
    email: str,
    role: Role,
    active_customer_id: int | None = None,
    password: str = PASSWORD,
) -> int:
    user = await user_store.create_user(
        session, email=email, name=email, password_hash=hash_password(password), role=role
    )
    if active_customer_id is not None:
        await user_store.set_active_customer_id(session, user.id, active_customer_id)
    await session.commit()
    return user.id


async def login(client: AsyncClient, email: str, password: str = PASSWORD) -> None:
    response = await client.post("/api/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text


class TestCustomerCrud:
    async def test_creates_lists_and_renames_a_workspace(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        bootstrap_id, _ = await create_workspace("Bootstrap")
        await session.commit()
        await make_user(session, "member@example.com", "member", bootstrap_id)
        await login(client, "member@example.com")

        created = await client.post(
            "/api/customers", json={"name": "Acme GmbH", "description": "Q3 eval"}
        )
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["name"] == "Acme GmbH"
        assert body["description"] == "Q3 eval"
        assert body["archived"] is False
        assert body["content"]["total"] == 0

        listed = await client.get("/api/customers")
        assert {row["name"] for row in listed.json()} == {"Bootstrap", "Acme GmbH"}

        renamed = await client.put(
            f"/api/customers/{body['id']}", json={"name": "Acme Renamed", "description": None}
        )
        assert renamed.status_code == 200
        assert renamed.json()["name"] == "Acme Renamed"
        assert renamed.json()["description"] is None

    async def test_a_duplicate_name_is_refused_naming_the_clash(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        existing_id, _ = await create_workspace("Acme")
        await session.commit()
        await make_user(session, "member@example.com", "member", existing_id)
        await login(client, "member@example.com")

        response = await client.post("/api/customers", json={"name": "acme"})
        assert response.status_code == 409
        assert str(existing_id) in response.json()["message"]
        assert "Acme" in response.json()["message"]

    async def test_renaming_to_ones_own_current_name_is_not_a_clash(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, _ = await create_workspace("Acme")
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        response = await client.put(
            f"/api/customers/{customer_id}", json={"name": "Acme", "description": "updated"}
        )
        assert response.status_code == 200

    async def test_a_viewer_cannot_create_a_workspace(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, _ = await create_workspace("Acme")
        await session.commit()
        await make_user(session, "viewer@example.com", "viewer", customer_id)
        await login(client, "viewer@example.com")

        response = await client.post("/api/customers", json={"name": "New"})
        assert response.status_code == 403

    async def test_archiving_and_unarchiving_touches_nothing_it_owns(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        first_id, scope = await create_workspace("Acme")
        second_id, _ = await create_workspace("Globex")
        await create_endpoint(scope, session, name="box", base_url="http://x/v1")
        await session.commit()
        await make_user(session, "member@example.com", "member", first_id)
        await login(client, "member@example.com")

        archived = await client.post(f"/api/customers/{second_id}/archive", json={"archived": True})
        assert archived.status_code == 200
        assert archived.json()["archived"] is True

        unarchived = await client.post(
            f"/api/customers/{second_id}/archive", json={"archived": False}
        )
        assert unarchived.json()["archived"] is False

        # Archiving never touched what the *other* workspace holds.
        first_view = next(
            row for row in (await client.get("/api/customers")).json() if row["id"] == first_id
        )
        assert first_view["content"]["endpoints"] == 1

    async def test_deleting_the_only_workspace_is_refused(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        only_id, _ = await create_workspace("Acme")
        await session.commit()
        await make_user(session, "admin@example.com", "admin", only_id)
        await login(client, "admin@example.com")

        response = await client.delete(f"/api/customers/{only_id}")
        assert response.status_code == 409
        assert "only workspace" in response.json()["message"]

    async def test_deleting_a_workspace_that_still_holds_content_names_it(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        first_id, scope = await create_workspace("Acme")
        await create_workspace("Globex")
        await create_endpoint(scope, session, name="box", base_url="http://x/v1")
        await session.commit()
        await make_user(session, "admin@example.com", "admin", first_id)
        await login(client, "admin@example.com")

        response = await client.delete(f"/api/customers/{first_id}")
        assert response.status_code == 409
        message = response.json()["message"]
        assert "1 endpoint" in message
        assert "Acme" in message

    async def test_a_member_cannot_delete_even_an_empty_workspace(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        first_id, _ = await create_workspace("Acme")
        await create_workspace("Globex")
        await session.commit()
        await make_user(session, "member@example.com", "member", first_id)
        await login(client, "member@example.com")

        response = await client.delete(f"/api/customers/{first_id}")
        assert response.status_code == 403

    async def test_deleting_an_empty_workspace_succeeds(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        first_id, _ = await create_workspace("Acme")
        second_id, _ = await create_workspace("Globex")
        await session.commit()
        await make_user(session, "admin@example.com", "admin", first_id)
        await login(client, "admin@example.com")

        response = await client.delete(f"/api/customers/{second_id}")
        assert response.status_code == 204

        remaining = await client.get("/api/customers")
        assert [row["name"] for row in remaining.json()] == ["Acme"]

    async def test_the_base_workspace_cannot_be_deleted_or_archived(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        """Both refusals, and the flag the client reads them from.

        Base owns the endpoints and toolsets every other workspace borrows:
        archiving it would hide the one place they are editable, and deleting it
        would take them with it. Neither is a privilege check — an admin is
        refused too — so this sits with the other two delete guards rather than
        with the role tests.
        """
        acme_id, _ = await create_workspace("Acme")
        base_id, _ = await create_workspace("Base")
        await session.execute(
            update(Customer).where(Customer.id == base_id).values(is_base=True)
        )
        await session.commit()
        await make_user(session, "admin@example.com", "admin", acme_id)
        await login(client, "admin@example.com")

        rows = {row["id"]: row for row in (await client.get("/api/customers")).json()}
        assert rows[base_id]["is_base"] is True
        assert rows[acme_id]["is_base"] is False

        archived = await client.post(f"/api/customers/{base_id}/archive", json={"archived": True})
        assert archived.status_code == 409
        assert "cannot be archived" in archived.json()["message"]

        deleted = await client.delete(f"/api/customers/{base_id}")
        assert deleted.status_code == 409
        assert "cannot be deleted" in deleted.json()["message"]

        # Still there, still live, and un-archiving is not refused — only the
        # transition *into* archived is.
        assert (
            await client.post(f"/api/customers/{base_id}/archive", json={"archived": False})
        ).status_code == 200

    async def test_a_missing_workspace_is_a_404_everywhere(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, _ = await create_workspace("Acme")
        await session.commit()
        await make_user(session, "admin@example.com", "admin", customer_id)
        await login(client, "admin@example.com")

        assert (
            await client.put("/api/customers/9999", json={"name": "x"})
        ).status_code == 404
        assert (
            await client.post("/api/customers/9999/archive", json={"archived": True})
        ).status_code == 404
        assert (await client.delete("/api/customers/9999")).status_code == 404
