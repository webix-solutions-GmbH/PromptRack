"""`/api/results/matrix` end to end: real app, real Postgres.

The pure suite (`tests/test_compare.py`) already pins the pivoting itself, so
what is left for a database is what the pure functions cannot see: that the two
reads feed them the right rows, that the pickers agree with the matrix, and
that neither pivot can reach another workspace's runs.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import users as user_store
from app.auth.passwords import hash_password
from app.main import app
from app.repos.machines import create_machine
from app.repos.runs import list_run_results, update_run_result
from app.repos.test_cases import create_test_case, create_test_group
from app.scope import Scope
from app.services.run_create import create_run_record

CreateWorkspace = Callable[[str], Awaitable[tuple[int, Scope]]]

PASSWORD = "correct horse battery staple"


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as http:
        yield http


async def sign_in(
    client: AsyncClient, session: AsyncSession, email: str, customer_id: int
) -> None:
    user = await user_store.create_user(
        session, email=email, name=email, password_hash=hash_password(PASSWORD), role="member"
    )
    await user_store.set_active_customer_id(session, user.id, customer_id)
    await session.commit()
    response = await client.post(
        "/api/auth/login", json={"email": email, "password": PASSWORD}
    )
    assert response.status_code == 200, response.text


async def _no_probe(base_url: str, api_key: str | None, model_id: str) -> None:
    del base_url, api_key, model_id
    return None


async def make_suite(scope: Scope, session: AsyncSession) -> tuple[int, int]:
    """`(machine_id, group_id)` with two test cases in run order."""
    machine = await create_machine(scope, session, name="Box", base_url="http://box/v1")
    group = await create_test_group(scope, session, name="Group")
    for index, title in enumerate(("First", "Second")):
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


async def make_run(
    scope: Scope,
    session: AsyncSession,
    machine_id: int,
    group_id: int,
    *,
    model_id: str = "test-model",
    outcomes: tuple[str, ...] = ("ok", "ok"),
) -> int:
    """A run whose rows are finished — what a comparable run looks like."""
    created = await create_run_record(
        scope,
        session,
        machine_id=machine_id,
        model_id=model_id,
        group_ids=[group_id],
        probe=_no_probe,
    )
    await session.commit()

    rows = await list_run_results(scope, session, created.run_id)
    for row, outcome in zip(rows, outcomes, strict=False):
        await update_run_result(
            scope,
            session,
            created.run_id,
            row.id,
            {
                "status": outcome,
                "response_text": None if outcome == "error" else f"answer {row.id}",
                "error": "connection refused" if outcome == "error" else None,
                "rating": "good" if outcome == "ok" else None,
                "tokens_per_sec": 20.0 if outcome == "ok" else None,
            },
        )
    await session.commit()
    return created.run_id


async def matrix(client: AsyncClient, **params: object) -> dict:
    response = await client.get("/api/results/matrix", params=params)
    assert response.status_code == 200, response.text
    return response.json()


class TestRunMode:
    async def test_two_runs_line_up_by_test_case(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        machine_id, group_id = await make_suite(scope, session)
        first = await make_run(scope, session, machine_id, group_id)
        second = await make_run(scope, session, machine_id, group_id)
        await sign_in(client, session, "member@example.com", customer_id)

        body = await matrix(client, mode="runs", runs=f"{first},{second}")

        assert body["mode"] == "runs"
        assert body["selected_run_ids"] == [first, second]
        assert [row["test_case_title"] for row in body["rows"]] == ["First", "Second"]
        assert all(cell is not None for row in body["rows"] for cell in row["cells"])
        # Same suite, same machine, same params: nothing drifted.
        assert [row["drift"] for row in body["rows"]] == [[], []]
        # The picker carries the tallies its column headers show.
        assert {run["id"] for run in body["available_runs"]} == {first, second}
        assert body["available_runs"][0]["ok"] == 2
        assert body["available_runs"][0]["group_names"] == ["Group"]

    async def test_a_runs_link_without_a_mode_keeps_its_pivot(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        machine_id, group_id = await make_suite(scope, session)
        first = await make_run(scope, session, machine_id, group_id)
        second = await make_run(scope, session, machine_id, group_id)
        await sign_in(client, session, "member@example.com", customer_id)

        assert (await matrix(client, runs=f"{first},{second}"))["mode"] == "runs"
        # …while the default pivot, with nothing selected, is by model.
        assert (await matrix(client))["mode"] == "models"

    async def test_below_the_minimum_selection_there_is_no_matrix(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        machine_id, group_id = await make_suite(scope, session)
        only = await make_run(scope, session, machine_id, group_id)
        await sign_in(client, session, "member@example.com", customer_id)

        body = await matrix(client, mode="runs", runs=str(only))
        assert body["min_columns"] == 2
        assert body["rows"] == []

    async def test_an_archived_run_is_hidden_unless_already_selected(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        machine_id, group_id = await make_suite(scope, session)
        first = await make_run(scope, session, machine_id, group_id)
        second = await make_run(scope, session, machine_id, group_id)
        await sign_in(client, session, "member@example.com", customer_id)
        assert (await client.post(f"/api/runs/{second}/archive")).status_code == 200

        hidden = await matrix(client, mode="runs")
        assert [run["id"] for run in hidden["available_runs"]] == [first]
        assert hidden["hidden_archived_runs"] == 1

        # A bookmarked comparison keeps working, and stays deselectable.
        kept = await matrix(client, mode="runs", runs=f"{first},{second}")
        assert kept["selected_run_ids"] == [first, second]
        assert kept["hidden_archived_runs"] == 0

    async def test_another_workspaces_run_is_dropped_from_the_selection(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        _, other_scope = await create_workspace("Other")
        other_machine, other_group = await make_suite(other_scope, session)
        foreign = await make_run(other_scope, session, other_machine, other_group)

        customer_id, scope = await create_workspace("Acme")
        machine_id, group_id = await make_suite(scope, session)
        mine = await make_run(scope, session, machine_id, group_id)
        await sign_in(client, session, "member@example.com", customer_id)

        body = await matrix(client, mode="runs", runs=f"{mine},{foreign}")
        assert body["selected_run_ids"] == [mine]
        assert body["rows"] == []
        assert [run["id"] for run in body["available_runs"]] == [mine]


class TestModelMode:
    async def test_a_column_shows_the_newest_usable_result(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        machine_id, group_id = await make_suite(scope, session)
        old = await make_run(scope, session, machine_id, group_id)
        # The newer run answered the first case and died on the second.
        new = await make_run(
            scope, session, machine_id, group_id, outcomes=("ok", "error")
        )
        await sign_in(client, session, "member@example.com", customer_id)

        key = f"{machine_id}|test-model"
        available = await matrix(client)
        assert [column["key"] for column in available["available_models"]] == [key]
        assert available["available_models"][0]["run_count"] == 2
        assert available["available_models"][0]["test_case_count"] == 2

        body = await matrix(client, models=[key])
        assert body["selected_model_keys"] == [key]
        first, second = body["rows"]
        assert first["cells"][0]["run_id"] == new
        assert first["cells"][0]["superseded"] is None
        # The failed newer attempt must not blank a good older answer — but it
        # is reported rather than silently skipped.
        assert second["cells"][0]["run_id"] == old
        assert second["cells"][0]["superseded"]["run_id"] == new
        assert second["cells"][0]["superseded"]["status"] == "error"
        assert body["column_tallies"][0] == {
            "answered": 2,
            "good": 2,
            "meh": 0,
            "bad": 0,
            "unrated": 0,
            "avg_rate": 20.0,
        }

    async def test_an_archived_run_cannot_be_asked_back(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        machine_id, group_id = await make_suite(scope, session)
        only = await make_run(scope, session, machine_id, group_id)
        await sign_in(client, session, "member@example.com", customer_id)
        assert (await client.post(f"/api/runs/{only}/archive")).status_code == 200

        body = await matrix(client, models=[f"{machine_id}|test-model"])
        assert body["available_models"] == []
        assert body["selected_model_keys"] == []

    async def test_the_group_filter_narrows_the_rows(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        machine_id, group_id = await make_suite(scope, session)
        other = await create_test_group(scope, session, name="Other")
        await create_test_case(
            scope, session, group_id=other.id, title="Third", content="Say Third."
        )
        await session.commit()
        await make_run(scope, session, machine_id, group_id)
        await sign_in(client, session, "member@example.com", customer_id)

        key = f"{machine_id}|test-model"
        unfiltered = await matrix(client, models=[key])
        assert {group["name"] for group in unfiltered["groups"]} == {"Group", "Other"}
        # "Third" was never run, so it is counted rather than drawn empty.
        assert len(unfiltered["rows"]) == 2
        assert unfiltered["uncovered_test_cases"] == 1

        filtered = await matrix(client, models=[key], group=str(other.id))
        assert filtered["selected_group_ids"] == [other.id]
        assert filtered["rows"] == []
        assert filtered["uncovered_test_cases"] == 1

    async def test_the_matrix_route_is_not_shadowed_by_a_result_id(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        # `/api/results/{result_id}` would otherwise match "matrix" and 422.
        customer_id, _ = await create_workspace("Acme")
        await sign_in(client, session, "member@example.com", customer_id)

        response = await client.get("/api/results/matrix")
        assert response.status_code == 200
        assert (await client.get("/api/results/9999")).status_code == 404

    async def test_signing_in_is_required(self, client: AsyncClient) -> None:
        assert (await client.get("/api/results/matrix")).status_code == 401
