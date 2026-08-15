"""`/api/endpoints` end to end: real app, real Postgres, role gating.

`discover`/`test` stub out `app.services.discovery.probe_models` — its own
parsing/error-mapping is `tests/test_discovery.py`'s job, which needs no
database at all. What this file covers is what only the wired-up route can
show: the sync actually lands in `endpoint_models`, a foreign endpoint is a 404
rather than someone else's row, and each endpoint sits behind the role the
plan names.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import datetime

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import users as user_store
from app.auth.passwords import hash_password
from app.auth.policy import Role
from app.main import app
from app.models import Customer
from app.repos.endpoints import (
    create_endpoint,
    get_endpoint,
    list_endpoint_models,
    sync_discovered_models,
    touch_endpoint_model,
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


async def _make_base(
    session: AsyncSession, create_workspace: CreateWorkspace, name: str = "Base"
) -> tuple[int, Scope]:
    """A Base workspace, flagged the way the migration flags it — see
    `tests/integration/test_workspaces.py`, which explains why this is a direct
    UPDATE rather than a repository call.
    """
    customer_id, scope = await create_workspace(name)
    await session.execute(
        update(Customer).where(Customer.id == customer_id).values(is_base=True)
    )
    return customer_id, scope


class TestEndpointCrud:
    async def test_admin_creates_lists_and_reads_an_endpoint(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, _ = await create_workspace("Acme")
        await session.commit()
        await make_user(session, "admin@example.com", "admin", customer_id)
        await login(client, "admin@example.com")

        created = await client.post(
            "/api/endpoints",
            json={"name": "vLLM box", "base_url": "http://10.0.0.5:8000/v1", "api_key": "s3cret"},
        )
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["has_api_key"] is True
        assert "api_key" not in body

        listed = await client.get("/api/endpoints")
        assert [m["name"] for m in listed.json()] == ["vLLM box"]

        got = await client.get(f"/api/endpoints/{body['id']}")
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
            "/api/endpoints", json={"name": "box", "base_url": "http://x:8000/v1/"}
        )
        assert created.json()["base_url"] == "http://x:8000/v1"

    async def test_base_url_must_carry_a_scheme(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, _ = await create_workspace("Acme")
        await session.commit()
        await make_user(session, "admin@example.com", "admin", customer_id)
        await login(client, "admin@example.com")

        response = await client.post(
            "/api/endpoints", json={"name": "box", "base_url": "x:8000/v1"}
        )
        assert response.status_code == 422

    async def test_a_member_cannot_create_an_endpoint(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, _ = await create_workspace("Acme")
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        response = await client.post("/api/endpoints", json={"name": "box", "base_url": "http://x/v1"})
        assert response.status_code == 403

    async def test_every_signed_in_role_can_list_and_read(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        endpoint = await create_endpoint(scope, session, name="box", base_url="http://x/v1")
        await session.commit()
        await make_user(session, "viewer@example.com", "viewer", customer_id)
        await login(client, "viewer@example.com")

        assert (await client.get("/api/endpoints")).status_code == 200
        assert (await client.get(f"/api/endpoints/{endpoint.id}")).status_code == 200

    async def test_a_viewer_cannot_write(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        endpoint = await create_endpoint(scope, session, name="box", base_url="http://x/v1")
        await session.commit()
        await make_user(session, "viewer@example.com", "viewer", customer_id)
        await login(client, "viewer@example.com")

        response = await client.put(
            f"/api/endpoints/{endpoint.id}", json={"name": "renamed", "base_url": "http://x/v1"}
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
        endpoint = await create_endpoint(
            scope, session, name="box", base_url="http://x/v1", api_key="s3cret"
        )
        # Captured before `expire_all()` below: an expired attribute needs an
        # `await` to refresh, and `endpoint` is a plain reference here.
        endpoint_id = endpoint.id
        await session.commit()
        await make_user(session, "admin@example.com", "admin", customer_id)
        await login(client, "admin@example.com")

        updated = await client.put(
            f"/api/endpoints/{endpoint_id}", json={"name": "renamed", "base_url": "http://x/v1"}
        )
        assert updated.status_code == 200
        body = updated.json()
        assert body["name"] == "renamed"
        assert body["has_api_key"] is True

        session.expire_all()
        stored = await get_endpoint(scope, session, endpoint_id)
        assert stored is not None
        assert stored.api_key == "s3cret"

    async def test_sharing_survives_a_save_that_says_nothing_about_it(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        """`is_global` is patch-like on `PUT` for the same reason `api_key` is,
        though the risk runs the other way: it defaults to `false`, so a client
        that has never heard of sharing would un-share the row on every save.

        The endpoint has to be *visible* to the borrower and *unchanged* by
        the owner's ordinary rename, which is exactly the pair a silently
        cleared flag would break.
        """
        base_id, base = await _make_base(session, create_workspace)
        _, other = await create_workspace("Acme")
        endpoint_id = (
            await create_endpoint(
                base, session, name="DGX Spark", base_url="http://x/v1", is_global=True
            )
        ).id
        await session.commit()
        await make_user(session, "admin@example.com", "admin", base_id)
        await login(client, "admin@example.com")

        renamed = await client.put(
            f"/api/endpoints/{endpoint_id}",
            json={"name": "DGX Spark 2", "base_url": "http://x/v1"},
        )
        assert renamed.status_code == 200
        assert renamed.json()["is_global"] is True
        assert renamed.json()["editable"] is True

        # Un-sharing is a deliberate `false`, not an omission.
        unshared = await client.put(
            f"/api/endpoints/{endpoint_id}",
            json={"name": "DGX Spark 2", "base_url": "http://x/v1", "is_global": False},
        )
        assert unshared.json()["is_global"] is False

        session.expire_all()
        assert await get_endpoint(other, session, endpoint_id) is None

    async def test_a_borrowed_endpoint_is_read_only_and_says_so(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        """`scope_where` already makes the write match no row; the 403 exists so
        the caller is not told a no-op succeeded.
        """
        _, base = await _make_base(session, create_workspace)
        other_id, _ = await create_workspace("Acme")
        endpoint_id = (
            await create_endpoint(
                base, session, name="DGX Spark", base_url="http://x/v1", is_global=True
            )
        ).id
        await session.commit()
        await make_user(session, "admin@example.com", "admin", other_id)
        await login(client, "admin@example.com")

        listed = (await client.get("/api/endpoints")).json()
        assert [(row["id"], row["is_global"], row["editable"]) for row in listed] == [
            (endpoint_id, True, False)
        ]

        refused = await client.put(
            f"/api/endpoints/{endpoint_id}", json={"name": "mine now", "base_url": "http://x/v1"}
        )
        assert refused.status_code == 403
        assert "Base workspace" in refused.json()["message"]
        assert (await client.delete(f"/api/endpoints/{endpoint_id}")).status_code == 403

        # And it cannot be made global from here either — a 400, because the
        # request is well formed and simply asks for something only Base may.
        local = await client.post(
            "/api/endpoints", json={"name": "mine", "base_url": "http://y/v1", "is_global": True}
        )
        assert local.status_code == 400
        assert "Base workspace" in local.json()["message"]

    async def test_a_named_api_key_replaces_the_stored_one(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        endpoint = await create_endpoint(
            scope, session, name="box", base_url="http://x/v1", api_key="s3cret"
        )
        # Captured before `expire_all()` below: an expired attribute needs an
        # `await` to refresh, and `endpoint` is a plain reference here.
        endpoint_id = endpoint.id
        await session.commit()
        await make_user(session, "admin@example.com", "admin", customer_id)
        await login(client, "admin@example.com")

        updated = await client.put(
            f"/api/endpoints/{endpoint_id}",
            json={"name": "box", "base_url": "http://x/v1", "api_key": "rotated"},
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["has_api_key"] is True

        session.expire_all()
        stored = await get_endpoint(scope, session, endpoint_id)
        assert stored is not None
        assert stored.api_key == "rotated"

    async def test_an_explicit_null_api_key_clears_it(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        """`null` is how the editor's "remove the stored key" action says so
        deliberately — the one shape that is meant to destroy a credential.
        """
        customer_id, scope = await create_workspace("Acme")
        endpoint = await create_endpoint(
            scope, session, name="box", base_url="http://x/v1", api_key="s3cret"
        )
        # Captured before `expire_all()` below: an expired attribute needs an
        # `await` to refresh, and `endpoint` is a plain reference here.
        endpoint_id = endpoint.id
        await session.commit()
        await make_user(session, "admin@example.com", "admin", customer_id)
        await login(client, "admin@example.com")

        updated = await client.put(
            f"/api/endpoints/{endpoint_id}",
            json={"name": "box", "base_url": "http://x/v1", "api_key": None},
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["has_api_key"] is False

        session.expire_all()
        stored = await get_endpoint(scope, session, endpoint_id)
        assert stored is not None
        assert stored.api_key is None

    async def test_an_explicit_blank_api_key_clears_it(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        endpoint = await create_endpoint(
            scope, session, name="box", base_url="http://x/v1", api_key="s3cret"
        )
        # Captured before `expire_all()` below: an expired attribute needs an
        # `await` to refresh, and `endpoint` is a plain reference here.
        endpoint_id = endpoint.id
        await session.commit()
        await make_user(session, "admin@example.com", "admin", customer_id)
        await login(client, "admin@example.com")

        updated = await client.put(
            f"/api/endpoints/{endpoint_id}",
            json={"name": "box", "base_url": "http://x/v1", "api_key": ""},
        )
        assert updated.json()["has_api_key"] is False

        session.expire_all()
        stored = await get_endpoint(scope, session, endpoint_id)
        assert stored is not None
        assert stored.api_key is None

    async def test_deleting_an_endpoint(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        endpoint = await create_endpoint(scope, session, name="box", base_url="http://x/v1")
        await session.commit()
        await make_user(session, "admin@example.com", "admin", customer_id)
        await login(client, "admin@example.com")

        deleted = await client.delete(f"/api/endpoints/{endpoint.id}")
        assert deleted.status_code == 204
        assert (await client.get(f"/api/endpoints/{endpoint.id}")).status_code == 404

    async def test_an_endpoint_in_another_workspace_is_a_404(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        _, scope_a = await create_workspace("A")
        endpoint_a = await create_endpoint(scope_a, session, name="a-box", base_url="http://a/v1")
        customer_b, _ = await create_workspace("B")
        await session.commit()
        await make_user(session, "admin@example.com", "admin", customer_b)
        await login(client, "admin@example.com")

        assert (await client.get(f"/api/endpoints/{endpoint_a.id}")).status_code == 404
        assert (await client.delete(f"/api/endpoints/{endpoint_a.id}")).status_code == 404


class TestEndpointPlatformAndParams:
    """`platform` and `default_params` — a catalog key and a request-body
    params object, both content rather than credentials, so unlike `api_key`
    they round-trip through the view instead of being hidden."""

    async def test_creating_with_platform_and_nested_default_params(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        await session.commit()
        await make_user(session, "admin@example.com", "admin", customer_id)
        await login(client, "admin@example.com")

        created = await client.post(
            "/api/endpoints",
            json={
                "name": "vLLM box",
                "base_url": "http://10.0.0.5:8000/v1",
                "platform": "vllm",
                "default_params": {
                    "temperature": 0.2,
                    "chat_template_kwargs": {"enable_thinking": False},
                },
            },
        )
        assert created.status_code == 201, created.text
        endpoint_id = created.json()["id"]

        # View returns platform and the parsed dict.
        assert created.json()["platform"] == "vllm"
        assert created.json()["default_params"] == {
            "temperature": 0.2,
            "chat_template_kwargs": {"enable_thinking": False},
        }

        # And the stored columns, not just the view.
        session.expire_all()
        stored = await get_endpoint(scope, session, endpoint_id)
        assert stored is not None
        assert stored.platform == "vllm"
        assert json.loads(stored.default_params) == {
            "temperature": 0.2,
            "chat_template_kwargs": {"enable_thinking": False},
        }

    async def test_creating_without_the_fields_defaults_to_generic_with_no_params(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        await session.commit()
        await make_user(session, "admin@example.com", "admin", customer_id)
        await login(client, "admin@example.com")

        created = await client.post(
            "/api/endpoints", json={"name": "box", "base_url": "http://x/v1"}
        )
        assert created.status_code == 201, created.text
        assert created.json()["platform"] == "generic"
        assert created.json()["default_params"] is None

        session.expire_all()
        stored = await get_endpoint(scope, session, created.json()["id"])
        assert stored is not None
        assert stored.platform == "generic"
        assert stored.default_params is None

    async def test_a_put_omitting_both_fields_leaves_them_untouched(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        endpoint = await create_endpoint(
            scope,
            session,
            name="box",
            base_url="http://x/v1",
            platform="ollama",
            default_params=json.dumps({"temperature": 0.5}),
        )
        endpoint_id = endpoint.id
        await session.commit()
        await make_user(session, "admin@example.com", "admin", customer_id)
        await login(client, "admin@example.com")

        updated = await client.put(
            f"/api/endpoints/{endpoint_id}",
            json={"name": "renamed", "base_url": "http://x/v1"},
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["platform"] == "ollama"
        assert updated.json()["default_params"] == {"temperature": 0.5}

        session.expire_all()
        stored = await get_endpoint(scope, session, endpoint_id)
        assert stored is not None
        assert stored.platform == "ollama"
        assert json.loads(stored.default_params) == {"temperature": 0.5}

    async def test_a_put_sending_default_params_null_clears_it(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        endpoint = await create_endpoint(
            scope,
            session,
            name="box",
            base_url="http://x/v1",
            default_params=json.dumps({"temperature": 0.5}),
        )
        endpoint_id = endpoint.id
        await session.commit()
        await make_user(session, "admin@example.com", "admin", customer_id)
        await login(client, "admin@example.com")

        updated = await client.put(
            f"/api/endpoints/{endpoint_id}",
            json={"name": "box", "base_url": "http://x/v1", "default_params": None},
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["default_params"] is None

        session.expire_all()
        stored = await get_endpoint(scope, session, endpoint_id)
        assert stored is not None
        assert stored.default_params is None

    async def test_a_reserved_key_in_default_params_is_refused_with_a_named_message(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        endpoint = await create_endpoint(scope, session, name="box", base_url="http://x/v1")
        endpoint_id = endpoint.id
        await session.commit()
        await make_user(session, "admin@example.com", "admin", customer_id)
        await login(client, "admin@example.com")

        response = await client.put(
            f"/api/endpoints/{endpoint_id}",
            json={"name": "box", "base_url": "http://x/v1", "default_params": {"tools": []}},
        )
        assert response.status_code == 422
        assert "tools" in response.text


class TestEndpointModels:
    """`GET/POST /api/endpoints/{id}/models` — the history table the new-run
    page's model picker and the endpoint detail page both read.
    """

    async def test_list_puts_currently_loaded_models_first(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        endpoint = await create_endpoint(scope, session, name="box", base_url="http://x/v1")
        # Seen once, never since — the row a discovery pass would have left
        # behind with `currently_loaded` false.
        await touch_endpoint_model(
            scope, session, endpoint_id=endpoint.id, model_id="retired-model", source="manual"
        )
        await sync_discovered_models(scope, session, endpoint.id, ["loaded-model"])
        await session.commit()
        await make_user(session, "viewer@example.com", "viewer", customer_id)
        await login(client, "viewer@example.com")

        response = await client.get(f"/api/endpoints/{endpoint.id}/models")
        assert response.status_code == 200, response.text
        rows = response.json()
        assert [row["model_id"] for row in rows] == ["loaded-model", "retired-model"]
        # The SPA's `EndpointModel` interface, field for field.
        assert set(rows[0]) == {
            "id",
            "endpoint_id",
            "model_id",
            "currently_loaded",
            "first_seen_at",
            "last_seen_at",
            "source",
        }
        assert rows[0]["currently_loaded"] is True
        assert rows[0]["source"] == "discovered"
        assert rows[1]["currently_loaded"] is False
        assert rows[1]["endpoint_id"] == endpoint.id

    async def test_an_endpoint_with_no_models_lists_nothing(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        """The distinction the endpoint detail page depends on: an empty history
        is a 200 with `[]`, not the 404 a missing endpoint answers."""
        customer_id, scope = await create_workspace("Acme")
        endpoint = await create_endpoint(scope, session, name="box", base_url="http://x/v1")
        await session.commit()
        await make_user(session, "viewer@example.com", "viewer", customer_id)
        await login(client, "viewer@example.com")

        response = await client.get(f"/api/endpoints/{endpoint.id}/models")
        assert response.status_code == 200
        assert response.json() == []

    async def test_a_manual_add_creates_the_row(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        endpoint = await create_endpoint(scope, session, name="box", base_url="http://x/v1")
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        created = await client.post(
            f"/api/endpoints/{endpoint.id}/models", json={"model_id": "  qwen3-32b  "}
        )
        assert created.status_code == 200, created.text
        body = created.json()
        assert body["model_id"] == "qwen3-32b"
        assert body["source"] == "manual"
        # A manually named model is not claimed to be loaded — only discovery
        # says that.
        assert body["currently_loaded"] is False

        listed = await client.get(f"/api/endpoints/{endpoint.id}/models")
        assert [row["model_id"] for row in listed.json()] == ["qwen3-32b"]

    async def test_adding_a_model_twice_upserts_rather_than_failing(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        endpoint = await create_endpoint(scope, session, name="box", base_url="http://x/v1")
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        url = f"/api/endpoints/{endpoint.id}/models"
        first = await client.post(url, json={"model_id": "qwen"})
        second = await client.post(url, json={"model_id": "qwen"})
        assert first.status_code == 200
        assert second.status_code == 200, second.text
        assert second.json()["id"] == first.json()["id"]

        # The sighting is bumped; the row's own history is not rewritten.
        assert second.json()["first_seen_at"] == first.json()["first_seen_at"]
        assert datetime.fromisoformat(second.json()["last_seen_at"]) >= datetime.fromisoformat(
            first.json()["last_seen_at"]
        )

        listed = await client.get(f"/api/endpoints/{endpoint.id}/models")
        assert len(listed.json()) == 1

    async def test_a_model_discovery_already_recorded_keeps_its_source(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        """`source` says how a model was *first* learned about, so naming a
        discovered model by hand must not relabel it."""
        customer_id, scope = await create_workspace("Acme")
        endpoint = await create_endpoint(scope, session, name="box", base_url="http://x/v1")
        await sync_discovered_models(scope, session, endpoint.id, ["qwen"])
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        response = await client.post(
            f"/api/endpoints/{endpoint.id}/models", json={"model_id": "qwen"}
        )
        assert response.status_code == 200
        assert response.json()["source"] == "discovered"
        assert response.json()["currently_loaded"] is True

    async def test_a_blank_model_id_is_refused(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        endpoint = await create_endpoint(scope, session, name="box", base_url="http://x/v1")
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        assert (
            await client.post(f"/api/endpoints/{endpoint.id}/models", json={"model_id": ""})
        ).status_code == 422
        assert (
            await client.post(f"/api/endpoints/{endpoint.id}/models", json={"model_id": "   "})
        ).status_code == 422
        assert (await client.get(f"/api/endpoints/{endpoint.id}/models")).json() == []

    async def test_a_viewer_cannot_add_a_model(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        endpoint = await create_endpoint(scope, session, name="box", base_url="http://x/v1")
        await session.commit()
        await make_user(session, "viewer@example.com", "viewer", customer_id)
        await login(client, "viewer@example.com")

        response = await client.post(
            f"/api/endpoints/{endpoint.id}/models", json={"model_id": "qwen"}
        )
        assert response.status_code == 403
        assert (await client.get(f"/api/endpoints/{endpoint.id}/models")).json() == []

    async def test_another_workspaces_endpoint_is_a_404_both_ways(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        _, scope_a = await create_workspace("A")
        endpoint_a = await create_endpoint(scope_a, session, name="a-box", base_url="http://a/v1")
        await touch_endpoint_model(
            scope_a, session, endpoint_id=endpoint_a.id, model_id="a-model", source="manual"
        )
        customer_b, _ = await create_workspace("B")
        await session.commit()
        await make_user(session, "admin@example.com", "admin", customer_b)
        await login(client, "admin@example.com")

        assert (await client.get(f"/api/endpoints/{endpoint_a.id}/models")).status_code == 404
        posted = await client.post(
            f"/api/endpoints/{endpoint_a.id}/models", json={"model_id": "smuggled"}
        )
        assert posted.status_code == 404

        # And nothing landed on the other workspace's endpoint.
        surviving = await list_endpoint_models(scope_a, session, endpoint_id=endpoint_a.id)
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
        endpoint = await create_endpoint(scope, session, name="box", base_url="http://x/v1")
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        async def fake_probe(base_url, api_key, *, timeout, transport=None):
            assert base_url == "http://x/v1"
            assert api_key is None
            return discovery.ProbeResult(
                ok=True, status=200, latency_ms=5, model_ids=["qwen3-32b"], error=None
            )

        monkeypatch.setattr("app.api.endpoints.probe_models", fake_probe)

        response = await client.post(f"/api/endpoints/{endpoint.id}/discover")
        assert response.status_code == 200
        assert response.json() == {
            "ok": True,
            "discovered": 1,
            "retired": 0,
            "models": ["qwen3-32b"],
            "error": None,
        }

        again = await client.get(f"/api/endpoints/{endpoint.id}")
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
        endpoint = await create_endpoint(scope, session, name="box", base_url="http://x/v1")
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

        monkeypatch.setattr("app.api.endpoints.probe_models", fake_probe)

        response = await client.post(f"/api/endpoints/{endpoint.id}/discover")
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
        endpoint = await create_endpoint(scope, session, name="box", base_url="http://x/v1")
        await session.commit()
        await make_user(session, "viewer@example.com", "viewer", customer_id)
        await login(client, "viewer@example.com")

        response = await client.post(f"/api/endpoints/{endpoint.id}/discover")
        assert response.status_code == 403

    async def test_a_foreign_endpoint_cannot_be_discovered(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        _, scope_a = await create_workspace("A")
        endpoint_a = await create_endpoint(scope_a, session, name="a-box", base_url="http://a/v1")
        customer_b, _ = await create_workspace("B")
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_b)
        await login(client, "member@example.com")

        response = await client.post(f"/api/endpoints/{endpoint_a.id}/discover")
        assert response.status_code == 404

    async def test_test_endpoint_is_admin_only(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        endpoint = await create_endpoint(scope, session, name="box", base_url="http://x/v1")
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        response = await client.post(f"/api/endpoints/{endpoint.id}/test")
        assert response.status_code == 403

    async def test_test_endpoint_reports_latency_on_success(
        self,
        client: AsyncClient,
        session: AsyncSession,
        create_workspace: CreateWorkspace,
        monkeypatch,
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        endpoint = await create_endpoint(scope, session, name="box", base_url="http://x/v1")
        await session.commit()
        await make_user(session, "admin@example.com", "admin", customer_id)
        await login(client, "admin@example.com")

        async def fake_probe(base_url, api_key, *, timeout, transport=None):
            return discovery.ProbeResult(
                ok=True, status=200, latency_ms=12, model_ids=[], error=None
            )

        monkeypatch.setattr("app.api.endpoints.probe_models", fake_probe)

        response = await client.post(f"/api/endpoints/{endpoint.id}/test")
        assert response.status_code == 200
        assert response.json() == {"ok": True, "status": 200, "latency_ms": 12, "error": None}

    async def test_test_endpoint_does_not_sync_models(
        self,
        client: AsyncClient,
        session: AsyncSession,
        create_workspace: CreateWorkspace,
        monkeypatch,
    ) -> None:
        """`test` is a probe, not a discovery pass — `endpoint_models` must be
        untouched even when the probe reports models."""
        customer_id, scope = await create_workspace("Acme")
        endpoint = await create_endpoint(scope, session, name="box", base_url="http://x/v1")
        await session.commit()
        await make_user(session, "admin@example.com", "admin", customer_id)
        await login(client, "admin@example.com")

        async def fake_probe(base_url, api_key, *, timeout, transport=None):
            return discovery.ProbeResult(
                ok=True, status=200, latency_ms=3, model_ids=["qwen3-32b"], error=None
            )

        monkeypatch.setattr("app.api.endpoints.probe_models", fake_probe)

        await client.post(f"/api/endpoints/{endpoint.id}/test")

        got = await client.get(f"/api/endpoints/{endpoint.id}")
        assert got.json()["model_count"] == 0


class TestConnectionWithoutAnEndpoint:
    """`POST /api/endpoints/test-connection` — the "New endpoint" dialog's
    probe, run against a base URL before any row exists to attach it to."""

    async def test_admin_gets_the_probe_result(
        self,
        client: AsyncClient,
        session: AsyncSession,
        create_workspace: CreateWorkspace,
        monkeypatch,
    ) -> None:
        # A plain POST to this literal path also proves it is not swallowed by
        # `GET/PUT/DELETE /{endpoint_id}` — a match there would 422 trying to
        # convert "test-connection" to an int, never reaching `fake_probe`.
        customer_id, _ = await create_workspace("Acme")
        await session.commit()
        await make_user(session, "admin@example.com", "admin", customer_id)
        await login(client, "admin@example.com")

        async def fake_probe(base_url, api_key, *, timeout, transport=None):
            assert base_url == "http://new-box/v1"
            assert api_key == "s3cret"
            return discovery.ProbeResult(
                ok=True, status=200, latency_ms=7, model_ids=["qwen"], error=None
            )

        monkeypatch.setattr("app.api.endpoints.probe_models", fake_probe)

        response = await client.post(
            "/api/endpoints/test-connection",
            json={"base_url": "http://new-box/v1", "api_key": "s3cret"},
        )
        assert response.status_code == 200, response.text
        assert response.json() == {"ok": True, "status": 200, "latency_ms": 7, "error": None}

    async def test_a_failed_probe_still_answers_200(
        self,
        client: AsyncClient,
        session: AsyncSession,
        create_workspace: CreateWorkspace,
        monkeypatch,
    ) -> None:
        customer_id, _ = await create_workspace("Acme")
        await session.commit()
        await make_user(session, "admin@example.com", "admin", customer_id)
        await login(client, "admin@example.com")

        async def fake_probe(base_url, api_key, *, timeout, transport=None):
            return discovery.ProbeResult(
                ok=False, status=None, latency_ms=2, model_ids=None, error="Connection refused"
            )

        monkeypatch.setattr("app.api.endpoints.probe_models", fake_probe)

        response = await client.post(
            "/api/endpoints/test-connection", json={"base_url": "http://nope/v1"}
        )
        assert response.status_code == 200
        assert response.json() == {
            "ok": False,
            "status": None,
            "latency_ms": 2,
            "error": "Connection refused",
        }

    async def test_a_member_is_refused(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, _ = await create_workspace("Acme")
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        response = await client.post(
            "/api/endpoints/test-connection", json={"base_url": "http://x/v1"}
        )
        assert response.status_code == 403
