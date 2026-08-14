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
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import users as user_store
from app.auth.passwords import hash_password
from app.auth.policy import Role
from app.main import app
from app.repos.machines import create_machine
from app.repos.prompt_versions import commit_version
from app.repos.prompts import create_prompt
from app.repos.runs import create_run as create_run_row
from app.repos.runs import insert_run_results
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
        machine_id: int,
        *,
        prompt_version_id: int | None,
    ) -> int:
        run = await create_run_row(
            scope,
            session,
            machine_id=machine_id,
            machine_snapshot="{}",
            model_id="qwen3-32b",
            group_names="[]",
        )
        await insert_run_results(
            scope,
            session,
            run.id,
            [
                {
                    "prompt_version_id": prompt_version_id,
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
        machine = await create_machine(scope, session, name="box", base_url="http://x/v1")
        run_id = await self._run_with_attributed_result(
            session, scope, machine.id, prompt_version_id=version.id
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
        machine = await create_machine(scope, session, name="box", base_url="http://x/v1")
        run_id = await self._run_with_attributed_result(
            session, scope, machine.id, prompt_version_id=None
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
        machine_b = await create_machine(scope_b, session, name="box", base_url="http://x/v1")
        run_b_id = await self._run_with_attributed_result(
            session, scope_b, machine_b.id, prompt_version_id=None
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
        machine = await create_machine(scope, session, name="box", base_url="http://x/v1")
        run_id = await self._run_with_attributed_result(
            session, scope, machine.id, prompt_version_id=version.id
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
