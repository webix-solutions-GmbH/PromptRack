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

import json
from collections.abc import AsyncIterator, Awaitable, Callable

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import users as user_store
from app.auth.passwords import hash_password
from app.auth.policy import Role
from app.main import app
from app.models import TestCase as CaseModel
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


HISTORY = [
    {"role": "user", "content": "List open invoices."},
    {"role": "assistant", "content": '{"invoices": [4711, 4712]}'},
]


async def _stored(session: AsyncSession, test_case_id: int) -> tuple[str | None, str | None]:
    """The two JSON columns as stored — asserted directly, not via the view."""
    row = (
        await session.execute(
            select(CaseModel.history, CaseModel.mock_overrides).where(
                CaseModel.id == test_case_id
            )
        )
    ).one()
    return row[0], row[1]


class TestConversationHistory:
    async def test_round_trips_and_is_stored_as_a_json_array(
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
                "title": "Follow-up",
                "content": "Only Germany.",
                "history": HISTORY,
            },
        )
        assert created.status_code == 201, created.text
        assert created.json()["history"] == HISTORY
        assert created.json()["mock_overrides"] == {}

        history, overrides = await _stored(session, created.json()["id"])
        assert json.loads(history or "null") == HISTORY
        assert overrides is None

        fetched = await client.get(f"/api/test-cases/{created.json()['id']}")
        assert fetched.json()["history"] == HISTORY

    async def test_a_case_without_history_reads_back_an_empty_list_and_stores_null(
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
        assert created.json()["history"] == []
        assert await _stored(session, created.json()["id"]) == (None, None)

    @pytest.mark.parametrize(
        ("history", "fragment"),
        [
            ([{"role": "user", "content": "only a question"}], 'end with an "assistant"'),
            (
                [{"role": "assistant", "content": "a"}, {"role": "user", "content": "b"}],
                'must be "user"',
            ),
            (
                [{"role": "user", "content": "a"}, {"role": "assistant", "content": "  "}],
                "has no content",
            ),
        ],
    )
    async def test_an_unsendable_history_is_a_400_naming_the_case(
        self,
        client: AsyncClient,
        session: AsyncSession,
        create_workspace: CreateWorkspace,
        history: list[dict[str, str]],
        fragment: str,
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        group = await create_test_group(scope, session, name="Group A")
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        response = await client.post(
            "/api/test-cases",
            json={"group_id": group.id, "title": "Follow-up", "content": "x", "history": history},
        )
        assert response.status_code == 400, response.text
        assert response.json()["message"].startswith('Test case "Follow-up": ')
        assert fragment in response.json()["message"]

    async def test_a_system_role_is_refused_by_the_wire_shape(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        group = await create_test_group(scope, session, name="Group A")
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        response = await client.post(
            "/api/test-cases",
            json={
                "group_id": group.id,
                "title": "t",
                "content": "x",
                "history": [
                    {"role": "system", "content": "a"},
                    {"role": "assistant", "content": "b"},
                ],
            },
        )
        assert response.status_code == 422
        assert "message" in response.json()

    async def test_patch_is_presence_based_and_null_or_empty_clears(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        group = await create_test_group(scope, session, name="Group A")
        case = await create_test_case(
            scope, session, group_id=group.id, title="t", content="hi", history=HISTORY
        )
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        untouched = await client.patch(f"/api/test-cases/{case.id}", json={"title": "Renamed"})
        assert untouched.status_code == 200, untouched.text
        assert untouched.json()["history"] == HISTORY

        refused = await client.patch(
            f"/api/test-cases/{case.id}", json={"history": HISTORY[:1]}
        )
        assert refused.status_code == 400
        assert refused.json()["message"].startswith('Test case "Renamed": ')
        assert (await _stored(session, case.id))[0] is not None

        for clearing in (None, []):
            await client.patch(f"/api/test-cases/{case.id}", json={"history": HISTORY})
            cleared = await client.patch(
                f"/api/test-cases/{case.id}", json={"history": clearing}
            )
            assert cleared.status_code == 200, cleared.text
            assert cleared.json()["history"] == []
            assert (await _stored(session, case.id))[0] is None


class TestMockOverrides:
    async def _setup(
        self, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> tuple[int, Scope, int, int]:
        customer_id, scope = await create_workspace("Acme")
        group = await create_test_group(scope, session, name="Group A")
        toolset = await create_toolset(scope, session, name="Odoo")
        await create_tool(scope, session, toolset.id, name="lookup", mock_responses=["shared"])
        disabled = await create_tool(scope, session, toolset.id, name="archived")
        await set_tool_enabled(scope, session, disabled.id, False)
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        return customer_id, scope, group.id, toolset.id

    async def test_round_trips_and_a_string_is_not_accepted_where_a_list_is_required(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        _, _, group_id, toolset_id = await self._setup(session, create_workspace)
        await login(client, "member@example.com")

        created = await client.post(
            "/api/test-cases",
            json={
                "group_id": group_id,
                "title": "t",
                "content": "hi",
                "tool_mode": "execute",
                "toolset_ids": [toolset_id],
                "mock_overrides": {"lookup": ['{"status": "pending"}', ' {"status": "done"} ']},
            },
        )
        assert created.status_code == 201, created.text
        expected = {"lookup": ['{"status": "pending"}', '{"status": "done"}']}
        assert created.json()["mock_overrides"] == expected
        stored = (await _stored(session, created.json()["id"]))[1]
        assert json.loads(stored or "null") == expected

    async def test_an_override_of_a_tool_that_is_not_an_enabled_manual_tool_is_refused(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        _, _, group_id, toolset_id = await self._setup(session, create_workspace)
        await login(client, "member@example.com")

        for name in ("lookpu", "archived"):
            response = await client.post(
                "/api/test-cases",
                json={
                    "group_id": group_id,
                    "title": "Case X",
                    "content": "hi",
                    "tool_mode": "execute",
                    "toolset_ids": [toolset_id],
                    "mock_overrides": {name: ["x"]},
                },
            )
            assert response.status_code == 400, response.text
            message = response.json()["message"]
            assert message.startswith(
                f'Test case "Case X" overrides the canned response of "{name}"'
            )
            assert "overridden: lookup." in message

    async def test_a_documents_tool_cannot_be_overridden(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        group = await create_test_group(scope, session, name="Group A")
        corpus = await create_toolset(scope, session, name="Docs", kind="documents")
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        response = await client.post(
            "/api/test-cases",
            json={
                "group_id": group.id,
                "title": "t",
                "content": "hi",
                "tool_mode": "execute",
                "toolset_ids": [corpus.id],
                "mock_overrides": {"search_documents": ["x"]},
            },
        )
        assert response.status_code == 400
        assert "overridden: none" in response.json()["message"]

    async def test_a_blank_override_is_a_422(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        _, _, group_id, toolset_id = await self._setup(session, create_workspace)
        await login(client, "member@example.com")

        response = await client.post(
            "/api/test-cases",
            json={
                "group_id": group_id,
                "title": "t",
                "content": "hi",
                "tool_mode": "execute",
                "toolset_ids": [toolset_id],
                "mock_overrides": {"lookup": ["  "]},
            },
        )
        assert response.status_code == 422
        assert "no canned response" in response.json()["message"]

    async def test_overrides_are_kept_but_not_checked_under_tool_mode_none(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        _, _, group_id, _ = await self._setup(session, create_workspace)
        await login(client, "member@example.com")

        created = await client.post(
            "/api/test-cases",
            json={
                "group_id": group_id,
                "title": "t",
                "content": "hi",
                "mock_overrides": {"no_such_tool": ["x"]},
            },
        )
        assert created.status_code == 201, created.text
        assert created.json()["mock_overrides"] == {"no_such_tool": ["x"]}

    async def test_patch_is_presence_based_and_re_checked_post_patch(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        _, scope, group_id, toolset_id = await self._setup(session, create_workspace)
        case = await create_test_case(
            scope,
            session,
            group_id=group_id,
            title="t",
            content="hi",
            tool_mode="execute",
            mock_overrides={"lookup": ["mine"]},
        )
        await replace_toolset_links(scope, session, case.id, [toolset_id])
        await session.commit()
        await login(client, "member@example.com")

        untouched = await client.patch(f"/api/test-cases/{case.id}", json={"title": "Renamed"})
        assert untouched.status_code == 200, untouched.text
        assert untouched.json()["mock_overrides"] == {"lookup": ["mine"]}

        # Dropping the toolset that holds the overridden tool, without clearing
        # the override, is refused against the post-patch configuration.
        other = await create_toolset(scope, session, name="Other")
        await create_tool(scope, session, other.id, name="search")
        await session.commit()
        refused = await client.patch(
            f"/api/test-cases/{case.id}", json={"toolset_ids": [other.id]}
        )
        assert refused.status_code == 400
        assert 'overrides the canned response of "lookup"' in refused.json()["message"]

        for clearing in (None, {}):
            cleared = await client.patch(
                f"/api/test-cases/{case.id}", json={"mock_overrides": clearing}
            )
            assert cleared.status_code == 200, cleared.text
            assert cleared.json()["mock_overrides"] == {}
            assert (await _stored(session, case.id))[1] is None
            await client.patch(
                f"/api/test-cases/{case.id}", json={"mock_overrides": {"lookup": ["again"]}}
            )

    async def test_a_stale_override_only_blocks_a_patch_of_the_tool_configuration(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        """Names are re-validated only when the patch touches the overrides, the
        toolsets or the tool mode — a title or a rubric edit must not be held
        hostage by an override gone stale underneath the case.
        """
        _, scope, group_id, toolset_id = await self._setup(session, create_workspace)
        case = await create_test_case(
            scope,
            session,
            group_id=group_id,
            title="t",
            content="hi",
            tool_mode="execute",
            mock_overrides={"lookup": ["mine"]},
        )
        await replace_toolset_links(scope, session, case.id, [toolset_id])
        # Written underneath the guard that refuses this rename through the API.
        await session.execute(
            update(CaseModel)
            .where(CaseModel.id == case.id)
            .values(mock_overrides=json.dumps({"gone": ["x"]}))
        )
        await session.commit()
        await login(client, "member@example.com")

        for unrelated in ({"title": "Renamed"}, {"expected_output": "A rubric."}):
            response = await client.patch(f"/api/test-cases/{case.id}", json=unrelated)
            assert response.status_code == 200, response.text

        for touching in (
            {"tool_mode": "definitions"},
            {"toolset_ids": [toolset_id]},
            {"mock_overrides": {"gone": ["y"]}},
        ):
            response = await client.patch(f"/api/test-cases/{case.id}", json=touching)
            assert response.status_code == 400, (touching, response.text)
            assert 'overrides the canned response of "gone"' in response.json()["message"]


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
