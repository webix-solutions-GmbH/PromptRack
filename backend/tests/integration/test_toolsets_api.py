"""`/api/toolsets` end to end: real app, real Postgres, role gating.

`discover` stubs out `app.services.mcp_client.list_mcp_tools` — its own
transport/parsing is `app.services.mcp_client`'s job, exercised directly
against a real MCP server in development (see the module docstring). What
this file covers is what only the wired-up route can show: the sync actually
lands in `tools`, a foreign toolset is a 404 rather than someone else's row,
credentials never round-trip, and each endpoint sits behind the role the plan
names.
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
from app.repos.toolsets import create_tool, create_toolset, get_toolset
from app.scope import Scope
from app.services import mcp_client

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


class TestToolsetCrud:
    async def test_admin_creates_lists_and_reads_a_manual_toolset(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, _ = await create_workspace("Acme")
        await session.commit()
        await make_user(session, "admin@example.com", "admin", customer_id)
        await login(client, "admin@example.com")

        created = await client.post(
            "/api/toolsets", json={"name": "Odoo (manual)", "description": "hand-authored"}
        )
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["kind"] == "manual"
        assert body["has_mcp_headers"] is False
        assert body["tools"] == []

        listed = await client.get("/api/toolsets")
        assert [t["name"] for t in listed.json()] == ["Odoo (manual)"]

        got = await client.get(f"/api/toolsets/{body['id']}")
        assert got.status_code == 200
        assert got.json()["description"] == "hand-authored"

    async def test_creating_an_mcp_toolset_requires_a_url(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, _ = await create_workspace("Acme")
        await session.commit()
        await make_user(session, "admin@example.com", "admin", customer_id)
        await login(client, "admin@example.com")

        response = await client.post("/api/toolsets", json={"name": "no url", "kind": "mcp"})
        assert response.status_code == 422

        response = await client.post(
            "/api/toolsets", json={"name": "bad scheme", "kind": "mcp", "mcp_url": "x://y"}
        )
        assert response.status_code == 422

    async def test_mcp_headers_never_round_trip(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, _ = await create_workspace("Acme")
        await session.commit()
        await make_user(session, "admin@example.com", "admin", customer_id)
        await login(client, "admin@example.com")

        created = await client.post(
            "/api/toolsets",
            json={
                "name": "Websearch",
                "kind": "mcp",
                "mcp_url": "http://mcp:9000/mcp",
                "mcp_headers": '{"Authorization": "Bearer s3cret"}',
            },
        )
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["has_mcp_headers"] is True
        assert "mcp_headers" not in body

    async def test_omitting_mcp_headers_on_update_leaves_them_untouched(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        """The editor never sees the stored headers, so it cannot echo them back
        — a save that says nothing about the field has to preserve them
        verbatim, not just leave `has_mcp_headers` true.
        """
        customer_id, scope = await create_workspace("Acme")
        toolset = await create_toolset(
            scope,
            session,
            name="Websearch",
            kind="mcp",
            mcp_url="http://mcp:9000/mcp",
            mcp_headers='{"Authorization": "Bearer s3cret"}',
        )
        # Captured before `expire_all()` below: an expired attribute needs an
        # `await` to refresh, and `toolset` is a plain reference here.
        toolset_id = toolset.id
        await session.commit()
        await make_user(session, "admin@example.com", "admin", customer_id)
        await login(client, "admin@example.com")

        updated = await client.put(
            f"/api/toolsets/{toolset_id}",
            json={"name": "Websearch v2", "kind": "mcp", "mcp_url": "http://mcp:9000/mcp"},
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["has_mcp_headers"] is True

        session.expire_all()
        stored = await get_toolset(scope, session, toolset_id)
        assert stored is not None
        assert stored.mcp_headers == '{"Authorization": "Bearer s3cret"}'

    async def test_named_mcp_headers_replace_the_stored_ones(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        toolset = await create_toolset(
            scope,
            session,
            name="Websearch",
            kind="mcp",
            mcp_url="http://mcp:9000/mcp",
            mcp_headers='{"Authorization": "Bearer s3cret"}',
        )
        # Captured before `expire_all()` below: an expired attribute needs an
        # `await` to refresh, and `toolset` is a plain reference here.
        toolset_id = toolset.id
        await session.commit()
        await make_user(session, "admin@example.com", "admin", customer_id)
        await login(client, "admin@example.com")

        updated = await client.put(
            f"/api/toolsets/{toolset_id}",
            json={
                "name": "Websearch",
                "kind": "mcp",
                "mcp_url": "http://mcp:9000/mcp",
                "mcp_headers": '{"Authorization": "Bearer rotated"}',
            },
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["has_mcp_headers"] is True

        session.expire_all()
        stored = await get_toolset(scope, session, toolset_id)
        assert stored is not None
        assert stored.mcp_headers == '{"Authorization": "Bearer rotated"}'

    async def test_an_explicit_null_mcp_headers_clears_them(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        """`null` is how the editor's "remove the stored headers" action says so
        deliberately — the one shape that is meant to destroy a credential.
        """
        customer_id, scope = await create_workspace("Acme")
        toolset = await create_toolset(
            scope,
            session,
            name="Websearch",
            kind="mcp",
            mcp_url="http://mcp:9000/mcp",
            mcp_headers='{"Authorization": "Bearer s3cret"}',
        )
        # Captured before `expire_all()` below: an expired attribute needs an
        # `await` to refresh, and `toolset` is a plain reference here.
        toolset_id = toolset.id
        await session.commit()
        await make_user(session, "admin@example.com", "admin", customer_id)
        await login(client, "admin@example.com")

        updated = await client.put(
            f"/api/toolsets/{toolset_id}",
            json={
                "name": "Websearch",
                "kind": "mcp",
                "mcp_url": "http://mcp:9000/mcp",
                "mcp_headers": None,
            },
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["has_mcp_headers"] is False

        session.expire_all()
        stored = await get_toolset(scope, session, toolset_id)
        assert stored is not None
        assert stored.mcp_headers is None

    async def test_an_explicit_blank_mcp_headers_clears_it(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        toolset = await create_toolset(
            scope,
            session,
            name="Websearch",
            kind="mcp",
            mcp_url="http://mcp:9000/mcp",
            mcp_headers='{"Authorization": "Bearer s3cret"}',
        )
        # Captured before `expire_all()` below: an expired attribute needs an
        # `await` to refresh, and `toolset` is a plain reference here.
        toolset_id = toolset.id
        await session.commit()
        await make_user(session, "admin@example.com", "admin", customer_id)
        await login(client, "admin@example.com")

        updated = await client.put(
            f"/api/toolsets/{toolset_id}",
            json={
                "name": "Websearch",
                "kind": "mcp",
                "mcp_url": "http://mcp:9000/mcp",
                "mcp_headers": "",
            },
        )
        assert updated.json()["has_mcp_headers"] is False

    async def test_switching_to_manual_clears_the_mcp_fields(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        toolset = await create_toolset(
            scope,
            session,
            name="Websearch",
            kind="mcp",
            mcp_url="http://mcp:9000/mcp",
            mcp_headers='{"Authorization": "Bearer s3cret"}',
        )
        # Captured before `expire_all()` below: an expired attribute needs an
        # `await` to refresh, and `toolset` is a plain reference here.
        toolset_id = toolset.id
        await session.commit()
        await make_user(session, "admin@example.com", "admin", customer_id)
        await login(client, "admin@example.com")

        updated = await client.put(
            f"/api/toolsets/{toolset_id}", json={"name": "Websearch", "kind": "manual"}
        )
        assert updated.status_code == 200, updated.text
        body = updated.json()
        assert body["mcp_url"] is None
        assert body["has_mcp_headers"] is False

        session.expire_all()
        stored = await get_toolset(scope, session, toolset_id)
        assert stored is not None
        assert stored.mcp_headers is None

    async def test_a_member_cannot_create_a_toolset(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, _ = await create_workspace("Acme")
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        response = await client.post("/api/toolsets", json={"name": "box"})
        assert response.status_code == 403

    async def test_every_signed_in_role_can_list_and_read(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        toolset = await create_toolset(scope, session, name="Odoo")
        await session.commit()
        await make_user(session, "viewer@example.com", "viewer", customer_id)
        await login(client, "viewer@example.com")

        assert (await client.get("/api/toolsets")).status_code == 200
        assert (await client.get(f"/api/toolsets/{toolset.id}")).status_code == 200

    async def test_deleting_a_toolset(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        toolset = await create_toolset(scope, session, name="Odoo")
        await session.commit()
        await make_user(session, "admin@example.com", "admin", customer_id)
        await login(client, "admin@example.com")

        deleted = await client.delete(f"/api/toolsets/{toolset.id}")
        assert deleted.status_code == 204
        assert (await client.get(f"/api/toolsets/{toolset.id}")).status_code == 404

    async def test_a_toolset_in_another_workspace_is_a_404(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        _, scope_a = await create_workspace("A")
        toolset_a = await create_toolset(scope_a, session, name="a-toolset")
        customer_b, _ = await create_workspace("B")
        await session.commit()
        await make_user(session, "admin@example.com", "admin", customer_b)
        await login(client, "admin@example.com")

        assert (await client.get(f"/api/toolsets/{toolset_a.id}")).status_code == 404
        assert (await client.delete(f"/api/toolsets/{toolset_a.id}")).status_code == 404


class TestToolCrud:
    async def test_a_writer_creates_lists_and_updates_a_manual_tool(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        toolset = await create_toolset(scope, session, name="Odoo")
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        created = await client.post(
            f"/api/toolsets/{toolset.id}/tools",
            json={
                "name": "convert_currency",
                "description": "Returns a conversion rate.",
                "mock_response": '{"rate": 1.1}',
            },
        )
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["source"] == "manual"
        assert body["enabled"] is True
        assert body["parameters_json"] == '{"type": "object", "properties": {}}'

        detail = await client.get(f"/api/toolsets/{toolset.id}")
        assert [t["name"] for t in detail.json()["tools"]] == ["convert_currency"]
        assert detail.json()["tool_count"] == 1

        updated = await client.put(
            f"/api/toolsets/{toolset.id}/tools/{body['id']}",
            json={"name": "convert_currency", "mock_response": '{"rate": 1.25}'},
        )
        assert updated.status_code == 200
        assert updated.json()["mock_response"] == '{"rate": 1.25}'

    async def test_a_duplicate_tool_name_is_refused(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        toolset = await create_toolset(scope, session, name="Odoo")
        await create_tool(scope, session, toolset.id, name="echo_upper")
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        response = await client.post(
            f"/api/toolsets/{toolset.id}/tools", json={"name": "echo_upper"}
        )
        assert response.status_code == 409
        assert "already has a tool" in response.json()["message"]

    async def test_an_invalid_tool_name_is_rejected(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        toolset = await create_toolset(scope, session, name="Odoo")
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        response = await client.post(
            f"/api/toolsets/{toolset.id}/tools", json={"name": "not a valid name!"}
        )
        assert response.status_code == 422

    async def test_invalid_parameters_json_is_rejected(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        toolset = await create_toolset(scope, session, name="Odoo")
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        response = await client.post(
            f"/api/toolsets/{toolset.id}/tools",
            json={"name": "bad_schema", "parameters_json": "not json"},
        )
        assert response.status_code == 422

    async def test_toggling_enabled(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        toolset = await create_toolset(scope, session, name="Odoo")
        tool = await create_tool(scope, session, toolset.id, name="echo_upper")
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        response = await client.put(
            f"/api/toolsets/{toolset.id}/tools/{tool.id}/enabled", json={"enabled": False}
        )
        assert response.status_code == 200
        assert response.json()["enabled"] is False

    async def test_enabled_tool_count_excludes_disabled_tools(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        """Both counts, on the list and the detail response: discovery disables
        a vanished tool rather than deleting it, so "1/2 enabled" is what the
        toolsets list and the test-case editor have to be able to say.
        """
        customer_id, scope = await create_workspace("Acme")
        toolset = await create_toolset(scope, session, name="Odoo")
        await create_tool(scope, session, toolset.id, name="echo_upper")
        disabled = await create_tool(scope, session, toolset.id, name="add_numbers")
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        before = await client.get(f"/api/toolsets/{toolset.id}")
        assert before.json()["tool_count"] == 2
        assert before.json()["enabled_tool_count"] == 2

        toggled = await client.put(
            f"/api/toolsets/{toolset.id}/tools/{disabled.id}/enabled", json={"enabled": False}
        )
        assert toggled.status_code == 200, toggled.text

        detail = await client.get(f"/api/toolsets/{toolset.id}")
        assert detail.json()["tool_count"] == 2
        assert detail.json()["enabled_tool_count"] == 1

        listed = await client.get("/api/toolsets")
        assert [(t["tool_count"], t["enabled_tool_count"]) for t in listed.json()] == [(2, 1)]

    async def test_deleting_a_tool(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        toolset = await create_toolset(scope, session, name="Odoo")
        tool = await create_tool(scope, session, toolset.id, name="echo_upper")
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        deleted = await client.delete(f"/api/toolsets/{toolset.id}/tools/{tool.id}")
        assert deleted.status_code == 204

        detail = await client.get(f"/api/toolsets/{toolset.id}")
        assert detail.json()["tools"] == []

    async def test_a_tool_from_a_different_toolset_is_a_404(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        toolset_a = await create_toolset(scope, session, name="A")
        toolset_b = await create_toolset(scope, session, name="B")
        tool = await create_tool(scope, session, toolset_a.id, name="echo_upper")
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        response = await client.put(
            f"/api/toolsets/{toolset_b.id}/tools/{tool.id}/enabled", json={"enabled": False}
        )
        assert response.status_code == 404

    async def test_a_viewer_cannot_create_a_tool(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        toolset = await create_toolset(scope, session, name="Odoo")
        await session.commit()
        await make_user(session, "viewer@example.com", "viewer", customer_id)
        await login(client, "viewer@example.com")

        response = await client.post(
            f"/api/toolsets/{toolset.id}/tools", json={"name": "echo_upper"}
        )
        assert response.status_code == 403


class TestDiscover:
    async def test_discover_syncs_tools_and_never_deletes(
        self,
        client: AsyncClient,
        session: AsyncSession,
        create_workspace: CreateWorkspace,
        monkeypatch,
    ) -> None:
        """A tool a previous discovery pass found, absent from the next one,
        must be disabled — never deleted. Seeded through the endpoint itself
        (rather than `create_tool`, which stamps `source="manual"`) so the
        pre-existing row is genuinely `source="mcp"`, the case `retired`
        actually counts.
        """
        customer_id, scope = await create_workspace("Acme")
        toolset = await create_toolset(
            scope, session, name="Websearch", kind="mcp", mcp_url="http://mcp:9000/mcp"
        )
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        async def first_pass(url, headers_json, *, timeout=60.0):
            assert url == "http://mcp:9000/mcp"
            return [
                mcp_client.McpToolDescriptor(
                    name="stale_tool", description=None, parameters_json="{}"
                )
            ]

        monkeypatch.setattr("app.api.toolsets.list_mcp_tools", first_pass)
        seeded = await client.post(f"/api/toolsets/{toolset.id}/discover")
        assert seeded.json()["discovered"] == 1

        async def second_pass(url, headers_json, *, timeout=60.0):
            return [
                mcp_client.McpToolDescriptor(
                    name="echo_upper", description="Echoes upper-case.", parameters_json="{}"
                )
            ]

        monkeypatch.setattr("app.api.toolsets.list_mcp_tools", second_pass)
        response = await client.post(f"/api/toolsets/{toolset.id}/discover")
        assert response.status_code == 200
        body = response.json()
        assert body == {
            "ok": True,
            "discovered": 1,
            "retired": 1,
            "tools": ["echo_upper"],
            "error": None,
        }

        detail = await client.get(f"/api/toolsets/{toolset.id}")
        names_enabled = {t["name"]: t["enabled"] for t in detail.json()["tools"]}
        assert names_enabled == {"echo_upper": True, "stale_tool": False}

    async def test_discover_reports_a_connection_failure_without_raising(
        self,
        client: AsyncClient,
        session: AsyncSession,
        create_workspace: CreateWorkspace,
        monkeypatch,
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        toolset = await create_toolset(
            scope, session, name="Websearch", kind="mcp", mcp_url="http://mcp:9000/mcp"
        )
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        async def fake_list_mcp_tools(url, headers_json, *, timeout=60.0):
            raise mcp_client.McpClientError("Connection refused — is the server running?")

        monkeypatch.setattr("app.api.toolsets.list_mcp_tools", fake_list_mcp_tools)

        response = await client.post(f"/api/toolsets/{toolset.id}/discover")
        assert response.status_code == 200
        assert response.json() == {
            "ok": False,
            "discovered": 0,
            "retired": 0,
            "tools": [],
            "error": "Connection refused — is the server running?",
        }

    async def test_discovering_a_manual_toolset_is_reported_not_raised(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        toolset = await create_toolset(scope, session, name="Odoo")
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        response = await client.post(f"/api/toolsets/{toolset.id}/discover")
        assert response.status_code == 200
        assert response.json()["ok"] is False

    async def test_a_viewer_cannot_discover(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        toolset = await create_toolset(
            scope, session, name="Websearch", kind="mcp", mcp_url="http://mcp:9000/mcp"
        )
        await session.commit()
        await make_user(session, "viewer@example.com", "viewer", customer_id)
        await login(client, "viewer@example.com")

        response = await client.post(f"/api/toolsets/{toolset.id}/discover")
        assert response.status_code == 403

    async def test_a_foreign_toolset_cannot_be_discovered(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        _, scope_a = await create_workspace("A")
        toolset_a = await create_toolset(
            scope_a, session, name="a-toolset", kind="mcp", mcp_url="http://mcp:9000/mcp"
        )
        customer_b, _ = await create_workspace("B")
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_b)
        await login(client, "member@example.com")

        response = await client.post(f"/api/toolsets/{toolset_a.id}/discover")
        assert response.status_code == 404
