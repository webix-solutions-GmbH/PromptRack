"""`/api/param-groups` end to end: real app, real Postgres, role gating and
workspace isolation. The merge behavior itself is pure
(`tests/test_params.py`); what a run freezes is `test_run_create.py`'s.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable, Callable

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import users as user_store
from app.auth.passwords import hash_password
from app.auth.policy import Role
from app.main import app
from app.repos.param_groups import create_param_group
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


class TestParamGroupCrud:
    async def test_a_member_creates_lists_and_reads_a_group(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, _ = await create_workspace("Acme")
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        created = await client.post(
            "/api/param-groups",
            json={
                "name": "no thinking",
                "description": "vLLM Qwen3",
                "params": {"chat_template_kwargs": {"enable_thinking": False}},
            },
        )
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["name"] == "no thinking"
        assert body["params"] == {"chat_template_kwargs": {"enable_thinking": False}}

        listed = await client.get("/api/param-groups")
        assert [g["name"] for g in listed.json()] == ["no thinking"]

        got = await client.get(f"/api/param-groups/{body['id']}")
        assert got.status_code == 200
        assert got.json()["description"] == "vLLM Qwen3"

    async def test_a_null_value_is_stored_it_is_the_unset_signal(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, _ = await create_workspace("Acme")
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        created = await client.post(
            "/api/param-groups",
            json={"name": "no effort", "params": {"reasoning_effort": None}},
        )
        assert created.status_code == 201, created.text
        assert created.json()["params"] == {"reasoning_effort": None}

    async def test_a_reserved_key_is_refused(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, _ = await create_workspace("Acme")
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        refused = await client.post(
            "/api/param-groups", json={"name": "bad", "params": {"messages": []}}
        )
        assert refused.status_code == 422
        assert "messages" in refused.text

    async def test_updating_a_group(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        group = await create_param_group(
            scope, session, name="Original", params=json.dumps({"temperature": 0})
        )
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        updated = await client.put(
            f"/api/param-groups/{group.id}",
            json={"name": "Renamed", "description": "new", "params": {"temperature": 0.2}},
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["name"] == "Renamed"
        assert updated.json()["params"] == {"temperature": 0.2}

    async def test_deleting_a_group(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        group = await create_param_group(
            scope, session, name="gone soon", params=json.dumps({"seed": 7})
        )
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        deleted = await client.delete(f"/api/param-groups/{group.id}")
        assert deleted.status_code == 204
        assert (await client.get(f"/api/param-groups/{group.id}")).status_code == 404

    async def test_a_viewer_cannot_write_but_can_read(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        group = await create_param_group(
            scope, session, name="temp 0", params=json.dumps({"temperature": 0})
        )
        await session.commit()
        await make_user(session, "viewer@example.com", "viewer", customer_id)
        await login(client, "viewer@example.com")

        assert (await client.get("/api/param-groups")).status_code == 200
        assert (await client.get(f"/api/param-groups/{group.id}")).status_code == 200
        refused = await client.post(
            "/api/param-groups", json={"name": "x", "params": {"seed": 1}}
        )
        assert refused.status_code == 403
        assert (await client.delete(f"/api/param-groups/{group.id}")).status_code == 403

    async def test_a_group_in_another_workspace_is_a_404(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        _, scope_a = await create_workspace("A")
        group_a = await create_param_group(
            scope_a, session, name="a-group", params=json.dumps({"seed": 1})
        )
        customer_b, _ = await create_workspace("B")
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_b)
        await login(client, "member@example.com")

        assert (await client.get(f"/api/param-groups/{group_a.id}")).status_code == 404
        assert (await client.delete(f"/api/param-groups/{group_a.id}")).status_code == 404
