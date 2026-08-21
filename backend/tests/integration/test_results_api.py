"""`/api/results/matrix` end to end: real app, real Postgres.

The pure suite (`tests/test_compare.py`) already pins the pivoting itself, so
what is left for a database is what the pure functions cannot see: that the two
reads feed them the right rows, that the pickers agree with the matrix, and
that neither pivot can reach another workspace's runs.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable, Callable

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import users as user_store
from app.auth.passwords import hash_password
from app.main import app
from app.repos.endpoints import create_endpoint
from app.repos.prompt_versions import commit_version
from app.repos.prompts import create_prompt, update_prompt
from app.repos.runs import list_run_results, update_run_result
from app.repos.test_cases import (
    create_test_case,
    create_test_group,
    list_test_cases,
    update_test_case,
)
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
    """`(endpoint_id, group_id)` with two test cases in run order."""
    endpoint = await create_endpoint(scope, session, name="Box", base_url="http://box/v1")
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
    return endpoint.id, group.id


async def make_run(
    scope: Scope,
    session: AsyncSession,
    endpoint_id: int,
    group_id: int,
    *,
    model_id: str = "test-model",
    outcomes: tuple[str, ...] = ("ok", "ok"),
    durations: tuple[int | None, ...] | None = None,
    params: dict[str, object] | None = None,
    comment: str | None = None,
) -> int:
    """A run whose rows are finished — what a comparable run looks like."""
    created = await create_run_record(
        scope,
        session,
        endpoint_id=endpoint_id,
        model_id=model_id,
        group_ids=[group_id],
        params=params,
        comment=comment,
        probe=_no_probe,
    )
    await session.commit()

    rows = await list_run_results(scope, session, created.run_id)
    for index, (row, outcome) in enumerate(zip(rows, outcomes, strict=False)):
        duration = durations[index] if durations is not None else 1000
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
                "duration_ms": None if outcome == "error" else duration,
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
        endpoint_id, group_id = await make_suite(scope, session)
        first = await make_run(scope, session, endpoint_id, group_id)
        second = await make_run(scope, session, endpoint_id, group_id)
        await sign_in(client, session, "member@example.com", customer_id)

        body = await matrix(client, mode="runs", runs=f"{first},{second}")

        assert body["mode"] == "runs"
        assert body["selected_run_ids"] == [first, second]
        assert [row["test_case_title"] for row in body["rows"]] == ["First", "Second"]
        assert all(cell is not None for row in body["rows"] for cell in row["cells"])
        # Same suite, same endpoint, same params: nothing drifted.
        assert [row["drift"] for row in body["rows"]] == [[], []]
        # The picker carries the tallies its column headers show.
        assert {run["id"] for run in body["available_runs"]} == {first, second}
        assert body["available_runs"][0]["ok"] == 2
        assert body["available_runs"][0]["group_names"] == ["Group"]

    async def test_a_runs_link_without_a_mode_keeps_its_pivot(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        endpoint_id, group_id = await make_suite(scope, session)
        first = await make_run(scope, session, endpoint_id, group_id)
        second = await make_run(scope, session, endpoint_id, group_id)
        await sign_in(client, session, "member@example.com", customer_id)

        assert (await matrix(client, runs=f"{first},{second}"))["mode"] == "runs"
        # …while the default pivot, with nothing selected, is by model.
        assert (await matrix(client))["mode"] == "models"

    async def test_a_single_selected_run_still_builds_a_one_column_matrix(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        # One run is a valid selection: it is still the only view that shows
        # that run's rows against the live rubric, with its own params/comment
        # in the header.
        customer_id, scope = await create_workspace("Acme")
        endpoint_id, group_id = await make_suite(scope, session)
        only = await make_run(scope, session, endpoint_id, group_id)
        await sign_in(client, session, "member@example.com", customer_id)

        body = await matrix(client, mode="runs", runs=str(only))
        assert body["min_columns"] == 1
        assert body["selected_run_ids"] == [only]
        assert [row["test_case_title"] for row in body["rows"]] == ["First", "Second"]
        assert all(cell is not None for row in body["rows"] for cell in row["cells"])

    async def test_no_selection_at_all_there_is_no_matrix(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        endpoint_id, group_id = await make_suite(scope, session)
        await make_run(scope, session, endpoint_id, group_id)
        await sign_in(client, session, "member@example.com", customer_id)

        body = await matrix(client, mode="runs")
        assert body["min_columns"] == 1
        assert body["selected_run_ids"] == []
        assert body["rows"] == []

    async def test_an_archived_run_is_hidden_unless_already_selected(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        endpoint_id, group_id = await make_suite(scope, session)
        first = await make_run(scope, session, endpoint_id, group_id)
        second = await make_run(scope, session, endpoint_id, group_id)
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
        other_endpoint, other_group = await make_suite(other_scope, session)
        foreign = await make_run(other_scope, session, other_endpoint, other_group)

        customer_id, scope = await create_workspace("Acme")
        endpoint_id, group_id = await make_suite(scope, session)
        mine = await make_run(scope, session, endpoint_id, group_id)
        await sign_in(client, session, "member@example.com", customer_id)

        body = await matrix(client, mode="runs", runs=f"{mine},{foreign}")
        assert body["selected_run_ids"] == [mine]
        # The foreign run is dropped, not silently kept — but the one that's
        # left is still a valid, single-column selection.
        assert [row["test_case_title"] for row in body["rows"]] == ["First", "Second"]
        assert [run["id"] for run in body["available_runs"]] == [mine]

    async def test_the_column_header_sums_duration_over_the_runs_own_results(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        endpoint_id, group_id = await make_suite(scope, session)
        first = await make_run(scope, session, endpoint_id, group_id, durations=(1200, 800))
        second = await make_run(scope, session, endpoint_id, group_id)
        await sign_in(client, session, "member@example.com", customer_id)

        body = await matrix(client, mode="runs", runs=f"{first},{second}")

        by_id = {run["id"]: run for run in body["run_columns"]}
        assert by_id[first]["total_duration_ms"] == 2000
        assert by_id[first]["avg_rate"] == 20.0

    async def test_the_picker_and_the_columns_carry_params_and_the_comment(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        # Two runs of the same model differ only by what they were asked for
        # and what the person noted, so the header has to be able to say both.
        # Asserted on the stored JSON string rather than a rendering of it:
        # `formatParams` on the client is what renders it.
        customer_id, scope = await create_workspace("Acme")
        endpoint_id, group_id = await make_suite(scope, session)
        first = await make_run(
            scope,
            session,
            endpoint_id,
            group_id,
            params={"temperature": 0.2},
            comment="cold run",
        )
        second = await make_run(scope, session, endpoint_id, group_id)
        await sign_in(client, session, "member@example.com", customer_id)

        body = await matrix(client, mode="runs", runs=f"{first},{second}")

        columns = {run["id"]: run for run in body["run_columns"]}
        assert json.loads(columns[first]["params"]) == {"temperature": 0.2}
        assert columns[first]["comment"] == "cold run"
        # No params and no comment stay null rather than becoming "{}"/"" —
        # "server defaults" is the client's word for it, not the wire's.
        assert columns[second]["params"] is None
        assert columns[second]["comment"] is None
        picker = {run["id"]: run for run in body["available_runs"]}
        assert picker[first]["comment"] == "cold run"

    async def test_a_run_whose_results_never_measured_duration_reports_none(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        # SQL `sum()` over an all-NULL column is NULL, not 0 — "never measured"
        # must not render as "took no time at all".
        customer_id, scope = await create_workspace("Acme")
        endpoint_id, group_id = await make_suite(scope, session)
        first = await make_run(scope, session, endpoint_id, group_id, durations=(None, None))
        second = await make_run(scope, session, endpoint_id, group_id)
        await sign_in(client, session, "member@example.com", customer_id)

        body = await matrix(client, mode="runs", runs=f"{first},{second}")

        by_id = {run["id"]: run for run in body["run_columns"]}
        assert by_id[first]["total_duration_ms"] is None


class TestModelMode:
    async def test_a_column_shows_the_newest_usable_result(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        endpoint_id, group_id = await make_suite(scope, session)
        old = await make_run(scope, session, endpoint_id, group_id)
        # The newer run answered the first case and died on the second.
        new = await make_run(
            scope, session, endpoint_id, group_id, outcomes=("ok", "error")
        )
        await sign_in(client, session, "member@example.com", customer_id)

        key = f"{endpoint_id}|test-model"
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
            # `make_run`'s default duration is 1000ms/result, and both cells
            # shown here are `ok` ones (the failed newer attempt's duration
            # never counts — see the superseded assertions above).
            "total_duration_ms": 2000,
        }

    async def test_the_column_tally_sums_duration_over_the_cells_on_screen(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        endpoint_id, group_id = await make_suite(scope, session)
        await make_run(scope, session, endpoint_id, group_id, durations=(1500, 2500))
        await sign_in(client, session, "member@example.com", customer_id)

        body = await matrix(client, models=[f"{endpoint_id}|test-model"])
        assert body["column_tallies"][0]["total_duration_ms"] == 4000

    async def test_a_column_with_no_measured_duration_reports_none(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        endpoint_id, group_id = await make_suite(scope, session)
        await make_run(scope, session, endpoint_id, group_id, durations=(None, None))
        await sign_in(client, session, "member@example.com", customer_id)

        body = await matrix(client, models=[f"{endpoint_id}|test-model"])
        assert body["column_tallies"][0]["total_duration_ms"] is None

    async def test_an_archived_run_cannot_be_asked_back(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        endpoint_id, group_id = await make_suite(scope, session)
        only = await make_run(scope, session, endpoint_id, group_id)
        await sign_in(client, session, "member@example.com", customer_id)
        assert (await client.post(f"/api/runs/{only}/archive")).status_code == 200

        body = await matrix(client, models=[f"{endpoint_id}|test-model"])
        assert body["available_models"] == []
        assert body["selected_model_keys"] == []

    async def test_the_group_filter_narrows_the_rows(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        endpoint_id, group_id = await make_suite(scope, session)
        other = await create_test_group(scope, session, name="Other")
        await create_test_case(
            scope, session, group_id=other.id, title="Third", content="Say Third."
        )
        await session.commit()
        await make_run(scope, session, endpoint_id, group_id)
        await sign_in(client, session, "member@example.com", customer_id)

        key = f"{endpoint_id}|test-model"
        unfiltered = await matrix(client, models=[key])
        assert {group["name"] for group in unfiltered["groups"]} == {"Group", "Other"}
        # "Third" was never run, so it is counted rather than drawn empty.
        assert len(unfiltered["rows"]) == 2
        assert unfiltered["uncovered_test_cases"] == 1

        filtered = await matrix(client, models=[key], group=str(other.id))
        assert filtered["selected_group_ids"] == [other.id]
        assert filtered["rows"] == []
        assert filtered["uncovered_test_cases"] == 1

class TestThreeTextsOnTheWire:
    """The four new cell fields, and drift naming each text part separately.

    `tests/test_compare.py` pins the naming rule without a database. What only
    the wired-up route can show is that the two reads actually *feed* it three
    separate texts — a matrix that silently stopped selecting the prompt columns
    would report no drift at all and look perfectly healthy.
    """

    async def _suite_with_prompts(
        self, scope: Scope, session: AsyncSession
    ) -> tuple[int, int, int, int]:
        """`(endpoint_id, group_id, system_prompt_id, task_prompt_id)`."""
        endpoint = await create_endpoint(scope, session, name="Box", base_url="http://box/v1")
        group = await create_test_group(scope, session, name="Group")
        system_prompt = await create_prompt(
            scope, session, name="framing", content="You are terse.", kind="system"
        )
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
        return endpoint.id, group.id, system_prompt.id, task_prompt.id

    async def test_a_cell_carries_both_prompt_texts_and_both_version_ids(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        endpoint_id, group_id, system_id, task_id = await self._suite_with_prompts(
            scope, session
        )
        system_version = await commit_version(scope, session, system_id, message="s1")
        await session.commit()
        first = await make_run(scope, session, endpoint_id, group_id, outcomes=("ok",))
        second = await make_run(scope, session, endpoint_id, group_id, outcomes=("ok",))
        await sign_in(client, session, "member@example.com", customer_id)

        body = await matrix(client, mode="runs", runs=f"{first},{second}")

        cell = body["rows"][0]["cells"][0]
        assert cell["system_prompt_text"] == "You are terse."
        assert cell["task_prompt_text"] == "Extract the PO."
        assert cell["test_case_text"] == "Invoice 4711"
        assert cell["system_prompt_version_id"] == system_version.id
        # The task prompt was never committed, so its draft is dirty.
        assert cell["task_prompt_version_id"] is None
        assert body["rows"][0]["drift"] == []

    async def test_only_the_edited_prompt_is_named_across_a_row(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        """Two runs of the same case with the task prompt rewritten between
        them: the data never moved, so nothing may say it did.
        """
        customer_id, scope = await create_workspace("Acme")
        endpoint_id, group_id, _, task_id = await self._suite_with_prompts(scope, session)
        first = await make_run(scope, session, endpoint_id, group_id, outcomes=("ok",))

        await update_prompt(scope, session, task_id, {"content": "Extract the PO number."})
        await session.commit()
        session.expire_all()
        second = await make_run(scope, session, endpoint_id, group_id, outcomes=("ok",))
        await sign_in(client, session, "member@example.com", customer_id)

        body = await matrix(client, mode="runs", runs=f"{first},{second}")
        assert body["rows"][0]["drift"] == ["task prompt"]

    async def test_the_system_prompt_drifts_under_its_own_name(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        endpoint_id, group_id, system_id, _ = await self._suite_with_prompts(scope, session)
        first = await make_run(scope, session, endpoint_id, group_id, outcomes=("ok",))

        await update_prompt(scope, session, system_id, {"content": "You are verbose."})
        await session.commit()
        session.expire_all()
        second = await make_run(scope, session, endpoint_id, group_id, outcomes=("ok",))
        await sign_in(client, session, "member@example.com", customer_id)

        body = await matrix(client, mode="runs", runs=f"{first},{second}")
        assert body["rows"][0]["drift"] == ["system prompt"]

    async def test_model_mode_reports_a_prompt_edited_since_the_run(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        """Model mode's second comparison, wired to the live prompt drafts.

        Without the two aliased joins feeding the live texts, this degrades to
        comparing the case text alone and reports nothing — which is exactly
        the silent failure this test exists to catch.
        """
        customer_id, scope = await create_workspace("Acme")
        endpoint_id, group_id, _, task_id = await self._suite_with_prompts(scope, session)
        await make_run(scope, session, endpoint_id, group_id, outcomes=("ok",))

        await update_prompt(scope, session, task_id, {"content": "Extract the PO number."})
        await session.commit()
        await sign_in(client, session, "member@example.com", customer_id)

        body = await matrix(client, models=[f"{endpoint_id}|test-model"])
        assert body["rows"][0]["drift"] == ["task prompt edited since"]

    async def test_the_test_case_text_still_drifts_under_its_own_name(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        # The part that existed before the split has to keep its own sentence
        # rather than being absorbed into a prompt one.
        customer_id, scope = await create_workspace("Acme")
        endpoint_id, group_id, _, _ = await self._suite_with_prompts(scope, session)
        await make_run(scope, session, endpoint_id, group_id, outcomes=("ok",))

        [case] = await list_test_cases(scope, session, group_id=group_id)
        await update_test_case(scope, session, case.id, {"content": "Invoice 4712"})
        await session.commit()
        await sign_in(client, session, "member@example.com", customer_id)

        body = await matrix(client, models=[f"{endpoint_id}|test-model"])
        assert body["rows"][0]["drift"] == ["test case text edited since"]


class TestExpectedOutputOnTheWire:
    """The rubric the row header shows, in both pivots.

    `tests/test_compare.py` pins when a row may claim one; what only the route
    can show is that the read feeds it at all — a matrix that stopped selecting
    the frozen `expected_output` would report `null` on every row and look no
    different from a suite that simply has no rubrics.
    """

    async def _suite_with_rubric(
        self, scope: Scope, session: AsyncSession, rubric: str | None
    ) -> tuple[int, int]:
        endpoint = await create_endpoint(scope, session, name="Box", base_url="http://box/v1")
        group = await create_test_group(scope, session, name="Group")
        await create_test_case(
            scope,
            session,
            group_id=group.id,
            title="First",
            content="Invoice 4711",
            expected_output=rubric,
        )
        await session.commit()
        return endpoint.id, group.id

    async def test_run_mode_carries_the_rubric_both_cells_froze(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        endpoint_id, group_id = await self._suite_with_rubric(scope, session, "the PO number")
        first = await make_run(scope, session, endpoint_id, group_id, outcomes=("ok",))
        second = await make_run(scope, session, endpoint_id, group_id, outcomes=("ok",))
        await sign_in(client, session, "member@example.com", customer_id)

        body = await matrix(client, mode="runs", runs=f"{first},{second}")

        row = body["rows"][0]
        assert row["expected_output"] == "the PO number"
        assert row["cells"][0]["expected_output"] == "the PO number"
        assert row["drift"] == []

    async def test_model_mode_carries_it_too(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        endpoint_id, group_id = await self._suite_with_rubric(scope, session, "the PO number")
        await make_run(scope, session, endpoint_id, group_id, outcomes=("ok",))
        await sign_in(client, session, "member@example.com", customer_id)

        body = await matrix(client, models=[f"{endpoint_id}|test-model"])
        assert body["rows"][0]["expected_output"] == "the PO number"

    async def test_a_test_case_without_a_rubric_reports_null(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        # Many test cases have no rubric, and the row header renders nothing at
        # all for them rather than a disclosure onto "(none)".
        customer_id, scope = await create_workspace("Acme")
        endpoint_id, group_id = await self._suite_with_rubric(scope, session, None)
        await make_run(scope, session, endpoint_id, group_id, outcomes=("ok",))
        await sign_in(client, session, "member@example.com", customer_id)

        body = await matrix(client, models=[f"{endpoint_id}|test-model"])
        assert body["rows"][0]["expected_output"] is None
        assert body["rows"][0]["drift"] == []

    async def test_a_rubric_rewritten_between_runs_becomes_drift_not_a_row_claim(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        # The rubric is frozen per result, so editing the case between two runs
        # leaves the row graded two ways. Showing either copy as the row's would
        # be a claim about how the other cell was judged.
        customer_id, scope = await create_workspace("Acme")
        endpoint_id, group_id = await self._suite_with_rubric(scope, session, "the PO number")
        first = await make_run(scope, session, endpoint_id, group_id, outcomes=("ok",))

        [case] = await list_test_cases(scope, session, group_id=group_id)
        await update_test_case(
            scope, session, case.id, {"expected_output": "the PO number and the total"}
        )
        await session.commit()
        session.expire_all()
        second = await make_run(scope, session, endpoint_id, group_id, outcomes=("ok",))
        await sign_in(client, session, "member@example.com", customer_id)

        body = await matrix(client, mode="runs", runs=f"{first},{second}")

        row = body["rows"][0]
        assert row["expected_output"] is None
        assert row["drift"] == ["expected output"]
        # The live rubric is a property of the test case rather than of either
        # cell, so it is still offered — but nothing calls it an edit, since
        # there is no single frozen copy it could have been edited from.
        assert row["live_expected_output"] == "the PO number and the total"
        assert row["rubric_edited_since"] is False


class TestTheLiveRubricOnTheWire:
    """The current rubric beside the frozen one, in both pivots.

    `tests/test_compare.py` pins when each is offered; what only the route can
    show is that the live rubric is actually *read* — run mode reads it for the
    rows on screen (`live_expected_outputs`), model mode off the test-case rows
    it already loads, and a matrix that stopped selecting it would report
    `null` on every row and look exactly like a suite nobody has edited.
    """

    async def _rubric_suite(
        self, scope: Scope, session: AsyncSession, rubric: str | None
    ) -> tuple[int, int]:
        endpoint = await create_endpoint(scope, session, name="Box", base_url="http://box/v1")
        group = await create_test_group(scope, session, name="Group")
        await create_test_case(
            scope,
            session,
            group_id=group.id,
            title="First",
            content="Invoice 4711",
            expected_output=rubric,
        )
        await session.commit()
        return endpoint.id, group.id

    async def _rewrite_rubric(
        self, scope: Scope, session: AsyncSession, group_id: int, rubric: str | None
    ) -> None:
        [case] = await list_test_cases(scope, session, group_id=group_id)
        await update_test_case(scope, session, case.id, {"expected_output": rubric})
        await session.commit()
        session.expire_all()

    async def test_both_pivots_carry_a_rubric_edited_after_the_run(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        # The result stays valid — the model never saw the rubric — so both
        # copies are offered: the frozen one explains the ratings already
        # recorded, the live one is what to rate by now.
        customer_id, scope = await create_workspace("Acme")
        endpoint_id, group_id = await self._rubric_suite(scope, session, "the PO number")
        first = await make_run(scope, session, endpoint_id, group_id, outcomes=("ok",))
        second = await make_run(scope, session, endpoint_id, group_id, outcomes=("ok",))
        await self._rewrite_rubric(
            scope, session, group_id, "the PO number and the total"
        )
        await sign_in(client, session, "member@example.com", customer_id)

        by_runs = (await matrix(client, mode="runs", runs=f"{first},{second}"))["rows"][0]
        assert by_runs["expected_output"] == "the PO number"
        assert by_runs["live_expected_output"] == "the PO number and the total"
        assert by_runs["rubric_edited_since"] is True
        # Run mode's rows are a set of runs, not a claim about today's suite:
        # the two fields say it, `drift` stays silent.
        assert by_runs["drift"] == []

        by_models = (await matrix(client, models=[f"{endpoint_id}|test-model"]))["rows"][0]
        assert by_models["expected_output"] == "the PO number"
        assert by_models["live_expected_output"] == "the PO number and the total"
        assert by_models["rubric_edited_since"] is True
        assert by_models["drift"] == ["expected output edited since"]

    async def test_an_untouched_rubric_is_not_offered_twice(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        endpoint_id, group_id = await self._rubric_suite(scope, session, "the PO number")
        await make_run(scope, session, endpoint_id, group_id, outcomes=("ok",))
        await sign_in(client, session, "member@example.com", customer_id)

        row = (await matrix(client, models=[f"{endpoint_id}|test-model"]))["rows"][0]
        assert row["expected_output"] == "the PO number"
        assert row["live_expected_output"] is None
        assert row["rubric_edited_since"] is False

    async def test_a_rubric_written_after_the_run_is_offered_as_an_addition(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        # Nothing was edited — the run simply predates the rubric — so the row
        # offers the live copy without a frozen one to compare it against.
        customer_id, scope = await create_workspace("Acme")
        endpoint_id, group_id = await self._rubric_suite(scope, session, None)
        first = await make_run(scope, session, endpoint_id, group_id, outcomes=("ok",))
        second = await make_run(scope, session, endpoint_id, group_id, outcomes=("ok",))
        await self._rewrite_rubric(scope, session, group_id, "the PO number")
        await sign_in(client, session, "member@example.com", customer_id)

        by_runs = (await matrix(client, mode="runs", runs=f"{first},{second}"))["rows"][0]
        assert by_runs["expected_output"] is None
        assert by_runs["live_expected_output"] == "the PO number"
        assert by_runs["rubric_edited_since"] is False

        by_models = (await matrix(client, models=[f"{endpoint_id}|test-model"]))["rows"][0]
        assert by_models["expected_output"] is None
        assert by_models["live_expected_output"] == "the PO number"
        assert by_models["rubric_edited_since"] is False

    async def test_a_suite_with_no_rubric_at_all_reports_neither(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        endpoint_id, group_id = await self._rubric_suite(scope, session, None)
        await make_run(scope, session, endpoint_id, group_id, outcomes=("ok",))
        await sign_in(client, session, "member@example.com", customer_id)

        row = (await matrix(client, models=[f"{endpoint_id}|test-model"]))["rows"][0]
        assert row["expected_output"] is None
        assert row["live_expected_output"] is None
        assert row["rubric_edited_since"] is False


class TestRouting:
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
