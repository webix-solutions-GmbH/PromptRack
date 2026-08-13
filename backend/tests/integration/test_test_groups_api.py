"""`/api/test-groups` end to end: real app, real Postgres, role gating and
workspace isolation.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import users as user_store
from app.auth.passwords import hash_password
from app.auth.policy import Role
from app.main import app
from app.repos.test_cases import create_test_case, create_test_group
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


class TestTestGroupCrud:
    async def test_a_member_creates_lists_and_reads_a_group(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, _ = await create_workspace("Acme")
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        created = await client.post(
            "/api/test-groups", json={"name": "Invoice extraction", "description": "the suite"}
        )
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["name"] == "Invoice extraction"
        assert body["test_case_count"] == 0

        listed = await client.get("/api/test-groups")
        assert [g["name"] for g in listed.json()] == ["Invoice extraction"]

        got = await client.get(f"/api/test-groups/{body['id']}")
        assert got.status_code == 200
        assert got.json()["description"] == "the suite"

    async def test_test_case_count_reflects_contained_cases(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        group = await create_test_group(scope, session, name="Group A")
        await create_test_case(scope, session, group_id=group.id, title="t1", content="hi")
        await create_test_case(scope, session, group_id=group.id, title="t2", content="hi")
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        got = await client.get(f"/api/test-groups/{group.id}")
        assert got.json()["test_case_count"] == 2

    async def test_updating_a_group(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        group = await create_test_group(scope, session, name="Original")
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        updated = await client.put(
            f"/api/test-groups/{group.id}", json={"name": "Renamed", "description": "new"}
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["name"] == "Renamed"
        assert updated.json()["description"] == "new"

    async def test_deleting_a_group_cascades_to_its_test_cases(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        group = await create_test_group(scope, session, name="Group A")
        case = await create_test_case(scope, session, group_id=group.id, title="t1", content="hi")
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        deleted = await client.delete(f"/api/test-groups/{group.id}")
        assert deleted.status_code == 204
        assert (await client.get(f"/api/test-groups/{group.id}")).status_code == 404
        assert (await client.get(f"/api/test-cases/{case.id}")).status_code == 404

    async def test_a_viewer_cannot_create_a_group(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, _ = await create_workspace("Acme")
        await session.commit()
        await make_user(session, "viewer@example.com", "viewer", customer_id)
        await login(client, "viewer@example.com")

        response = await client.post("/api/test-groups", json={"name": "box"})
        assert response.status_code == 403

    async def test_every_signed_in_role_can_list_and_read(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        group = await create_test_group(scope, session, name="Group A")
        await session.commit()
        await make_user(session, "viewer@example.com", "viewer", customer_id)
        await login(client, "viewer@example.com")

        assert (await client.get("/api/test-groups")).status_code == 200
        assert (await client.get(f"/api/test-groups/{group.id}")).status_code == 200

    async def test_a_group_in_another_workspace_is_a_404(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        _, scope_a = await create_workspace("A")
        group_a = await create_test_group(scope_a, session, name="a-group")
        customer_b, _ = await create_workspace("B")
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_b)
        await login(client, "member@example.com")

        assert (await client.get(f"/api/test-groups/{group_a.id}")).status_code == 404
        assert (await client.delete(f"/api/test-groups/{group_a.id}")).status_code == 404
