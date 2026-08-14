"""`/api/prompts` end to end: real app, real Postgres, role gating.

The pure rules behind `dirty`/attribution are `tests/test_attribution.py`'s
job, and the repository-level invariants (cross-workspace refusal, the
`NoChangesError`/`NotAttributedError` refusals, cascade/`SET NULL` on delete)
are `tests/integration/test_versioning.py`'s. What this file covers is what
only the wired-up route can show: prompts hold no credentials so every
mutation sits at `Writer` (never `Admin`), a foreign prompt/version is a 404
rather than someone else's row, and the JSON shape of each response.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import users as user_store
from app.auth.passwords import hash_password
from app.auth.policy import Role
from app.main import app
from app.models import User
from app.repos.endpoints import create_endpoint
from app.repos.prompt_versions import commit_version
from app.repos.prompts import create_prompt
from app.repos.runs import create_run as create_run_row
from app.repos.runs import insert_run_results
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


class TestPromptCrud:
    async def test_a_member_creates_lists_and_reads_a_prompt(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, _ = await create_workspace("Acme")
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        created = await client.post(
            "/api/prompts", json={"name": "Greeting", "content": "Say hi."}
        )
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["name"] == "Greeting"
        assert body["content"] == "Say hi."
        # Nothing committed yet: dirty and headless.
        assert body["dirty"] is True
        assert body["head_version"] is None
        assert body["deployed_version"] is None

        listed = await client.get("/api/prompts")
        assert [p["name"] for p in listed.json()] == ["Greeting"]

        got = await client.get(f"/api/prompts/{body['id']}")
        assert got.status_code == 200
        assert got.json()["content"] == "Say hi."

    async def test_a_blank_name_is_rejected(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, _ = await create_workspace("Acme")
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        assert (
            await client.post("/api/prompts", json={"name": "  ", "content": "x"})
        ).status_code == 422

    async def test_an_empty_draft_is_accepted(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        """The asset can exist before its text is written — only the name
        identifies it. Blank, whitespace-only and omitted all mean the same
        empty draft, and the prompt is still dirty (nothing is committed), so
        the first commit stays available.
        """
        customer_id, _ = await create_workspace("Acme")
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        blank = await client.post("/api/prompts", json={"name": "Blank", "content": "  "})
        assert blank.status_code == 201, blank.text
        assert blank.json()["content"] == ""
        assert blank.json()["dirty"] is True

        omitted = await client.post("/api/prompts", json={"name": "Omitted"})
        assert omitted.status_code == 201, omitted.text
        assert omitted.json()["content"] == ""

        # And it can be filled in and committed like any other draft.
        patched = await client.patch(
            f"/api/prompts/{omitted.json()['id']}", json={"content": "Say hi."}
        )
        assert patched.json()["content"] == "Say hi."
        committed = await client.post(
            f"/api/prompts/{omitted.json()['id']}/commit", json={"message": "first"}
        )
        assert committed.status_code == 201, committed.text

    async def test_a_viewer_cannot_create_a_prompt(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, _ = await create_workspace("Acme")
        await session.commit()
        await make_user(session, "viewer@example.com", "viewer", customer_id)
        await login(client, "viewer@example.com")

        response = await client.post(
            "/api/prompts", json={"name": "Greeting", "content": "Say hi."}
        )
        assert response.status_code == 403

    async def test_every_signed_in_role_can_list_and_read(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        prompt = await create_prompt(scope, session, name="Greeting", content="Say hi.")
        await session.commit()
        await make_user(session, "viewer@example.com", "viewer", customer_id)
        await login(client, "viewer@example.com")

        assert (await client.get("/api/prompts")).status_code == 200
        assert (await client.get(f"/api/prompts/{prompt.id}")).status_code == 200

    async def test_patch_edits_only_the_field_present(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        prompt = await create_prompt(scope, session, name="Greeting", content="Say hi.")
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        patched = await client.patch(
            f"/api/prompts/{prompt.id}", json={"content": "Say hello."}
        )
        assert patched.status_code == 200, patched.text
        body = patched.json()
        assert body["name"] == "Greeting"
        assert body["content"] == "Say hello."

    async def test_patch_can_clear_the_draft_but_not_the_name(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        """An editor that can start empty has to be able to get back there, so a
        blank `content` clears the draft. The name is the asset's identity and
        stays required.
        """
        customer_id, scope = await create_workspace("Acme")
        prompt = await create_prompt(scope, session, name="Greeting", content="Say hi.")
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        cleared = await client.patch(f"/api/prompts/{prompt.id}", json={"content": "   "})
        assert cleared.status_code == 200, cleared.text
        assert cleared.json()["content"] == ""

        assert (
            await client.patch(f"/api/prompts/{prompt.id}", json={"name": "  "})
        ).status_code == 422

    async def test_deleting_a_prompt(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        prompt = await create_prompt(scope, session, name="Greeting", content="Say hi.")
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        deleted = await client.delete(f"/api/prompts/{prompt.id}")
        assert deleted.status_code == 204
        assert (await client.get(f"/api/prompts/{prompt.id}")).status_code == 404

    async def test_a_prompt_in_another_workspace_is_a_404(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        _, scope_a = await create_workspace("A")
        prompt_a = await create_prompt(scope_a, session, name="a-prompt", content="x")
        customer_b, _ = await create_workspace("B")
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_b)
        await login(client, "member@example.com")

        assert (await client.get(f"/api/prompts/{prompt_a.id}")).status_code == 404
        assert (await client.delete(f"/api/prompts/{prompt_a.id}")).status_code == 404


class TestPromptKind:
    """`kind` is a property of the asset — which channel every version of it is
    sent on. It defaults to `system`, it is reported on every read, and it can
    only change while nothing references the prompt.
    """

    async def test_a_new_prompt_defaults_to_the_system_channel(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, _ = await create_workspace("Acme")
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        created = await client.post(
            "/api/prompts", json={"name": "Greeting", "content": "Say hi."}
        )
        assert created.status_code == 201, created.text
        # Everything authored before the pivot went out as a system message;
        # defaulting anywhere else would move text between channels.
        assert created.json()["kind"] == "system"
        assert created.json()["used_by_test_case_count"] == 0

    async def test_a_task_prompt_can_be_created_and_read_back(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, _ = await create_workspace("Acme")
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        created = await client.post(
            "/api/prompts",
            json={"name": "Judge", "content": "Pick the PO.", "kind": "task"},
        )
        assert created.status_code == 201, created.text
        assert created.json()["kind"] == "task"

        assert (await client.get(f"/api/prompts/{created.json()['id']}")).json()["kind"] == (
            "task"
        )
        assert (await client.get("/api/prompts")).json()[0]["kind"] == "task"

    async def test_an_unrecognised_kind_is_refused_never_coerced(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, _ = await create_workspace("Acme")
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        response = await client.post(
            "/api/prompts", json={"name": "x", "content": "y", "kind": "user"}
        )
        assert response.status_code == 422, response.text

    async def test_kind_changes_freely_while_nothing_references_the_prompt(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        prompt = await create_prompt(
            scope, session, name="Greeting", content="Say hi.", kind="system"
        )
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        patched = await client.patch(f"/api/prompts/{prompt.id}", json={"kind": "task"})
        assert patched.status_code == 200, patched.text
        assert patched.json()["kind"] == "task"
        assert patched.json()["used_by_test_case_count"] == 0

    async def test_kind_change_is_refused_while_a_test_case_references_it(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        """409, and **nothing is written** — the refusal fires before the
        UPDATE, so a client must not read it as "the draft failed to save".

        The alternative would silently relocate this prompt's text from the
        system message to the head of the user message for every case that
        uses it: exactly the invisible wire-format change the pivot exists to
        eliminate.
        """
        customer_id, scope = await create_workspace("Acme")
        prompt = await create_prompt(
            scope, session, name="Greeting", content="Say hi.", kind="system"
        )
        group = await create_test_group(scope, session, name="Group A")
        await create_test_case(
            scope,
            session,
            group_id=group.id,
            title="t",
            content="hi",
            system_prompt_id=prompt.id,
        )
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        response = await client.patch(f"/api/prompts/{prompt.id}", json={"kind": "task"})
        assert response.status_code == 409, response.text
        message = response.json()["message"]
        assert "Greeting" in message
        assert "1 test case" in message

        got = await client.get(f"/api/prompts/{prompt.id}")
        assert got.json()["kind"] == "system"
        assert got.json()["used_by_test_case_count"] == 1

    async def test_the_refusal_counts_the_task_slot_too(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        # Both slots are references; a prompt is not free to move just because
        # the cases that use it happen to use the other one.
        customer_id, scope = await create_workspace("Acme")
        prompt = await create_prompt(
            scope, session, name="Judge", content="Pick the PO.", kind="task"
        )
        group = await create_test_group(scope, session, name="Group A")
        for index in range(2):
            await create_test_case(
                scope,
                session,
                group_id=group.id,
                title=f"case {index}",
                content="hi",
                task_prompt_id=prompt.id,
            )
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        response = await client.patch(f"/api/prompts/{prompt.id}", json={"kind": "system"})
        assert response.status_code == 409, response.text
        assert "2 test cases" in response.json()["message"]

        assert (await client.get("/api/prompts")).json()[0]["used_by_test_case_count"] == 2

    async def test_patching_kind_to_its_current_value_is_a_no_op_not_a_refusal(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        # Only a real *change* is refused; a form that always sends every field
        # must not be blocked from saving the draft.
        customer_id, scope = await create_workspace("Acme")
        prompt = await create_prompt(
            scope, session, name="Greeting", content="Say hi.", kind="system"
        )
        group = await create_test_group(scope, session, name="Group A")
        await create_test_case(
            scope,
            session,
            group_id=group.id,
            title="t",
            content="hi",
            system_prompt_id=prompt.id,
        )
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        patched = await client.patch(
            f"/api/prompts/{prompt.id}", json={"kind": "system", "content": "Say hello."}
        )
        assert patched.status_code == 200, patched.text
        assert patched.json()["content"] == "Say hello."
        assert patched.json()["kind"] == "system"

    async def test_a_referenced_prompts_draft_still_saves(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        # The refusal is about the channel, never about the text: editing and
        # committing a referenced prompt is the normal workflow.
        customer_id, scope = await create_workspace("Acme")
        prompt = await create_prompt(
            scope, session, name="Greeting", content="Say hi.", kind="system"
        )
        group = await create_test_group(scope, session, name="Group A")
        await create_test_case(
            scope,
            session,
            group_id=group.id,
            title="t",
            content="hi",
            system_prompt_id=prompt.id,
        )
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        patched = await client.patch(
            f"/api/prompts/{prompt.id}", json={"content": "Say hello."}
        )
        assert patched.status_code == 200, patched.text
        assert patched.json()["content"] == "Say hello."


class TestVersioning:
    async def test_commit_creates_a_version_and_updates_head(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        prompt = await create_prompt(scope, session, name="Greeting", content="Say hi.")
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        committed = await client.post(
            f"/api/prompts/{prompt.id}/commit", json={"message": "Initial version"}
        )
        assert committed.status_code == 201, committed.text
        version_body = committed.json()
        assert version_body["version"] == 1
        assert version_body["content"] == "Say hi."
        assert version_body["message"] == "Initial version"

        refreshed = await client.get(f"/api/prompts/{prompt.id}")
        body = refreshed.json()
        assert body["dirty"] is False
        assert body["head_version"] == {"id": version_body["id"], "version": 1}

    async def test_committed_version_reports_the_authors_name(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        """`created_by_name` is resolved server-side (`app.auth.users.list_display_names`)
        so the history panel never renders a bare user id — checked on the
        commit response itself, the list, and the single-version read.
        """
        customer_id, scope = await create_workspace("Acme")
        prompt = await create_prompt(scope, session, name="Greeting", content="Say hi.")
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        committed = await client.post(
            f"/api/prompts/{prompt.id}/commit", json={"message": "v1"}
        )
        assert committed.status_code == 201, committed.text
        assert committed.json()["created_by_name"] == "member@example.com"

        listed = await client.get(f"/api/prompts/{prompt.id}/versions")
        assert listed.json()[0]["created_by_name"] == "member@example.com"

        got = await client.get(f"/api/prompts/versions/{committed.json()['id']}")
        assert got.json()["created_by_name"] == "member@example.com"

    async def test_a_deleted_authors_version_reports_no_name(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        """`prompt_versions.created_by` is `SET NULL` when the author's account
        is deleted, so both the raw id and the resolved name read null —
        never a name looked up from a user row that no longer exists.
        """
        customer_id, scope = await create_workspace("Acme")
        prompt = await create_prompt(scope, session, name="Greeting", content="Say hi.")
        await session.commit()
        author_id = await make_user(session, "gone@example.com", "member", customer_id)
        await login(client, "gone@example.com")

        committed = await client.post(
            f"/api/prompts/{prompt.id}/commit", json={"message": "v1"}
        )
        assert committed.status_code == 201, committed.text
        version_id = committed.json()["id"]

        await session.execute(delete(User).where(User.id == author_id))
        await session.commit()

        await make_user(session, "viewer@example.com", "viewer", customer_id)
        await login(client, "viewer@example.com")

        got = await client.get(f"/api/prompts/versions/{version_id}")
        assert got.status_code == 200
        assert got.json()["created_by"] is None
        assert got.json()["created_by_name"] is None

    async def test_committing_with_no_changes_is_refused(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        prompt = await create_prompt(scope, session, name="Greeting", content="Say hi.")
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        first = await client.post(
            f"/api/prompts/{prompt.id}/commit", json={"message": "v1"}
        )
        assert first.status_code == 201

        second = await client.post(
            f"/api/prompts/{prompt.id}/commit", json={"message": "v2 (no-op)"}
        )
        assert second.status_code == 409
        assert "nothing to commit" in second.json()["message"]

    async def test_editing_the_draft_makes_it_dirty_again(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        prompt = await create_prompt(scope, session, name="Greeting", content="Say hi.")
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        await client.post(f"/api/prompts/{prompt.id}/commit", json={"message": "v1"})
        await client.patch(f"/api/prompts/{prompt.id}", json={"content": "Say hello!"})

        body = (await client.get(f"/api/prompts/{prompt.id}")).json()
        assert body["dirty"] is True
        assert body["head_version"]["version"] == 1

    async def test_list_and_get_version(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        prompt = await create_prompt(scope, session, name="Greeting", content="A")
        v1 = await commit_version(scope, session, prompt.id, message="v1")
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        listed = await client.get(f"/api/prompts/{prompt.id}/versions")
        assert listed.status_code == 200
        assert [v["version"] for v in listed.json()] == [1]

        got = await client.get(f"/api/prompts/versions/{v1.id}")
        assert got.status_code == 200
        assert got.json()["message"] == "v1"

    async def test_a_version_from_another_workspace_is_a_404(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        _, scope_a = await create_workspace("A")
        prompt_a = await create_prompt(scope_a, session, name="Greeting", content="A")
        version_a = await commit_version(scope_a, session, prompt_a.id, message="v1")
        customer_b, _ = await create_workspace("B")
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_b)
        await login(client, "member@example.com")

        assert (
            await client.get(f"/api/prompts/versions/{version_a.id}")
        ).status_code == 404

    async def test_deploy_sets_the_pointer(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        prompt = await create_prompt(scope, session, name="Greeting", content="A")
        v1 = await commit_version(scope, session, prompt.id, message="v1")
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        deployed = await client.post(
            f"/api/prompts/{prompt.id}/deploy", json={"version_id": v1.id}
        )
        assert deployed.status_code == 200, deployed.text
        body = deployed.json()
        assert body["deployed_version"] == {"id": v1.id, "version": 1}
        assert body["deployed_at"] is not None
        assert body["deployed_by_name"] == "member@example.com"

    async def test_deploy_refuses_a_version_belonging_to_a_different_prompt(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        prompt_1 = await create_prompt(scope, session, name="One", content="A")
        prompt_2 = await create_prompt(scope, session, name="Two", content="B")
        version_2 = await commit_version(scope, session, prompt_2.id, message="v1")
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        response = await client.post(
            f"/api/prompts/{prompt_1.id}/deploy", json={"version_id": version_2.id}
        )
        assert response.status_code == 400

    async def test_deploy_refuses_a_version_from_another_workspace(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        _, scope_a = await create_workspace("A")
        prompt_a = await create_prompt(scope_a, session, name="Greeting", content="A")
        customer_b, scope_b = await create_workspace("B")
        prompt_b = await create_prompt(scope_b, session, name="Greeting", content="A")
        version_b = await commit_version(scope_b, session, prompt_b.id, message="v1")
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_b)
        await login(client, "member@example.com")

        response = await client.post(
            f"/api/prompts/{prompt_a.id}/deploy", json={"version_id": version_b.id}
        )
        assert response.status_code == 404

    async def test_restore_copies_a_versions_text_into_the_draft(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        prompt = await create_prompt(scope, session, name="Greeting", content="A")
        v1 = await commit_version(scope, session, prompt.id, message="v1")
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        await client.patch(f"/api/prompts/{prompt.id}", json={"content": "B"})

        restored = await client.post(
            f"/api/prompts/{prompt.id}/restore", json={"version_id": v1.id}
        )
        assert restored.status_code == 200, restored.text
        body = restored.json()
        assert body["content"] == "A"
        # Restoring does not itself commit — the draft is dirty against v1
        # again only because it now differs... except it's identical, so:
        assert body["dirty"] is False

    async def test_restore_refuses_a_version_from_a_different_prompt(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        prompt_1 = await create_prompt(scope, session, name="One", content="A")
        prompt_2 = await create_prompt(scope, session, name="Two", content="B")
        version_2 = await commit_version(scope, session, prompt_2.id, message="v1")
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        response = await client.post(
            f"/api/prompts/{prompt_1.id}/restore", json={"version_id": version_2.id}
        )
        assert response.status_code == 404


class TestBaseline:
    async def _run_with_attributed_result(
        self,
        session: AsyncSession,
        scope: Scope,
        endpoint_id: int,
        *,
        prompt_version_id: int | None,
        slot: str = "system_prompt_version_id",
    ) -> int:
        """`slot` names which of the two version columns carries the id; the
        other stays null. `set_baseline` accepts either, and
        `tests/integration/test_versioning.py` is where that is pinned per
        column — here it only has to be nameable.
        """
        assert slot in ("system_prompt_version_id", "task_prompt_version_id")
        run = await create_run_row(
            scope,
            session,
            endpoint_id=endpoint_id,
            endpoint_snapshot="{}",
            model_id="qwen3-32b",
            group_names="[]",
        )
        await insert_run_results(
            scope,
            session,
            run.id,
            [
                {
                    "system_prompt_version_id": None,
                    "task_prompt_version_id": None,
                    slot: prompt_version_id,
                    "group_name": "General",
                    "test_case_title": "Hello",
                    "test_case_text": "Say hi.",
                    "status": "ok",
                }
            ],
        )
        return run.id

    async def test_set_baseline_when_the_run_tested_this_version(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        prompt = await create_prompt(scope, session, name="Greeting", content="A")
        version = await commit_version(scope, session, prompt.id, message="v1")
        endpoint = await create_endpoint(scope, session, name="box", base_url="http://x/v1")
        run_id = await self._run_with_attributed_result(
            session, scope, endpoint.id, prompt_version_id=version.id
        )
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        response = await client.post(
            f"/api/prompts/versions/{version.id}/baseline", json={"run_id": run_id}
        )
        assert response.status_code == 200, response.text
        assert response.json()["baseline_run_id"] == run_id

    async def test_set_baseline_refuses_a_run_that_never_tested_this_version(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        prompt = await create_prompt(scope, session, name="Greeting", content="A")
        version = await commit_version(scope, session, prompt.id, message="v1")
        endpoint = await create_endpoint(scope, session, name="box", base_url="http://x/v1")
        run_id = await self._run_with_attributed_result(
            session, scope, endpoint.id, prompt_version_id=None
        )
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        response = await client.post(
            f"/api/prompts/versions/{version.id}/baseline", json={"run_id": run_id}
        )
        assert response.status_code == 409

    async def test_set_baseline_refuses_a_run_from_another_workspace(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        _, scope_a = await create_workspace("A")
        prompt_a = await create_prompt(scope_a, session, name="Greeting", content="A")
        version_a = await commit_version(scope_a, session, prompt_a.id, message="v1")
        customer_b, scope_b = await create_workspace("B")
        endpoint_b = await create_endpoint(scope_b, session, name="box", base_url="http://x/v1")
        run_b_id = await self._run_with_attributed_result(
            session, scope_b, endpoint_b.id, prompt_version_id=None
        )
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_b)
        await login(client, "member@example.com")

        response = await client.post(
            f"/api/prompts/versions/{version_a.id}/baseline", json={"run_id": run_b_id}
        )
        assert response.status_code == 404

    async def test_a_viewer_cannot_set_a_baseline(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        prompt = await create_prompt(scope, session, name="Greeting", content="A")
        version = await commit_version(scope, session, prompt.id, message="v1")
        endpoint = await create_endpoint(scope, session, name="box", base_url="http://x/v1")
        run_id = await self._run_with_attributed_result(
            session, scope, endpoint.id, prompt_version_id=version.id
        )
        await session.commit()
        await make_user(session, "viewer@example.com", "viewer", customer_id)
        await login(client, "viewer@example.com")

        response = await client.post(
            f"/api/prompts/versions/{version.id}/baseline", json={"run_id": run_id}
        )
        assert response.status_code == 403


class TestDiff:
    async def test_diff_between_draft_and_a_version(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        prompt = await create_prompt(
            scope, session, name="Greeting", content="You are helpful.\nBe concise.\n"
        )
        v1 = await commit_version(scope, session, prompt.id, message="v1")
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        patched = await client.patch(
            f"/api/prompts/{prompt.id}",
            json={"content": "You are helpful.\nBe thorough.\n"},
        )
        assert patched.status_code == 200

        diff = await client.get(
            f"/api/prompts/{prompt.id}/diff", params={"from": str(v1.id), "to": "draft"}
        )
        assert diff.status_code == 200, diff.text
        body = diff.json()
        assert body["from_label"] == "v1"
        assert body["to_label"] == "draft"
        assert "-Be concise." in body["diff"]
        assert "+Be thorough." in body["diff"]

    async def test_diff_rejects_an_invalid_reference(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        prompt = await create_prompt(scope, session, name="Greeting", content="A")
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        response = await client.get(
            f"/api/prompts/{prompt.id}/diff", params={"from": "not-a-ref", "to": "draft"}
        )
        assert response.status_code == 422

    async def test_diff_404s_on_a_version_from_a_different_prompt(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        prompt_1 = await create_prompt(scope, session, name="One", content="A")
        prompt_2 = await create_prompt(scope, session, name="Two", content="B")
        version_2 = await commit_version(scope, session, prompt_2.id, message="v1")
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        response = await client.get(
            f"/api/prompts/{prompt_1.id}/diff",
            params={"from": "draft", "to": str(version_2.id)},
        )
        assert response.status_code == 404
