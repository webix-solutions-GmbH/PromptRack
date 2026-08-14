"""`/api/test-cases` end to end: real app, real Postgres, role gating,
tool-config validation (`app.services.tool_config.assert_tool_config`),
the two prompt slots, and workspace isolation.

The prompt-kinds pivot puts two new refusals on this surface, and both are
enforced from *inside* the repository functions so no route can forget them:

* the **slot guard** (`app.repos.prompts.assert_prompt_slot`) — a slot only
  accepts a prompt of its own `kind`, from this workspace. The two failures are
  deliberately different: a wrong kind is a 400 (the row is right there), a
  foreign prompt is a 404 (as far as this workspace is concerned it does not
  exist);
* the **user-message guard**
  (`app.services.message_assembly.assert_user_message`) — a case with neither a
  task prompt nor non-blank `content` sends no user message at all, checked on
  create and on the *merged* post-patch state.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable

import pytest
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

    async def test_referencing_a_prompt_in_each_slot(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        group = await create_test_group(scope, session, name="Group A")
        system_prompt = await create_prompt(
            scope, session, name="Framing", content="You are helpful.", kind="system"
        )
        task_prompt = await create_prompt(
            scope, session, name="Instruction", content="Extract the PO.", kind="task"
        )
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        created = await client.post(
            "/api/test-cases",
            json={
                "group_id": group.id,
                "title": "t",
                "content": "hi",
                "system_prompt_id": system_prompt.id,
                "task_prompt_id": task_prompt.id,
            },
        )
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["system_prompt_id"] == system_prompt.id
        assert body["system_prompt_name"] == "Framing"
        assert body["task_prompt_id"] == task_prompt.id
        assert body["task_prompt_name"] == "Instruction"

        listed = await client.get("/api/test-cases", params={"group_id": group.id})
        assert listed.json()[0]["system_prompt_name"] == "Framing"
        assert listed.json()[0]["task_prompt_name"] == "Instruction"

        got = await client.get(f"/api/test-cases/{body['id']}")
        assert got.json()["system_prompt_name"] == "Framing"
        assert got.json()["task_prompt_name"] == "Instruction"

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
        assert created.json()["system_prompt_id"] is None
        assert created.json()["system_prompt_name"] is None
        assert created.json()["task_prompt_id"] is None
        assert created.json()["task_prompt_name"] is None

    async def test_a_task_prompt_can_be_the_whole_user_message(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        """`content` is nullable now: "this prompt takes no input" has to be
        expressible, and it round-trips as `null` rather than `""`.
        """
        customer_id, scope = await create_workspace("Acme")
        group = await create_test_group(scope, session, name="Group A")
        task_prompt = await create_prompt(
            scope, session, name="Instruction", content="Summarise the tickets.", kind="task"
        )
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        created = await client.post(
            "/api/test-cases",
            json={"group_id": group.id, "title": "t", "task_prompt_id": task_prompt.id},
        )
        assert created.status_code == 201, created.text
        assert created.json()["content"] is None
        assert created.json()["task_prompt_id"] == task_prompt.id

    async def test_a_deleted_prompts_reference_reports_no_name(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        """Both slots are `SET NULL` when their prompt is deleted, so a case
        that referenced one reports a null id and a null name — never a name
        resolved from a row that no longer exists.
        """
        customer_id, scope = await create_workspace("Acme")
        group = await create_test_group(scope, session, name="Group A")
        system_prompt = await create_prompt(
            scope, session, name="Framing", content="You are helpful.", kind="system"
        )
        task_prompt = await create_prompt(
            scope, session, name="Instruction", content="Extract the PO.", kind="task"
        )
        case = await create_test_case(
            scope,
            session,
            group_id=group.id,
            title="t",
            content="hi",
            system_prompt_id=system_prompt.id,
            task_prompt_id=task_prompt.id,
        )
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        await delete_prompt(scope, session, system_prompt.id)
        await session.commit()

        got = await client.get(f"/api/test-cases/{case.id}")
        assert got.status_code == 200
        assert got.json()["system_prompt_id"] is None
        assert got.json()["system_prompt_name"] is None
        # The other slot is untouched — the two references are independent.
        assert got.json()["task_prompt_id"] == task_prompt.id
        assert got.json()["task_prompt_name"] == "Instruction"

        listed = await client.get("/api/test-cases", params={"group_id": group.id})
        assert listed.json()[0]["system_prompt_id"] is None
        assert listed.json()[0]["system_prompt_name"] is None


class TestPromptSlotGuard:
    """One prompt per kind, and the four ways of naming the wrong one."""

    async def test_a_task_prompt_in_the_system_slot_is_refused(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        group = await create_test_group(scope, session, name="Group A")
        task_prompt = await create_prompt(
            scope, session, name="Instruction", content="Extract the PO.", kind="task"
        )
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        response = await client.post(
            "/api/test-cases",
            json={
                "group_id": group.id,
                "title": "t",
                "content": "hi",
                "system_prompt_id": task_prompt.id,
            },
        )
        # 400, not 404: the prompt exists in this workspace, it is simply the
        # wrong kind — and the message has to say so.
        assert response.status_code == 400, response.text
        message = response.json()["message"]
        assert "Instruction" in message
        assert "task prompt" in message

    async def test_a_system_prompt_in_the_task_slot_is_refused(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        group = await create_test_group(scope, session, name="Group A")
        system_prompt = await create_prompt(
            scope, session, name="Framing", content="You are helpful.", kind="system"
        )
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        response = await client.post(
            "/api/test-cases",
            json={
                "group_id": group.id,
                "title": "t",
                "content": "hi",
                "task_prompt_id": system_prompt.id,
            },
        )
        assert response.status_code == 400, response.text
        assert "Framing" in response.json()["message"]

    async def test_patching_a_slot_to_the_wrong_kind_is_refused(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        group = await create_test_group(scope, session, name="Group A")
        task_prompt = await create_prompt(
            scope, session, name="Instruction", content="Extract the PO.", kind="task"
        )
        case = await create_test_case(
            scope, session, group_id=group.id, title="t", content="hi"
        )
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        response = await client.patch(
            f"/api/test-cases/{case.id}", json={"system_prompt_id": task_prompt.id}
        )
        assert response.status_code == 400, response.text

        # Nothing was written: the slot is still empty.
        got = await client.get(f"/api/test-cases/{case.id}")
        assert got.json()["system_prompt_id"] is None

    @pytest.mark.parametrize("slot", ["system_prompt_id", "task_prompt_id"])
    async def test_a_prompt_from_another_workspace_is_refused_in_either_slot(
        self,
        client: AsyncClient,
        session: AsyncSession,
        create_workspace: CreateWorkspace,
        slot: str,
    ) -> None:
        """A 404, not a 400: as far as workspace B is concerned that prompt
        does not exist, and saying anything else would leak that it does.
        """
        _, scope_a = await create_workspace("A")
        kind = "system" if slot == "system_prompt_id" else "task"
        foreign = await create_prompt(
            scope_a, session, name="Foreign", content="hi", kind=kind
        )
        customer_b, scope_b = await create_workspace("B")
        group_b = await create_test_group(scope_b, session, name="Group B")
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_b)
        await login(client, "member@example.com")

        response = await client.post(
            "/api/test-cases",
            json={"group_id": group_b.id, "title": "t", "content": "hi", slot: foreign.id},
        )
        assert response.status_code == 404, response.text

    @pytest.mark.parametrize("slot", ["system_prompt_id", "task_prompt_id"])
    async def test_patching_a_slot_to_a_foreign_prompt_is_refused(
        self,
        client: AsyncClient,
        session: AsyncSession,
        create_workspace: CreateWorkspace,
        slot: str,
    ) -> None:
        _, scope_a = await create_workspace("A")
        kind = "system" if slot == "system_prompt_id" else "task"
        foreign = await create_prompt(
            scope_a, session, name="Foreign", content="hi", kind=kind
        )
        customer_b, scope_b = await create_workspace("B")
        group_b = await create_test_group(scope_b, session, name="Group B")
        case_b = await create_test_case(
            scope_b, session, group_id=group_b.id, title="t", content="hi"
        )
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_b)
        await login(client, "member@example.com")

        response = await client.patch(
            f"/api/test-cases/{case_b.id}", json={slot: foreign.id}
        )
        assert response.status_code == 404, response.text

    async def test_clearing_a_slot_with_null_is_allowed(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        """`None` is not a reference to check — an empty slot is always valid,
        as long as something is left to send.
        """
        customer_id, scope = await create_workspace("Acme")
        group = await create_test_group(scope, session, name="Group A")
        system_prompt = await create_prompt(
            scope, session, name="Framing", content="You are helpful.", kind="system"
        )
        case = await create_test_case(
            scope,
            session,
            group_id=group.id,
            title="t",
            content="hi",
            system_prompt_id=system_prompt.id,
        )
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        patched = await client.patch(
            f"/api/test-cases/{case.id}", json={"system_prompt_id": None}
        )
        assert patched.status_code == 200, patched.text
        assert patched.json()["system_prompt_id"] is None


class TestUserMessageGuard:
    """A request with no user message measures nothing, so it is never saved."""

    async def test_creating_a_case_with_neither_content_nor_task_prompt_is_refused(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        group = await create_test_group(scope, session, name="Group A")
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        response = await client.post(
            "/api/test-cases", json={"group_id": group.id, "title": "Empty"}
        )
        assert response.status_code == 400, response.text
        assert "no user message" in response.json()["message"]
        assert "Empty" in response.json()["message"]

        listed = await client.get("/api/test-cases", params={"group_id": group.id})
        assert listed.json() == []

    async def test_a_system_prompt_alone_is_not_a_user_message(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        # The system prompt goes on the other channel entirely, so it cannot
        # stand in for the user message.
        customer_id, scope = await create_workspace("Acme")
        group = await create_test_group(scope, session, name="Group A")
        system_prompt = await create_prompt(
            scope, session, name="Framing", content="You are helpful.", kind="system"
        )
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        response = await client.post(
            "/api/test-cases",
            json={
                "group_id": group.id,
                "title": "Empty",
                "system_prompt_id": system_prompt.id,
            },
        )
        assert response.status_code == 400, response.text
        assert "no user message" in response.json()["message"]

    async def test_whitespace_only_content_counts_as_absent(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        group = await create_test_group(scope, session, name="Group A")
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        response = await client.post(
            "/api/test-cases",
            json={"group_id": group.id, "title": "Empty", "content": "   \n  "},
        )
        assert response.status_code == 400, response.text

    async def test_patching_content_away_is_refused_when_no_task_prompt_remains(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        """The guard reads the *merged* post-patch state, not the body alone."""
        customer_id, scope = await create_workspace("Acme")
        group = await create_test_group(scope, session, name="Group A")
        case = await create_test_case(
            scope, session, group_id=group.id, title="t", content="hi"
        )
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        response = await client.patch(f"/api/test-cases/{case.id}", json={"content": None})
        assert response.status_code == 400, response.text

        got = await client.get(f"/api/test-cases/{case.id}")
        assert got.json()["content"] == "hi"

    async def test_clearing_content_is_allowed_when_a_task_prompt_remains(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        group = await create_test_group(scope, session, name="Group A")
        task_prompt = await create_prompt(
            scope, session, name="Instruction", content="Summarise.", kind="task"
        )
        case = await create_test_case(
            scope,
            session,
            group_id=group.id,
            title="t",
            content="hi",
            task_prompt_id=task_prompt.id,
        )
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        patched = await client.patch(f"/api/test-cases/{case.id}", json={"content": None})
        assert patched.status_code == 200, patched.text
        assert patched.json()["content"] is None

    async def test_clearing_the_task_prompt_is_refused_when_no_content_remains(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        """The mirror image: the patch names the slot, and the *existing*
        `content` is what decides — which is why the merge has to read the row.
        """
        customer_id, scope = await create_workspace("Acme")
        group = await create_test_group(scope, session, name="Group A")
        task_prompt = await create_prompt(
            scope, session, name="Instruction", content="Summarise.", kind="task"
        )
        case = await create_test_case(
            scope, session, group_id=group.id, title="t", task_prompt_id=task_prompt.id
        )
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        response = await client.patch(
            f"/api/test-cases/{case.id}", json={"task_prompt_id": None}
        )
        assert response.status_code == 400, response.text

        got = await client.get(f"/api/test-cases/{case.id}")
        assert got.json()["task_prompt_id"] == task_prompt.id

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


class TestTheEffectivePromptRouteIsGone:
    """The preview endpoint was deleted with `mode`/`custom_text`.

    There is nothing left to resolve server-side: the editor's preview is a
    client-side concat of two texts it already fetched. The route must not
    linger — a stale client calling it has to fail loudly rather than get an
    answer computed from arguments the domain no longer has.
    """

    async def test_the_route_does_not_exist(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        prompt = await create_prompt(scope, session, name="Base", content="Be helpful.")
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        response = await client.post(
            "/api/test-cases/effective-prompt",
            json={"prompt_id": prompt.id, "mode": "append", "custom_text": "Be brief."},
        )
        # 404/405, never a 200 with a resolved prompt in it.
        assert response.status_code in (404, 405), response.text

    async def test_it_is_absent_from_the_openapi_schema(
        self, client: AsyncClient
    ) -> None:
        schema = (await client.get("/openapi.json")).json()
        assert "/api/test-cases/effective-prompt" not in schema["paths"]
