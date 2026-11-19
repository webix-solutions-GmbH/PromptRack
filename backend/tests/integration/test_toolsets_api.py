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
from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import users as user_store
from app.auth.passwords import hash_password
from app.auth.policy import Role
from app.main import app
from app.models import Customer
from app.repos.customers import create_customer
from app.repos.test_cases import create_test_case, create_test_group, replace_toolset_links
from app.repos.toolsets import create_tool, create_toolset, get_toolset
from app.scope import Scope, scope_from_row
from app.services import mcp_client

CreateWorkspace = Callable[[str], Awaitable[tuple[int, Scope]]]

PASSWORD = "correct horse battery staple"


async def _make_base(session: AsyncSession) -> tuple[int, Scope]:
    """A Base workspace, flagged the way the migration flags it — see
    `test_workspaces._make_base`, which this mirrors.
    """
    customer = await create_customer(session, name="Base")
    await session.execute(
        update(Customer).where(Customer.id == customer.id).values(is_base=True)
    )
    return customer.id, scope_from_row(customer.id)


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

    async def test_a_member_creates_and_edits_a_manual_toolset(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        """A manual toolset holds no credentials, so it is content."""
        customer_id, _ = await create_workspace("Acme")
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        created = await client.post(
            "/api/toolsets", json={"name": "box", "description": "mocks", "kind": "manual"}
        )
        assert created.status_code == 201, created.text
        toolset_id = created.json()["id"]

        updated = await client.put(
            f"/api/toolsets/{toolset_id}",
            json={"name": "box v2", "description": None, "kind": "manual"},
        )
        assert updated.status_code == 200, updated.text
        assert (updated.json()["name"], updated.json()["description"]) == ("box v2", None)

    async def test_a_member_is_refused_everything_that_touches_a_credential(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        manual = await create_toolset(scope, session, name="Mocks")
        mcp = await create_toolset(
            scope, session, name="Odoo", kind="mcp", mcp_url="http://mcp:9000/mcp"
        )
        manual_id, mcp_id = manual.id, mcp.id
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        refused_creates = [
            {"name": "x", "kind": "mcp", "mcp_url": "http://mcp:9000/mcp"},
            {"name": "x", "kind": "documents"},
            {"name": "x", "is_global": True},
            {"name": "x", "mcp_headers": '{"Authorization": "Bearer s3cret"}'},
            {"name": "x", "mcp_url": None},
        ]
        for body in refused_creates:
            response = await client.post("/api/toolsets", json=body)
            assert response.status_code == 403, body
            assert "administrator" in response.json()["message"]

        refused_updates = [
            (manual_id, {"name": "Mocks", "kind": "mcp", "mcp_url": "http://mcp:9000/mcp"}),
            (manual_id, {"name": "Mocks", "kind": "documents"}),
            (manual_id, {"name": "Mocks", "is_global": False}),
            (manual_id, {"name": "Mocks", "mcp_headers": None}),
            # Renaming an MCP toolset is still an edit of a credential-holding row.
            (mcp_id, {"name": "Odoo v2", "kind": "mcp", "mcp_url": "http://mcp:9000/mcp"}),
            (mcp_id, {"name": "Odoo v2", "kind": "manual"}),
        ]
        for toolset_id, body in refused_updates:
            response = await client.put(f"/api/toolsets/{toolset_id}", json=body)
            assert response.status_code == 403, body

        assert (await client.delete(f"/api/toolsets/{manual_id}")).status_code == 403

        session.expire_all()
        stored = await get_toolset(scope, session, mcp_id)
        assert stored is not None and (stored.name, stored.kind) == ("Odoo", "mcp")

    async def test_a_viewer_cannot_create_a_toolset(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, _ = await create_workspace("Acme")
        await session.commit()
        await make_user(session, "viewer@example.com", "viewer", customer_id)
        await login(client, "viewer@example.com")

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

    async def test_a_name_another_visible_toolset_holds_is_refused(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        """Case-insensitive over visible rows — the same rule MCP resolves names
        by — on create and on a rename; an unchanged name is never re-checked.
        """
        customer_id, scope = await create_workspace("Acme")
        await create_toolset(scope, session, name="Odoo")
        other = await create_toolset(scope, session, name="Other")
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        created = await client.post("/api/toolsets", json={"name": "ODOO"})
        assert created.status_code == 409, created.text
        assert 'A toolset named "Odoo" already exists' in created.json()["message"]

        renamed = await client.put(f"/api/toolsets/{other.id}", json={"name": "odoo"})
        assert renamed.status_code == 409, renamed.text
        assert (await get_toolset(scope, session, other.id)).name == "Other"

        # A duplicate that already exists stays saveable under its own name.
        await session.execute(
            text("UPDATE toolsets SET name = 'Odoo' WHERE id = :id"), {"id": other.id}
        )
        await session.commit()
        resaved = await client.put(
            f"/api/toolsets/{other.id}", json={"name": "Odoo", "description": "kept"}
        )
        assert resaved.status_code == 200, resaved.text

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
                "mock_responses": ['{"rate": 1.1}'],
            },
        )
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["source"] == "manual"
        assert body["enabled"] is True
        assert body["parameters_json"] == '{"type": "object", "properties": {}}'
        assert body["mock_responses"] == ['{"rate": 1.1}']

        detail = await client.get(f"/api/toolsets/{toolset.id}")
        assert [t["name"] for t in detail.json()["tools"]] == ["convert_currency"]
        assert detail.json()["tool_count"] == 1

        updated = await client.put(
            f"/api/toolsets/{toolset.id}/tools/{body['id']}",
            json={"name": "convert_currency", "mock_responses": ['{"rate": 1.25}']},
        )
        assert updated.status_code == 200
        assert updated.json()["mock_responses"] == ['{"rate": 1.25}']

        # Omitted (or []) clears them: a PUT is a full replace.
        cleared = await client.put(
            f"/api/toolsets/{toolset.id}/tools/{body['id']}", json={"name": "convert_currency"}
        )
        assert cleared.json()["mock_responses"] == []

    async def test_mock_responses_round_trip_in_call_order(
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
                "name": "poll_status",
                "mock_responses": [' {"status": "pending"} ', '{"status": "done"}'],
            },
        )
        assert created.status_code == 201, created.text
        assert created.json()["mock_responses"] == ['{"status": "pending"}', '{"status": "done"}']

        detail = (await client.get(f"/api/toolsets/{toolset.id}")).json()
        assert detail["tools"][0]["mock_responses"] == [
            '{"status": "pending"}',
            '{"status": "done"}',
        ]

        blank_entry = await client.post(
            f"/api/toolsets/{toolset.id}/tools",
            json={"name": "other", "mock_responses": ["one", "  "]},
        )
        assert blank_entry.status_code == 422
        too_many = await client.post(
            f"/api/toolsets/{toolset.id}/tools",
            json={"name": "other", "mock_responses": ["x"] * 21},
        )
        assert too_many.status_code == 422

    async def test_long_descriptions_and_mock_responses_round_trip(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        """A real customer tool: a ~60k multi-line description and canned answer."""
        customer_id, scope = await create_workspace("Acme")
        toolset = await create_toolset(scope, session, name="SIMPOS")
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        description = ("Looks a record up.\n" * 3200)[:60_000].strip()
        answer = '{"rows": [' + ",".join(['"' + "x" * 50 + '"'] * 1150) + "]}"
        assert len(answer) > 58_000

        created = await client.post(
            f"/api/toolsets/{toolset.id}/tools",
            json={"name": "lookup", "description": description, "mock_responses": [answer]},
        )
        assert created.status_code == 201, created.text
        detail = (await client.get(f"/api/toolsets/{toolset.id}")).json()
        assert detail["tools"][0]["description"] == description
        assert detail["tools"][0]["mock_responses"] == [answer]

        too_long = await client.post(
            f"/api/toolsets/{toolset.id}/tools",
            json={"name": "huge", "description": "x" * 200_001},
        )
        assert too_long.status_code == 422
        assert "200,000" in too_long.json()["message"]

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

    async def test_a_tool_test_cases_override_cannot_be_renamed_disabled_or_deleted(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        """A global toolset overridden from another workspace: the refusal has to
        count cases the writer cannot see, or the write strands them silently.
        """
        base_id, base = await _make_base(session)
        _, acme = await create_workspace("Acme")
        shared = await create_toolset(base, session, name="Shared mocks", is_global=True)
        tool = await create_tool(
            base, session, shared.id, name="fetch_page", mock_responses=["page 1"]
        )
        group = await create_test_group(acme, session, name="Paging")
        for title in ("One", "Two"):
            case = await create_test_case(
                acme,
                session,
                group_id=group.id,
                title=title,
                content="x",
                tool_mode="execute",
                mock_overrides={"fetch_page": ["only page"]},
            )
            await replace_toolset_links(acme, session, case.id, [shared.id])
        await session.commit()
        await make_user(session, "admin@example.com", "admin", base_id)
        await login(client, "admin@example.com")

        tool_url = f"/api/toolsets/{shared.id}/tools/{tool.id}"
        for response in (
            await client.put(tool_url, json={"name": "get_page", "mock_responses": ["page 1"]}),
            await client.put(f"{tool_url}/enabled", json={"enabled": False}),
            await client.delete(tool_url),
        ):
            assert response.status_code == 409, response.text
            message = response.json()["message"]
            assert '2 test cases override the canned responses of "fetch_page"' in message
            assert "(2 in Acme)" in message
            assert "Remove those overrides" in message

        # Editing the tool without renaming it stays allowed.
        edited = await client.put(
            tool_url, json={"name": "fetch_page", "mock_responses": ["page 1", "page 2"]}
        )
        assert edited.status_code == 200, edited.text
        detail = await client.get(f"/api/toolsets/{shared.id}")
        assert [(t["name"], t["enabled"]) for t in detail.json()["tools"]] == [
            ("fetch_page", True)
        ]

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
