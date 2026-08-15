"""`/api/runs` and `/api/results` end to end: real app, real Postgres.

The execute endpoint is exercised over an endpoint whose endpoint refuses
connections, which is the one way to drive the whole NDJSON path — route
guard, detached executor, event queue, persistence — without a model on the
other end. What it produces is itself an invariant worth pinning: every
attempt died at connection level, so the run ends `failed`.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable, Callable

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import tokens as token_store
from app.auth import users as user_store
from app.auth.passwords import hash_password
from app.auth.policy import Role
from app.main import app
from app.repos.endpoints import create_endpoint, update_endpoint
from app.repos.prompt_versions import commit_version
from app.repos.prompts import create_prompt, delete_prompt
from app.repos.runs import list_run_results, rate_result
from app.repos.test_cases import create_test_case, create_test_group
from app.scope import Scope
from app.services.run_create import create_run_record
from app.services.run_lock import acquire_run_lock

CreateWorkspace = Callable[[str], Awaitable[tuple[int, Scope]]]

PASSWORD = "correct horse battery staple"

#: Nothing listens on port 1, so a completion fails at connection level fast.
DEAD_ENDPOINT = "http://127.0.0.1:1/v1"


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
) -> int:
    user = await user_store.create_user(
        session, email=email, name=email, password_hash=hash_password(PASSWORD), role=role
    )
    if active_customer_id is not None:
        await user_store.set_active_customer_id(session, user.id, active_customer_id)
    await session.commit()
    return user.id


async def login(client: AsyncClient, email: str) -> None:
    response = await client.post(
        "/api/auth/login", json={"email": email, "password": PASSWORD}
    )
    assert response.status_code == 200, response.text


async def _no_probe(base_url: str, api_key: str | None, model_id: str) -> None:
    del base_url, api_key, model_id
    return None


async def make_fixture(
    scope: Scope,
    session: AsyncSession,
    *,
    titles: tuple[str, ...] = ("First",),
    base_url: str = DEAD_ENDPOINT,
) -> tuple[int, int]:
    """`(endpoint_id, group_id)` with one test case per title."""
    endpoint = await create_endpoint(scope, session, name="Box", base_url=base_url)
    group = await create_test_group(scope, session, name="Group")
    for index, title in enumerate(titles):
        await create_test_case(
            scope,
            session,
            group_id=group.id,
            title=title,
            content=f"Say {title}.",
            sort_order=index,
        )
    await session.commit()
    return endpoint.id, group.id


async def make_run(scope: Scope, session: AsyncSession, **kwargs: object) -> int:
    endpoint_id, group_id = await make_fixture(scope, session, **kwargs)  # type: ignore[arg-type]
    created = await create_run_record(
        scope,
        session,
        endpoint_id=endpoint_id,
        model_id="test-model",
        group_ids=[group_id],
        probe=_no_probe,
    )
    await session.commit()
    return created.run_id


class TestRunCrud:
    async def test_a_member_creates_and_reads_a_run(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        endpoint_id, group_id = await make_fixture(scope, session, titles=("First", "Second"))
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        created = await client.post(
            "/api/runs",
            json={
                "endpoint_id": endpoint_id,
                "model_id": "  qwen3:8b  ",
                "group_ids": [group_id],
                "params": {"temperature": 0.2},
                "comment": "baseline",
            },
        )
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["model_id"] == "qwen3:8b"
        assert body["status"] == "pending"
        assert body["params"] == {"temperature": 0.2}
        assert body["group_names"] == ["Group"]
        assert body["endpoint_snapshot"]["name"] == "Box"
        # A snapshot is display data and must never carry the key.
        assert "api_key" not in body["endpoint_snapshot"]

        detail = await client.get(f"/api/runs/{body['id']}")
        assert detail.status_code == 200
        results = detail.json()["results"]
        assert [r["test_case_title"] for r in results] == ["First", "Second"]
        assert [r["status"] for r in results] == ["pending", "pending"]
        assert results[0]["test_case_text"] == "Say First."
        # No prompts referenced: the two prompt slots read as empty rather
        # than absent from the payload.
        assert results[0]["system_prompt_text"] is None
        assert results[0]["task_prompt_text"] is None
        assert results[0]["system_prompt_version_id"] is None
        assert results[0]["task_prompt_version_id"] is None

    async def test_a_result_row_reports_all_three_frozen_texts(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        """The `RunResultView` half of the contract seam with `runs.ts`.

        The detail view renders the system prompt and the task prompt as two
        separate labelled blocks with their own version badges, so all four
        fields have to survive the wire — asserted here rather than inferred
        from a rendered string.
        """
        customer_id, scope = await create_workspace("Acme")
        endpoint = await create_endpoint(scope, session, name="Box", base_url=DEAD_ENDPOINT)
        group = await create_test_group(scope, session, name="Group")
        system_prompt = await create_prompt(
            scope, session, name="framing", content="You are terse.", kind="system"
        )
        system_version = await commit_version(scope, session, system_prompt.id, message="s1")
        task_prompt = await create_prompt(
            scope, session, name="instruction", content="Extract the PO.", kind="task"
        )
        await create_test_case(
            scope,
            session,
            group_id=group.id,
            title="First",
            content="Invoice 4711",
            system_prompt_id=system_prompt.id,
            task_prompt_id=task_prompt.id,
        )
        await session.commit()
        created = await create_run_record(
            scope,
            session,
            endpoint_id=endpoint.id,
            model_id="test-model",
            group_ids=[group.id],
            probe=_no_probe,
        )
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        detail = await client.get(f"/api/runs/{created.run_id}")
        assert detail.status_code == 200, detail.text
        [result] = detail.json()["results"]
        assert result["system_prompt_text"] == "You are terse."
        assert result["task_prompt_text"] == "Extract the PO."
        assert result["test_case_text"] == "Invoice 4711"
        assert result["system_prompt_version_id"] == system_version.id
        # Never committed, so the badge reads "dirty" rather than a version.
        assert result["task_prompt_version_id"] is None

    async def test_run_params_are_merged_over_the_endpoints_defaults(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        """The wire half of the two-level merge.

        The request carries only the *overrides*; what the run view echoes is
        the merged object that was frozen — including a default the request
        unset with a null, which must be gone rather than null.
        """
        customer_id, scope = await create_workspace("Acme")
        endpoint_id, group_id = await make_fixture(scope, session)
        await update_endpoint(
            scope,
            session,
            endpoint_id,
            {"default_params": json.dumps({"temperature": 0.2, "top_p": 0.9})},
        )
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        created = await client.post(
            "/api/runs",
            json={
                "endpoint_id": endpoint_id,
                "model_id": "qwen3:8b",
                "group_ids": [group_id],
                "params": {"temperature": 0.7, "seed": 7, "top_p": None},
            },
        )
        assert created.status_code == 201, created.text
        assert created.json()["params"] == {"temperature": 0.7, "seed": 7}

    async def test_a_param_the_run_sets_itself_is_refused(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        """A param named `messages` would not tune the request, it would
        replace it — refused by name, with the name in the message.
        """
        customer_id, scope = await create_workspace("Acme")
        endpoint_id, group_id = await make_fixture(scope, session)
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        refused = await client.post(
            "/api/runs",
            json={
                "endpoint_id": endpoint_id,
                "model_id": "qwen3:8b",
                "group_ids": [group_id],
                "params": {"messages": []},
            },
        )
        assert refused.status_code == 422, refused.text
        assert "messages" in refused.json()["message"]

    async def test_creating_a_run_with_no_groups_is_refused(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        endpoint_id, _ = await make_fixture(scope, session)
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        refused = await client.post(
            "/api/runs",
            json={"endpoint_id": endpoint_id, "model_id": "m", "group_ids": []},
        )
        assert refused.status_code == 422, refused.text

    async def test_a_case_with_no_user_message_is_refused_as_a_400(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        """The guard's sentence has to survive the HTTP seam.

        A case whose task prompt *was* its user message loses it when that
        prompt is deleted (`SET NULL`). Run creation refuses — and the refusal
        names the case to fix, so it must arrive as a 400 in the `message`
        envelope rather than as an unhandled 500.
        """
        customer_id, scope = await create_workspace("Acme")
        endpoint = await create_endpoint(scope, session, name="Box", base_url=DEAD_ENDPOINT)
        group = await create_test_group(scope, session, name="Group")
        task_prompt = await create_prompt(
            scope, session, name="instruction", content="Extract the PO.", kind="task"
        )
        await create_test_case(
            scope,
            session,
            group_id=group.id,
            title="Task only",
            task_prompt_id=task_prompt.id,
        )
        await session.commit()
        await delete_prompt(scope, session, task_prompt.id)
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        refused = await client.post(
            "/api/runs",
            json={"endpoint_id": endpoint.id, "model_id": "m", "group_ids": [group.id]},
        )
        assert refused.status_code == 400, refused.text
        assert 'Test case "Task only"' in refused.json()["message"]

    async def test_a_viewer_cannot_create_a_run(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        endpoint_id, group_id = await make_fixture(scope, session)
        await make_user(session, "viewer@example.com", "viewer", customer_id)
        await login(client, "viewer@example.com")

        refused = await client.post(
            "/api/runs",
            json={"endpoint_id": endpoint_id, "model_id": "m", "group_ids": [group_id]},
        )
        assert refused.status_code == 403

    async def test_another_workspaces_run_is_not_found(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        _, other_scope = await create_workspace("Other")
        run_id = await make_run(other_scope, session)
        mine_id, _ = await create_workspace("Mine")
        await make_user(session, "member@example.com", "member", mine_id)
        await login(client, "member@example.com")

        assert (await client.get(f"/api/runs/{run_id}")).status_code == 404
        assert (await client.delete(f"/api/runs/{run_id}")).status_code == 404

    async def test_archiving_hides_a_run_from_the_default_list(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        run_id = await make_run(scope, session)
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        archived = await client.post(f"/api/runs/{run_id}/archive")
        assert archived.status_code == 200, archived.text
        assert archived.json()["archived_at"] is not None

        assert (await client.get("/api/runs")).json() == []
        assert len((await client.get("/api/runs", params={"archived": "only"})).json()) == 1
        assert len((await client.get("/api/runs", params={"archived": "all"})).json()) == 1

        unarchived = await client.post(f"/api/runs/{run_id}/unarchive")
        assert unarchived.json()["archived_at"] is None
        assert len((await client.get("/api/runs")).json()) == 1

    async def test_deleting_a_run_takes_its_results_with_it(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        run_id = await make_run(scope, session)
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        assert (await client.delete(f"/api/runs/{run_id}")).status_code == 204
        session.expire_all()
        assert await list_run_results(scope, session, run_id) == []

    async def test_an_executing_run_refuses_archive_and_delete(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        run_id = await make_run(scope, session)
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        lock = await acquire_run_lock(run_id)
        assert lock is not None
        try:
            assert (await client.post(f"/api/runs/{run_id}/archive")).status_code == 409
            assert (await client.delete(f"/api/runs/{run_id}")).status_code == 409
        finally:
            await lock.release()


class TestExecute:
    async def test_a_run_with_nothing_pending_streams_one_line(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        run_id = await make_run(scope, session)
        results = await list_run_results(scope, session, run_id)
        results[0].status = "ok"
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        response = await client.post(f"/api/runs/{run_id}/execute")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/x-ndjson")
        assert json.loads(response.text.strip()) == {
            "type": "runDone",
            "run_id": run_id,
            "status": "pending",
            "nothing_pending": True,
        }

    async def test_a_locked_run_is_refused_before_the_stream_starts(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        run_id = await make_run(scope, session)
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        lock = await acquire_run_lock(run_id)
        assert lock is not None
        try:
            refused = await client.post(f"/api/runs/{run_id}/execute")
            assert refused.status_code == 409
            # A refusal is plain JSON, never a truncated NDJSON body.
            assert refused.json()["message"] == "This run is already executing."
        finally:
            await lock.release()

    async def test_a_viewer_is_refused_as_json(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        run_id = await make_run(scope, session)
        await make_user(session, "viewer@example.com", "viewer", customer_id)
        await login(client, "viewer@example.com")

        refused = await client.post(f"/api/runs/{run_id}/execute")
        assert refused.status_code == 403
        assert refused.headers["content-type"].startswith("application/json")

    async def test_an_unreachable_endpoint_streams_errors_and_fails_the_run(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        run_id = await make_run(scope, session, titles=("First", "Second"))
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        events: list[dict] = []
        async with client.stream("POST", f"/api/runs/{run_id}/execute") as response:
            assert response.status_code == 200
            async for line in response.aiter_lines():
                if line.strip():
                    events.append(json.loads(line))

        types = [event["type"] for event in events]
        assert types[0] == "runStart"
        assert events[0] == {
            "type": "runStart",
            "run_id": run_id,
            "pending": 2,
            "total": 2,
        }
        assert types.count("resultStart") == 2
        assert types.count("resultError") == 2
        assert events[-1]["type"] == "runDone"
        # Every attempt died at connection level: the endpoint was never there.
        assert events[-1]["status"] == "failed"

        session.expire_all()
        results = await list_run_results(scope, session, run_id)
        assert [r.status for r in results] == ["error", "error"]
        assert results[0].error


class TestRating:
    async def _finished_result(
        self, session: AsyncSession, scope: Scope, run_id: int
    ) -> int:
        results = await list_run_results(scope, session, run_id)
        results[0].status = "ok"
        results[0].response_text = "Hello."
        await session.commit()
        return results[0].id

    async def _stored_rated_via(
        self, session: AsyncSession, scope: Scope, run_id: int
    ) -> str | None:
        """The column itself, re-read past this session's identity map."""
        session.expire_all()
        return (await list_run_results(scope, session, run_id))[0].rated_via

    async def test_a_pending_result_cannot_be_rated(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        run_id = await make_run(scope, session)
        result_id = (await list_run_results(scope, session, run_id))[0].id
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        refused = await client.patch(f"/api/results/{result_id}", json={"rating": "good"})
        assert refused.status_code == 409

    async def test_rating_and_note_semantics(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        run_id = await make_run(scope, session)
        result_id = await self._finished_result(session, scope, run_id)
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        rated = await client.patch(
            f"/api/results/{result_id}", json={"rating": "meh", "note": "close"}
        )
        assert rated.status_code == 200, rated.text
        assert rated.json()["rating"] == "meh"
        assert rated.json()["rating_note"] == "close"

        # An omitted note is left alone — a rating button must never wipe one.
        again = await client.patch(f"/api/results/{result_id}", json={"rating": "good"})
        assert again.json() == {**rated.json(), "rating": "good"}

        # `unrated` is the wire word for "clear it".
        cleared = await client.patch(f"/api/results/{result_id}", json={"rating": "unrated"})
        assert cleared.json()["rating"] is None
        assert cleared.json()["rating_note"] == "close"

        # A note on its own leaves the (cleared) rating alone.
        noted = await client.patch(f"/api/results/{result_id}", json={"note": "  later  "})
        assert noted.json()["rating"] is None
        assert noted.json()["rating_note"] == "later"

    async def test_a_rating_from_the_ui_records_the_session_that_set_it(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        """`rated_via` is asserted on the **stored column**, not on the payload
        alone: it is what the judge badge in the run detail view reads, and a
        view field derived from something else would still pass.
        """
        customer_id, scope = await create_workspace("Acme")
        run_id = await make_run(scope, session)
        result_id = await self._finished_result(session, scope, run_id)
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        rated = await client.patch(f"/api/results/{result_id}", json={"rating": "good"})
        assert rated.status_code == 200, rated.text
        assert rated.json()["rated_via"] == "session"
        assert await self._stored_rated_via(session, scope, run_id) == "session"

    async def test_a_rating_from_an_api_token_records_the_token_that_set_it(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        """The same REST route, hit with an API token instead of a session
        cookie — the credential type it reads is `Actor.via`, so this is the
        route stamping `token` on its own, not `set_rating` doing it over MCP.
        """
        customer_id, scope = await create_workspace("Acme")
        run_id = await make_run(scope, session)
        result_id = await self._finished_result(session, scope, run_id)
        user_id = await make_user(session, "agent@example.com", "member", customer_id)
        _, raw = await token_store.create_token(
            session, user_id=user_id, name="judge", expires_at=None
        )
        await session.commit()

        rated = await client.patch(
            f"/api/results/{result_id}",
            json={"rating": "good"},
            headers={"x-api-key": raw},
        )
        assert rated.status_code == 200, rated.text
        assert rated.json()["rated_via"] == "token"
        assert await self._stored_rated_via(session, scope, run_id) == "token"

    async def test_a_note_only_patch_leaves_the_provenance_alone(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        """Whoever fixes a typo in the note has not re-judged the row.

        The verdict here is stamped `token` first — the state an agent's
        `set_rating` leaves behind — so a note-only PATCH restamping it as
        `session` would be visible.
        """
        customer_id, scope = await create_workspace("Acme")
        run_id = await make_run(scope, session)
        result_id = await self._finished_result(session, scope, run_id)
        await rate_result(
            scope, session, result_id, rating="good", rated_via="token"
        )
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        noted = await client.patch(f"/api/results/{result_id}", json={"note": "canary present"})
        assert noted.status_code == 200, noted.text
        assert noted.json()["rating"] == "good"
        assert noted.json()["rated_via"] == "token"
        assert await self._stored_rated_via(session, scope, run_id) == "token"

    async def test_clearing_the_rating_clears_the_provenance_with_it(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        # An unrated row has nobody to attribute, so leaving `token` behind
        # would badge a verdict that no longer exists.
        customer_id, scope = await create_workspace("Acme")
        run_id = await make_run(scope, session)
        result_id = await self._finished_result(session, scope, run_id)
        await rate_result(scope, session, result_id, rating="bad", rated_via="token")
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        cleared = await client.patch(f"/api/results/{result_id}", json={"rating": "unrated"})
        assert cleared.status_code == 200, cleared.text
        assert cleared.json()["rating"] is None
        assert cleared.json()["rated_via"] is None
        assert await self._stored_rated_via(session, scope, run_id) is None

    async def test_a_human_re_rating_takes_the_row_back_from_the_judge(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        # No separate "accept the judge's verdict" action exists: an ordinary
        # click overwrites the provenance, which is what drops the badge.
        customer_id, scope = await create_workspace("Acme")
        run_id = await make_run(scope, session)
        result_id = await self._finished_result(session, scope, run_id)
        await rate_result(
            scope, session, result_id, rating="bad", rating_note="wrong PO", write_note=True,
            rated_via="token",
        )
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        rated = await client.patch(f"/api/results/{result_id}", json={"rating": "meh"})
        assert rated.json()["rated_via"] == "session"
        # The judge's reasoning survives — only the verdict changed hands.
        assert rated.json()["rating_note"] == "wrong PO"
        assert await self._stored_rated_via(session, scope, run_id) == "session"

    async def test_an_empty_patch_is_refused(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        run_id = await make_run(scope, session)
        result_id = await self._finished_result(session, scope, run_id)
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        assert (await client.patch(f"/api/results/{result_id}", json={})).status_code == 400

    async def test_a_viewer_cannot_rate(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        run_id = await make_run(scope, session)
        result_id = await self._finished_result(session, scope, run_id)
        await make_user(session, "viewer@example.com", "viewer", customer_id)
        await login(client, "viewer@example.com")

        refused = await client.patch(f"/api/results/{result_id}", json={"rating": "good"})
        assert refused.status_code == 403

    async def test_another_workspaces_result_is_not_found(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        _, other_scope = await create_workspace("Other")
        run_id = await make_run(other_scope, session)
        result_id = await self._finished_result(session, other_scope, run_id)
        mine_id, _ = await create_workspace("Mine")
        await make_user(session, "member@example.com", "member", mine_id)
        await login(client, "member@example.com")

        refused = await client.patch(f"/api/results/{result_id}", json={"rating": "good"})
        assert refused.status_code == 404
