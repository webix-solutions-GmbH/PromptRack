"""`/api/test-cases` end to end: real app, real Postgres, role gating,
tool-config validation (`app.services.tool_config.assert_tool_config`) and
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
from app.repos.prompts import create_prompt, delete_prompt
from app.repos.test_cases import create_test_case, create_test_group, replace_toolset_links
from app.repos.toolsets import create_tool, create_toolset, set_tool_enabled
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


class TestTestCaseCrud:
    async def test_a_member_creates_lists_and_reads_a_test_case(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        group = await create_test_group(scope, session, name="Group A")
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        created = await client.post(
            "/api/test-cases",
            json={
                "group_id": group.id,
                "title": "Reconcile invoice",
                "content": "Reconcile this invoice.",
                "expected_output": "ASK",
            },
        )
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["tool_mode"] == "none"
        assert body["max_turns"] == 6
        assert body["toolset_ids"] == []

        listed = await client.get("/api/test-cases", params={"group_id": group.id})
        assert [c["title"] for c in listed.json()] == ["Reconcile invoice"]

        got = await client.get(f"/api/test-cases/{body['id']}")
        assert got.status_code == 200
        assert got.json()["expected_output"] == "ASK"

    async def test_max_turns_is_clamped(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        group = await create_test_group(scope, session, name="Group A")
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        created = await client.post(
            "/api/test-cases",
            json={
                "group_id": group.id,
                "title": "t",
                "content": "hi",
                "max_turns": 1000,
            },
        )
        assert created.status_code == 201
        assert created.json()["max_turns"] == 20

    async def test_referencing_a_prompt(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        group = await create_test_group(scope, session, name="Group A")
        prompt = await create_prompt(scope, session, name="Base", content="You are helpful.")
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        created = await client.post(
            "/api/test-cases",
            json={
                "group_id": group.id,
                "title": "t",
                "content": "hi",
                "prompt_id": prompt.id,
                "mode": "override",
                "custom_text": "Be brief.",
            },
        )
        assert created.status_code == 201, created.text
        assert created.json()["prompt_id"] == prompt.id
        assert created.json()["prompt_name"] == "Base"
        assert created.json()["mode"] == "override"

        listed = await client.get("/api/test-cases", params={"group_id": group.id})
        assert listed.json()[0]["prompt_name"] == "Base"

        got = await client.get(f"/api/test-cases/{created.json()['id']}")
        assert got.json()["prompt_name"] == "Base"

    async def test_a_test_case_with_no_prompt_reports_no_name(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        group = await create_test_group(scope, session, name="Group A")
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        created = await client.post(
            "/api/test-cases", json={"group_id": group.id, "title": "t", "content": "hi"}
        )
        assert created.status_code == 201, created.text
        assert created.json()["prompt_id"] is None
        assert created.json()["prompt_name"] is None

    async def test_a_deleted_prompts_reference_reports_no_name(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        """`test_cases.prompt_id` is `SET NULL` when its prompt is deleted, so
        a case that referenced it reports both a null id and a null name —
        never a name resolved from a row that no longer exists.
        """
        customer_id, scope = await create_workspace("Acme")
        group = await create_test_group(scope, session, name="Group A")
        prompt = await create_prompt(scope, session, name="Base", content="You are helpful.")
        case = await create_test_case(
            scope, session, group_id=group.id, title="t", content="hi", prompt_id=prompt.id
        )
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        await delete_prompt(scope, session, prompt.id)
        await session.commit()

        got = await client.get(f"/api/test-cases/{case.id}")
        assert got.status_code == 200
        assert got.json()["prompt_id"] is None
        assert got.json()["prompt_name"] is None

        listed = await client.get("/api/test-cases", params={"group_id": group.id})
        assert listed.json()[0]["prompt_id"] is None
        assert listed.json()[0]["prompt_name"] is None

    async def test_creating_with_a_foreign_group_is_refused(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        _, scope_a = await create_workspace("A")
        group_a = await create_test_group(scope_a, session, name="a-group")
        customer_b, _ = await create_workspace("B")
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_b)
        await login(client, "member@example.com")

        response = await client.post(
            "/api/test-cases", json={"group_id": group_a.id, "title": "t", "content": "hi"}
        )
        assert response.status_code == 404

    async def test_patching_a_test_case(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        group = await create_test_group(scope, session, name="Group A")
        case = await create_test_case(
            scope, session, group_id=group.id, title="Original", content="hi"
        )
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        patched = await client.patch(
            f"/api/test-cases/{case.id}", json={"title": "Renamed"}
        )
        assert patched.status_code == 200, patched.text
        assert patched.json()["title"] == "Renamed"
        # untouched fields survive the patch
        assert patched.json()["content"] == "hi"

    async def test_deleting_a_test_case(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        group = await create_test_group(scope, session, name="Group A")
        case = await create_test_case(scope, session, group_id=group.id, title="t", content="hi")
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        deleted = await client.delete(f"/api/test-cases/{case.id}")
        assert deleted.status_code == 204
        assert (await client.get(f"/api/test-cases/{case.id}")).status_code == 404

    async def test_a_viewer_cannot_create_a_test_case(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        group = await create_test_group(scope, session, name="Group A")
        await session.commit()
        await make_user(session, "viewer@example.com", "viewer", customer_id)
        await login(client, "viewer@example.com")

        response = await client.post(
            "/api/test-cases", json={"group_id": group.id, "title": "t", "content": "hi"}
        )
        assert response.status_code == 403

    async def test_a_test_case_in_another_workspace_is_a_404(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        _, scope_a = await create_workspace("A")
        group_a = await create_test_group(scope_a, session, name="a-group")
        case_a = await create_test_case(
            scope_a, session, group_id=group_a.id, title="t", content="hi"
        )
        customer_b, _ = await create_workspace("B")
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_b)
        await login(client, "member@example.com")

        assert (await client.get(f"/api/test-cases/{case_a.id}")).status_code == 404
        assert (await client.delete(f"/api/test-cases/{case_a.id}")).status_code == 404


class TestToolsetLinksAndConfig:
    async def test_creating_a_tool_test_links_its_toolsets(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        group = await create_test_group(scope, session, name="Group A")
        toolset = await create_toolset(scope, session, name="Odoo")
        await create_tool(scope, session, toolset.id, name="convert_currency")
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        created = await client.post(
            "/api/test-cases",
            json={
                "group_id": group.id,
                "title": "t",
                "content": "hi",
                "tool_mode": "execute",
                "toolset_ids": [toolset.id],
            },
        )
        assert created.status_code == 201, created.text
        assert created.json()["toolset_ids"] == [toolset.id]
        assert created.json()["tool_mode"] == "execute"

    async def test_a_tool_mode_with_no_toolsets_is_refused(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        group = await create_test_group(scope, session, name="Group A")
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        response = await client.post(
            "/api/test-cases",
            json={"group_id": group.id, "title": "t", "content": "hi", "tool_mode": "execute"},
        )
        assert response.status_code == 400
        assert "no enabled tools" in response.json()["message"]

    async def test_a_toolset_with_no_enabled_tools_is_refused(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        group = await create_test_group(scope, session, name="Group A")
        toolset = await create_toolset(scope, session, name="Odoo")
        tool = await create_tool(scope, session, toolset.id, name="convert_currency")
        await set_tool_enabled(scope, session, tool.id, False)
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        response = await client.post(
            "/api/test-cases",
            json={
                "group_id": group.id,
                "title": "t",
                "content": "hi",
                "tool_mode": "definitions",
                "toolset_ids": [toolset.id],
            },
        )
        assert response.status_code == 400
        assert "no enabled tools" in response.json()["message"]

    async def test_colliding_tool_names_across_toolsets_are_refused(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        group = await create_test_group(scope, session, name="Group A")
        toolset_a = await create_toolset(scope, session, name="A")
        toolset_b = await create_toolset(scope, session, name="B")
        await create_tool(scope, session, toolset_a.id, name="search")
        await create_tool(scope, session, toolset_b.id, name="search")
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        response = await client.post(
            "/api/test-cases",
            json={
                "group_id": group.id,
                "title": "t",
                "content": "hi",
                "tool_mode": "definitions",
                "toolset_ids": [toolset_a.id, toolset_b.id],
            },
        )
        assert response.status_code == 400
        assert "both define: search" in response.json()["message"]

    async def test_a_foreign_toolset_id_is_refused(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        _, scope_a = await create_workspace("A")
        foreign_toolset = await create_toolset(scope_a, session, name="foreign")
        customer_b, scope_b = await create_workspace("B")
        group_b = await create_test_group(scope_b, session, name="Group B")
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_b)
        await login(client, "member@example.com")

        response = await client.post(
            "/api/test-cases",
            json={
                "group_id": group_b.id,
                "title": "t",
                "content": "hi",
                "tool_mode": "definitions",
                "toolset_ids": [foreign_toolset.id],
            },
        )
        assert response.status_code == 404

    async def test_patch_re_checks_tool_config_after_switching_mode(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        """Switching `tool_mode` without naming `toolset_ids` re-validates
        against the *existing* links, not against an empty set.
        """
        customer_id, scope = await create_workspace("Acme")
        group = await create_test_group(scope, session, name="Group A")
        case = await create_test_case(
            scope, session, group_id=group.id, title="t", content="hi", tool_mode="none"
        )
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        # No toolsets linked at all: switching to "execute" must be refused.
        response = await client.patch(
            f"/api/test-cases/{case.id}", json={"tool_mode": "execute"}
        )
        assert response.status_code == 400
        assert "no enabled tools" in response.json()["message"]

    async def test_patch_keeps_existing_links_when_toolset_ids_is_absent(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        group = await create_test_group(scope, session, name="Group A")
        toolset = await create_toolset(scope, session, name="Odoo")
        await create_tool(scope, session, toolset.id, name="convert_currency")
        case = await create_test_case(
            scope, session, group_id=group.id, title="t", content="hi", tool_mode="definitions"
        )
        await replace_toolset_links(scope, session, case.id, [toolset.id])
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        patched = await client.patch(
            f"/api/test-cases/{case.id}", json={"title": "Renamed"}
        )
        assert patched.status_code == 200, patched.text
        assert patched.json()["toolset_ids"] == [toolset.id]

    async def test_patch_can_clear_toolset_links_with_an_empty_list(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        group = await create_test_group(scope, session, name="Group A")
        toolset = await create_toolset(scope, session, name="Odoo")
        await create_tool(scope, session, toolset.id, name="convert_currency")
        case = await create_test_case(
            scope, session, group_id=group.id, title="t", content="hi", tool_mode="none"
        )
        await replace_toolset_links(scope, session, case.id, [toolset.id])
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        patched = await client.patch(
            f"/api/test-cases/{case.id}", json={"toolset_ids": []}
        )
        assert patched.status_code == 200, patched.text
        assert patched.json()["toolset_ids"] == []


class TestEffectivePromptPreview:
    async def test_append_mode_joins_base_and_custom(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        prompt = await create_prompt(scope, session, name="Base", content="Be helpful.")
        await session.commit()
        await make_user(session, "viewer@example.com", "viewer", customer_id)
        await login(client, "viewer@example.com")

        response = await client.post(
            "/api/test-cases/effective-prompt",
            json={"prompt_id": prompt.id, "mode": "append", "custom_text": "Be brief."},
        )
        assert response.status_code == 200, response.text
        assert response.json()["content"] == "Be helpful.\n\nBe brief."

    async def test_override_mode_ignores_the_base(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        prompt = await create_prompt(scope, session, name="Base", content="Be helpful.")
        await session.commit()
        await make_user(session, "viewer@example.com", "viewer", customer_id)
        await login(client, "viewer@example.com")

        response = await client.post(
            "/api/test-cases/effective-prompt",
            json={"prompt_id": prompt.id, "mode": "override", "custom_text": "Only this."},
        )
        assert response.json()["content"] == "Only this."

    async def test_no_prompt_id_uses_custom_text_alone(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, _ = await create_workspace("Acme")
        await session.commit()
        await make_user(session, "viewer@example.com", "viewer", customer_id)
        await login(client, "viewer@example.com")

        response = await client.post(
            "/api/test-cases/effective-prompt",
            json={"mode": "append", "custom_text": "Solo text."},
        )
        assert response.json()["content"] == "Solo text."

    async def test_a_foreign_prompt_id_is_a_404(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        _, scope_a = await create_workspace("A")
        prompt_a = await create_prompt(scope_a, session, name="Base", content="hi")
        customer_b, _ = await create_workspace("B")
        await session.commit()
        await make_user(session, "viewer@example.com", "viewer", customer_b)
        await login(client, "viewer@example.com")

        response = await client.post(
            "/api/test-cases/effective-prompt",
            json={"prompt_id": prompt_a.id, "mode": "append", "custom_text": None},
        )
        assert response.status_code == 404
