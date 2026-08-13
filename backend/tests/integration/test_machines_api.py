"""`/api/machines` end to end: real app, real Postgres, role gating.

`discover`/`test` stub out `app.services.discovery.probe_models` — its own
parsing/error-mapping is `tests/test_discovery.py`'s job, which needs no
database at all. What this file covers is what only the wired-up route can
show: the sync actually lands in `machine_models`, a foreign machine is a 404
rather than someone else's row, and each endpoint sits behind the role the
plan names.
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
from app.repos.machines import create_machine
from app.scope import Scope
from app.services import discovery

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


class TestMachineCrud:
    async def test_admin_creates_lists_and_reads_a_machine(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, _ = await create_workspace("Acme")
        await session.commit()
        await make_user(session, "admin@example.com", "admin", customer_id)
        await login(client, "admin@example.com")

        created = await client.post(
            "/api/machines",
            json={"name": "vLLM box", "base_url": "http://10.0.0.5:8000/v1", "api_key": "s3cret"},
        )
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["has_api_key"] is True
        assert "api_key" not in body

        listed = await client.get("/api/machines")
        assert [m["name"] for m in listed.json()] == ["vLLM box"]

        got = await client.get(f"/api/machines/{body['id']}")
        assert got.status_code == 200
        assert got.json()["base_url"] == "http://10.0.0.5:8000/v1"

    async def test_a_trailing_slash_is_stripped_from_the_base_url(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, _ = await create_workspace("Acme")
        await session.commit()
        await make_user(session, "admin@example.com", "admin", customer_id)
        await login(client, "admin@example.com")

        created = await client.post(
            "/api/machines", json={"name": "box", "base_url": "http://x:8000/v1/"}
        )
        assert created.json()["base_url"] == "http://x:8000/v1"

    async def test_base_url_must_carry_a_scheme(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, _ = await create_workspace("Acme")
        await session.commit()
        await make_user(session, "admin@example.com", "admin", customer_id)
        await login(client, "admin@example.com")

        response = await client.post("/api/machines", json={"name": "box", "base_url": "x:8000/v1"})
        assert response.status_code == 422

    async def test_a_member_cannot_create_a_machine(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, _ = await create_workspace("Acme")
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        response = await client.post("/api/machines", json={"name": "box", "base_url": "http://x/v1"})
        assert response.status_code == 403

    async def test_every_signed_in_role_can_list_and_read(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        machine = await create_machine(scope, session, name="box", base_url="http://x/v1")
        await session.commit()
        await make_user(session, "viewer@example.com", "viewer", customer_id)
        await login(client, "viewer@example.com")

        assert (await client.get("/api/machines")).status_code == 200
        assert (await client.get(f"/api/machines/{machine.id}")).status_code == 200

    async def test_a_viewer_cannot_write(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        machine = await create_machine(scope, session, name="box", base_url="http://x/v1")
        await session.commit()
        await make_user(session, "viewer@example.com", "viewer", customer_id)
        await login(client, "viewer@example.com")

        response = await client.put(
            f"/api/machines/{machine.id}", json={"name": "renamed", "base_url": "http://x/v1"}
        )
        assert response.status_code == 403

    async def test_omitting_the_api_key_leaves_it_untouched(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        machine = await create_machine(
            scope, session, name="box", base_url="http://x/v1", api_key="s3cret"
        )
        await session.commit()
        await make_user(session, "admin@example.com", "admin", customer_id)
        await login(client, "admin@example.com")

        updated = await client.put(
            f"/api/machines/{machine.id}", json={"name": "renamed", "base_url": "http://x/v1"}
        )
        assert updated.status_code == 200
        body = updated.json()
        assert body["name"] == "renamed"
        assert body["has_api_key"] is True

    async def test_an_explicit_blank_api_key_clears_it(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        machine = await create_machine(
            scope, session, name="box", base_url="http://x/v1", api_key="s3cret"
        )
        await session.commit()
        await make_user(session, "admin@example.com", "admin", customer_id)
        await login(client, "admin@example.com")

        updated = await client.put(
            f"/api/machines/{machine.id}",
            json={"name": "box", "base_url": "http://x/v1", "api_key": ""},
        )
        assert updated.json()["has_api_key"] is False

    async def test_deleting_a_machine(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        machine = await create_machine(scope, session, name="box", base_url="http://x/v1")
        await session.commit()
        await make_user(session, "admin@example.com", "admin", customer_id)
        await login(client, "admin@example.com")

        deleted = await client.delete(f"/api/machines/{machine.id}")
        assert deleted.status_code == 204
        assert (await client.get(f"/api/machines/{machine.id}")).status_code == 404

    async def test_a_machine_in_another_workspace_is_a_404(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        _, scope_a = await create_workspace("A")
        machine_a = await create_machine(scope_a, session, name="a-box", base_url="http://a/v1")
        customer_b, _ = await create_workspace("B")
        await session.commit()
        await make_user(session, "admin@example.com", "admin", customer_b)
        await login(client, "admin@example.com")

        assert (await client.get(f"/api/machines/{machine_a.id}")).status_code == 404
        assert (await client.delete(f"/api/machines/{machine_a.id}")).status_code == 404


class TestDiscoverAndTest:
    async def test_discover_syncs_models_and_flips_currently_loaded(
        self,
        client: AsyncClient,
        session: AsyncSession,
        create_workspace: CreateWorkspace,
        monkeypatch,
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        machine = await create_machine(scope, session, name="box", base_url="http://x/v1")
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        async def fake_probe(base_url, api_key, *, timeout, transport=None):
            assert base_url == "http://x/v1"
            assert api_key is None
            return discovery.ProbeResult(
                ok=True, status=200, latency_ms=5, model_ids=["qwen3-32b"], error=None
            )

        monkeypatch.setattr("app.api.machines.probe_models", fake_probe)

        response = await client.post(f"/api/machines/{machine.id}/discover")
        assert response.status_code == 200
        assert response.json() == {
            "ok": True,
            "discovered": 1,
            "retired": 0,
            "models": ["qwen3-32b"],
            "error": None,
        }

        again = await client.get(f"/api/machines/{machine.id}")
        assert again.json()["model_count"] == 1
        assert again.json()["loaded_model_count"] == 1

    async def test_discover_reports_a_failed_probe_without_raising(
        self,
        client: AsyncClient,
        session: AsyncSession,
        create_workspace: CreateWorkspace,
        monkeypatch,
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        machine = await create_machine(scope, session, name="box", base_url="http://x/v1")
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        async def fake_probe(base_url, api_key, *, timeout, transport=None):
            return discovery.ProbeResult(
                ok=False,
                status=None,
                latency_ms=1,
                model_ids=None,
                error="Connection refused — is the server running?",
            )

        monkeypatch.setattr("app.api.machines.probe_models", fake_probe)

        response = await client.post(f"/api/machines/{machine.id}/discover")
        assert response.status_code == 200
        assert response.json() == {
            "ok": False,
            "discovered": 0,
            "retired": 0,
            "models": [],
            "error": "Connection refused — is the server running?",
        }

    async def test_a_viewer_cannot_discover(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        machine = await create_machine(scope, session, name="box", base_url="http://x/v1")
        await session.commit()
        await make_user(session, "viewer@example.com", "viewer", customer_id)
        await login(client, "viewer@example.com")

        response = await client.post(f"/api/machines/{machine.id}/discover")
        assert response.status_code == 403

    async def test_a_foreign_machine_cannot_be_discovered(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        _, scope_a = await create_workspace("A")
        machine_a = await create_machine(scope_a, session, name="a-box", base_url="http://a/v1")
        customer_b, _ = await create_workspace("B")
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_b)
        await login(client, "member@example.com")

        response = await client.post(f"/api/machines/{machine_a.id}/discover")
        assert response.status_code == 404

    async def test_test_endpoint_is_admin_only(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        machine = await create_machine(scope, session, name="box", base_url="http://x/v1")
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        response = await client.post(f"/api/machines/{machine.id}/test")
        assert response.status_code == 403

    async def test_test_endpoint_reports_latency_on_success(
        self,
        client: AsyncClient,
        session: AsyncSession,
        create_workspace: CreateWorkspace,
        monkeypatch,
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        machine = await create_machine(scope, session, name="box", base_url="http://x/v1")
        await session.commit()
        await make_user(session, "admin@example.com", "admin", customer_id)
        await login(client, "admin@example.com")

        async def fake_probe(base_url, api_key, *, timeout, transport=None):
            return discovery.ProbeResult(
                ok=True, status=200, latency_ms=12, model_ids=[], error=None
            )

        monkeypatch.setattr("app.api.machines.probe_models", fake_probe)

        response = await client.post(f"/api/machines/{machine.id}/test")
        assert response.status_code == 200
        assert response.json() == {"ok": True, "status": 200, "latency_ms": 12, "error": None}

    async def test_test_endpoint_does_not_sync_models(
        self,
        client: AsyncClient,
        session: AsyncSession,
        create_workspace: CreateWorkspace,
        monkeypatch,
    ) -> None:
        """`test` is a probe, not a discovery pass — `machine_models` must be
        untouched even when the probe reports models."""
        customer_id, scope = await create_workspace("Acme")
        machine = await create_machine(scope, session, name="box", base_url="http://x/v1")
        await session.commit()
        await make_user(session, "admin@example.com", "admin", customer_id)
        await login(client, "admin@example.com")

        async def fake_probe(base_url, api_key, *, timeout, transport=None):
            return discovery.ProbeResult(
                ok=True, status=200, latency_ms=3, model_ids=["qwen3-32b"], error=None
            )

        monkeypatch.setattr("app.api.machines.probe_models", fake_probe)

        await client.post(f"/api/machines/{machine.id}/test")

        got = await client.get(f"/api/machines/{machine.id}")
        assert got.json()["model_count"] == 0
