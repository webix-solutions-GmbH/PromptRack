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
from datetime import datetime

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import users as user_store
from app.auth.passwords import hash_password
from app.auth.policy import Role
from app.main import app
from app.repos.machines import (
    create_machine,
    get_machine,
    list_machine_models,
    sync_discovered_models,
    touch_machine_model,
)
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
        """The editor never sees the stored key, so it cannot echo it back — a
        save that says nothing about the field has to preserve it verbatim, not
        just leave `has_api_key` true.
        """
        customer_id, scope = await create_workspace("Acme")
        machine = await create_machine(
            scope, session, name="box", base_url="http://x/v1", api_key="s3cret"
        )
        # Captured before `expire_all()` below: an expired attribute needs an
        # `await` to refresh, and `machine` is a plain reference here.
        machine_id = machine.id
        await session.commit()
        await make_user(session, "admin@example.com", "admin", customer_id)
        await login(client, "admin@example.com")

        updated = await client.put(
            f"/api/machines/{machine_id}", json={"name": "renamed", "base_url": "http://x/v1"}
        )
        assert updated.status_code == 200
        body = updated.json()
        assert body["name"] == "renamed"
        assert body["has_api_key"] is True

        session.expire_all()
        stored = await get_machine(scope, session, machine_id)
        assert stored is not None
        assert stored.api_key == "s3cret"

    async def test_a_named_api_key_replaces_the_stored_one(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        machine = await create_machine(
            scope, session, name="box", base_url="http://x/v1", api_key="s3cret"
        )
        # Captured before `expire_all()` below: an expired attribute needs an
        # `await` to refresh, and `machine` is a plain reference here.
        machine_id = machine.id
        await session.commit()
        await make_user(session, "admin@example.com", "admin", customer_id)
        await login(client, "admin@example.com")

        updated = await client.put(
            f"/api/machines/{machine_id}",
            json={"name": "box", "base_url": "http://x/v1", "api_key": "rotated"},
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["has_api_key"] is True

        session.expire_all()
        stored = await get_machine(scope, session, machine_id)
        assert stored is not None
        assert stored.api_key == "rotated"

    async def test_an_explicit_null_api_key_clears_it(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        """`null` is how the editor's "remove the stored key" action says so
        deliberately — the one shape that is meant to destroy a credential.
        """
        customer_id, scope = await create_workspace("Acme")
        machine = await create_machine(
            scope, session, name="box", base_url="http://x/v1", api_key="s3cret"
        )
        # Captured before `expire_all()` below: an expired attribute needs an
        # `await` to refresh, and `machine` is a plain reference here.
        machine_id = machine.id
        await session.commit()
        await make_user(session, "admin@example.com", "admin", customer_id)
        await login(client, "admin@example.com")

        updated = await client.put(
            f"/api/machines/{machine_id}",
            json={"name": "box", "base_url": "http://x/v1", "api_key": None},
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["has_api_key"] is False

        session.expire_all()
        stored = await get_machine(scope, session, machine_id)
        assert stored is not None
        assert stored.api_key is None

    async def test_an_explicit_blank_api_key_clears_it(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        machine = await create_machine(
            scope, session, name="box", base_url="http://x/v1", api_key="s3cret"
        )
        # Captured before `expire_all()` below: an expired attribute needs an
        # `await` to refresh, and `machine` is a plain reference here.
        machine_id = machine.id
        await session.commit()
        await make_user(session, "admin@example.com", "admin", customer_id)
        await login(client, "admin@example.com")

        updated = await client.put(
            f"/api/machines/{machine_id}",
            json={"name": "box", "base_url": "http://x/v1", "api_key": ""},
        )
        assert updated.json()["has_api_key"] is False

        session.expire_all()
        stored = await get_machine(scope, session, machine_id)
        assert stored is not None
        assert stored.api_key is None

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


class TestMachineModels:
    """`GET/POST /api/machines/{id}/models` — the history table the new-run
    page's model picker and the machine detail page both read.
    """

    async def test_list_puts_currently_loaded_models_first(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        machine = await create_machine(scope, session, name="box", base_url="http://x/v1")
        # Seen once, never since — the row a discovery pass would have left
        # behind with `currently_loaded` false.
        await touch_machine_model(
            scope, session, machine_id=machine.id, model_id="retired-model", source="manual"
        )
        await sync_discovered_models(scope, session, machine.id, ["loaded-model"])
        await session.commit()
        await make_user(session, "viewer@example.com", "viewer", customer_id)
        await login(client, "viewer@example.com")

        response = await client.get(f"/api/machines/{machine.id}/models")
        assert response.status_code == 200, response.text
        rows = response.json()
        assert [row["model_id"] for row in rows] == ["loaded-model", "retired-model"]
        # The SPA's `MachineModel` interface, field for field.
        assert set(rows[0]) == {
            "id",
            "machine_id",
            "model_id",
            "currently_loaded",
            "first_seen_at",
            "last_seen_at",
            "source",
        }
        assert rows[0]["currently_loaded"] is True
        assert rows[0]["source"] == "discovered"
        assert rows[1]["currently_loaded"] is False
        assert rows[1]["machine_id"] == machine.id

    async def test_a_machine_with_no_models_lists_nothing(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        """The distinction the machine detail page depends on: an empty history
        is a 200 with `[]`, not the 404 a missing machine answers."""
        customer_id, scope = await create_workspace("Acme")
        machine = await create_machine(scope, session, name="box", base_url="http://x/v1")
        await session.commit()
        await make_user(session, "viewer@example.com", "viewer", customer_id)
        await login(client, "viewer@example.com")

        response = await client.get(f"/api/machines/{machine.id}/models")
        assert response.status_code == 200
        assert response.json() == []

    async def test_a_manual_add_creates_the_row(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        machine = await create_machine(scope, session, name="box", base_url="http://x/v1")
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        created = await client.post(
            f"/api/machines/{machine.id}/models", json={"model_id": "  qwen3-32b  "}
        )
        assert created.status_code == 200, created.text
        body = created.json()
        assert body["model_id"] == "qwen3-32b"
        assert body["source"] == "manual"
        # A manually named model is not claimed to be loaded — only discovery
        # says that.
        assert body["currently_loaded"] is False

        listed = await client.get(f"/api/machines/{machine.id}/models")
        assert [row["model_id"] for row in listed.json()] == ["qwen3-32b"]

    async def test_adding_a_model_twice_upserts_rather_than_failing(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        machine = await create_machine(scope, session, name="box", base_url="http://x/v1")
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        first = await client.post(f"/api/machines/{machine.id}/models", json={"model_id": "qwen"})
        second = await client.post(f"/api/machines/{machine.id}/models", json={"model_id": "qwen"})
        assert first.status_code == 200
        assert second.status_code == 200, second.text
        assert second.json()["id"] == first.json()["id"]

        # The sighting is bumped; the row's own history is not rewritten.
        assert second.json()["first_seen_at"] == first.json()["first_seen_at"]
        assert datetime.fromisoformat(second.json()["last_seen_at"]) >= datetime.fromisoformat(
            first.json()["last_seen_at"]
        )

        listed = await client.get(f"/api/machines/{machine.id}/models")
        assert len(listed.json()) == 1

    async def test_a_model_discovery_already_recorded_keeps_its_source(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        """`source` says how a model was *first* learned about, so naming a
        discovered model by hand must not relabel it."""
        customer_id, scope = await create_workspace("Acme")
        machine = await create_machine(scope, session, name="box", base_url="http://x/v1")
        await sync_discovered_models(scope, session, machine.id, ["qwen"])
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        response = await client.post(
            f"/api/machines/{machine.id}/models", json={"model_id": "qwen"}
        )
        assert response.status_code == 200
        assert response.json()["source"] == "discovered"
        assert response.json()["currently_loaded"] is True

    async def test_a_blank_model_id_is_refused(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        machine = await create_machine(scope, session, name="box", base_url="http://x/v1")
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        assert (
            await client.post(f"/api/machines/{machine.id}/models", json={"model_id": ""})
        ).status_code == 422
        assert (
            await client.post(f"/api/machines/{machine.id}/models", json={"model_id": "   "})
        ).status_code == 422
        assert (await client.get(f"/api/machines/{machine.id}/models")).json() == []

    async def test_a_viewer_cannot_add_a_model(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        machine = await create_machine(scope, session, name="box", base_url="http://x/v1")
        await session.commit()
        await make_user(session, "viewer@example.com", "viewer", customer_id)
        await login(client, "viewer@example.com")

        response = await client.post(
            f"/api/machines/{machine.id}/models", json={"model_id": "qwen"}
        )
        assert response.status_code == 403
        assert (await client.get(f"/api/machines/{machine.id}/models")).json() == []

    async def test_another_workspaces_machine_is_a_404_both_ways(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        _, scope_a = await create_workspace("A")
        machine_a = await create_machine(scope_a, session, name="a-box", base_url="http://a/v1")
        await touch_machine_model(
            scope_a, session, machine_id=machine_a.id, model_id="a-model", source="manual"
        )
        customer_b, _ = await create_workspace("B")
        await session.commit()
        await make_user(session, "admin@example.com", "admin", customer_b)
        await login(client, "admin@example.com")

        assert (await client.get(f"/api/machines/{machine_a.id}/models")).status_code == 404
        posted = await client.post(
            f"/api/machines/{machine_a.id}/models", json={"model_id": "smuggled"}
        )
        assert posted.status_code == 404

        # And nothing landed on the other workspace's machine.
        surviving = await list_machine_models(scope_a, session, machine_id=machine_a.id)
        assert [row.model_id for row in surviving] == ["a-model"]


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
