"""`/api/runs` and `/api/results` end to end: real app, real Postgres.

The execute endpoint is exercised over a machine whose endpoint refuses
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

from app.auth import users as user_store
from app.auth.passwords import hash_password
from app.auth.policy import Role
from app.main import app
from app.repos.machines import create_machine
from app.repos.runs import list_run_results
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
    """`(machine_id, group_id)` with one test case per title."""
    machine = await create_machine(scope, session, name="Box", base_url=base_url)
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
    return machine.id, group.id


async def make_run(scope: Scope, session: AsyncSession, **kwargs: object) -> int:
    machine_id, group_id = await make_fixture(scope, session, **kwargs)  # type: ignore[arg-type]
    created = await create_run_record(
        scope,
        session,
        machine_id=machine_id,
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
        machine_id, group_id = await make_fixture(scope, session, titles=("First", "Second"))
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        created = await client.post(
            "/api/runs",
            json={
                "machine_id": machine_id,
                "model_id": "  qwen3:8b  ",
                "group_ids": [group_id],
                "temperature": 0.2,
                "comment": "baseline",
            },
        )
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["model_id"] == "qwen3:8b"
        assert body["status"] == "pending"
        assert body["params"] == {"temperature": 0.2}
        assert body["group_names"] == ["Group"]
        assert body["machine_snapshot"]["name"] == "Box"
        # A snapshot is display data and must never carry the key.
        assert "api_key" not in body["machine_snapshot"]

        detail = await client.get(f"/api/runs/{body['id']}")
        assert detail.status_code == 200
        results = detail.json()["results"]
        assert [r["test_case_title"] for r in results] == ["First", "Second"]
        assert [r["status"] for r in results] == ["pending", "pending"]
        assert results[0]["test_case_text"] == "Say First."

    async def test_creating_a_run_with_no_groups_is_refused(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        machine_id, _ = await make_fixture(scope, session)
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        refused = await client.post(
            "/api/runs",
            json={"machine_id": machine_id, "model_id": "m", "group_ids": []},
        )
        assert refused.status_code == 422, refused.text

    async def test_a_viewer_cannot_create_a_run(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        machine_id, group_id = await make_fixture(scope, session)
        await make_user(session, "viewer@example.com", "viewer", customer_id)
        await login(client, "viewer@example.com")

        refused = await client.post(
            "/api/runs",
            json={"machine_id": machine_id, "model_id": "m", "group_ids": [group_id]},
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
        # Every attempt died at connection level: the machine was never there.
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
