"""Schema behavior only a real Postgres can show: the FK actions Task 1.1
declared, and `timestamptz`/`bool`/`double precision` round-tripping through
asyncpg. Ports `git show legacy-nextjs:tests/integration/schema.test.ts` onto the
pivot's renamed tables.

Deletions go through the repository layer (`delete_run`, `delete_toolset`,
…) rather than raw SQL, and every assertion re-reads through a **brand-new**
session — the models package deliberately defines no ORM relationships and
this suite's fixtures reuse one session per test, so a fresh session is what
proves a value round-tripped through Postgres rather than just surviving in
Python's identity map.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import async_session
from app.models import Endpoint, EndpointModel, Run, RunResult, Tool

# Aliased away from the `Test`-prefixed names: pytest's default collector
# treats any class visible at test-module level whose *name* starts with
# `Test` as a candidate test class, and warns when it can't be instantiated
# with no arguments (these are SQLAlchemy declarative models).
from app.models import TestCase as CaseModel
from app.models import TestCaseToolset as CaseToolsetModel
from app.models.prompts import PromptVersion
from app.repos.endpoints import create_endpoint, delete_endpoint, sync_discovered_models
from app.repos.prompt_versions import commit_version
from app.repos.prompts import create_prompt, delete_prompt
from app.repos.runs import create_run, delete_run, insert_run_results, list_run_results
from app.repos.test_cases import (
    create_test_case,
    create_test_group,
    delete_test_case,
    replace_toolset_links,
)
from app.repos.toolsets import create_tool, create_toolset, delete_toolset
from app.scope import Scope

NOW = datetime(2026, 7, 27, 9, 46, 0, tzinfo=UTC)


async def _seed_everything(session: AsyncSession, scope: Scope) -> dict[str, int]:
    endpoint = await create_endpoint(scope, session, name="test-box", base_url="http://x/v1")
    await sync_discovered_models(scope, session, endpoint.id, ["qwen3-32b"])

    toolset = await create_toolset(scope, session, name="Support Desk", kind="manual")
    tool = await create_tool(
        scope,
        session,
        toolset.id,
        name="lookup_order",
        parameters_json="{}",
        mock_response="shipped",
    )

    group = await create_test_group(scope, session, name="General")
    test_case = await create_test_case(
        scope, session, group_id=group.id, title="Hello", content="Say hi."
    )
    await replace_toolset_links(scope, session, test_case.id, [toolset.id])

    run = await create_run(
        scope,
        session,
        endpoint_id=endpoint.id,
        endpoint_snapshot='{"name":"test-box"}',
        model_id="qwen3-32b",
        group_names='["General"]',
        status="completed",
    )
    await insert_run_results(
        scope,
        session,
        run.id,
        [
            {
                "test_case_id": test_case.id,
                "group_name": "General",
                "test_case_title": "Hello",
                "test_case_text": "Say hi.",
                "status": "ok",
                "tokens_per_sec": 41.318472916393,
                "tokens_estimated": True,
                "rating": "good",
                "started_at": NOW,
                "finished_at": NOW,
            }
        ],
    )
    [result] = await list_run_results(scope, session, run.id)

    await session.commit()
    return {
        "endpoint_id": endpoint.id,
        "toolset_id": toolset.id,
        "tool_id": tool.id,
        "group_id": group.id,
        "test_case_id": test_case.id,
        "run_id": run.id,
        "result_id": result.id,
    }


async def test_round_trips_date_bool_and_double_precision(session: AsyncSession, scope: Scope):
    ids = await _seed_everything(session, scope)

    async with async_session() as fresh:
        result = await fresh.get(RunResult, ids["result_id"])
        assert result is not None
        assert isinstance(result.started_at, datetime)
        assert result.started_at.timestamp() == NOW.timestamp()
        assert result.tokens_estimated is True
        # float8, not float4: the historical value must not be silently rounded.
        assert result.tokens_per_sec == 41.318472916393

        endpoint = await fresh.get(Endpoint, ids["endpoint_id"])
        assert endpoint is not None
        assert isinstance(endpoint.created_at, datetime)
        assert endpoint.created_at.tzinfo is not None

        models = (
            await fresh.scalars(
                select(EndpointModel).where(EndpointModel.endpoint_id == endpoint.id)
            )
        ).all()
        assert len(models) == 1
        assert models[0].currently_loaded is True


async def test_cascades_run_results_when_run_deleted(session: AsyncSession, scope: Scope):
    ids = await _seed_everything(session, scope)

    await delete_run(scope, session, ids["run_id"])
    await session.commit()

    async with async_session() as fresh:
        assert (await fresh.scalars(select(RunResult))).all() == []


async def test_cascades_tools_and_links_when_toolset_deleted(session: AsyncSession, scope: Scope):
    ids = await _seed_everything(session, scope)

    await delete_toolset(scope, session, ids["toolset_id"])
    await session.commit()

    async with async_session() as fresh:
        assert (await fresh.scalars(select(Tool))).all() == []
        assert (await fresh.scalars(select(CaseToolsetModel))).all() == []
        # The link is a cascade too; the test case itself survives.
        case = await fresh.get(CaseModel, ids["test_case_id"])
        assert case is not None


async def test_nulls_run_endpoint_id_when_endpoint_deleted_keeping_the_run(
    session: AsyncSession, scope: Scope
):
    ids = await _seed_everything(session, scope)

    await delete_endpoint(scope, session, ids["endpoint_id"])
    await session.commit()

    async with async_session() as fresh:
        run = await fresh.get(Run, ids["run_id"])
        assert run is not None
        assert run.endpoint_id is None
        # endpoint_models is a cascade, unlike runs.
        assert (await fresh.scalars(select(EndpointModel))).all() == []


async def test_nulls_result_test_case_id_when_test_case_deleted_keeping_the_snapshot(
    session: AsyncSession, scope: Scope
):
    ids = await _seed_everything(session, scope)

    await delete_test_case(scope, session, ids["test_case_id"])
    await session.commit()

    async with async_session() as fresh:
        result = await fresh.get(RunResult, ids["result_id"])
        assert result is not None
        assert result.test_case_id is None
        assert result.test_case_text == "Say hi."


# ---------------------------------------------------------------------------
# The two prompt slots and the two version columns
# ---------------------------------------------------------------------------


async def _seed_both_slots(session: AsyncSession, scope: Scope) -> dict[str, int]:
    """One prompt per kind, each committed once, both referenced by one test
    case and both attributed on one result row.

    Everything the prompt-kinds spec added to the FK graph hangs off this: two
    `SET NULL` slots on `test_cases`, two `SET NULL` version columns on
    `run_results`, and the `CASCADE` from a prompt to its own history.
    """
    system_prompt = await create_prompt(
        scope, session, name="framing", content="SYSTEM", kind="system"
    )
    system_version = await commit_version(scope, session, system_prompt.id, message="s1")
    task_prompt = await create_prompt(
        scope, session, name="instruction", content="TASK", kind="task"
    )
    task_version = await commit_version(scope, session, task_prompt.id, message="t1")

    group = await create_test_group(scope, session, name="General")
    test_case = await create_test_case(
        scope,
        session,
        group_id=group.id,
        title="Both slots",
        content="DATA",
        system_prompt_id=system_prompt.id,
        task_prompt_id=task_prompt.id,
    )

    endpoint = await create_endpoint(scope, session, name="test-box", base_url="http://x/v1")
    run = await create_run(
        scope,
        session,
        endpoint_id=endpoint.id,
        endpoint_snapshot='{"name":"test-box"}',
        model_id="qwen3-32b",
        group_names='["General"]',
        status="completed",
    )
    await insert_run_results(
        scope,
        session,
        run.id,
        [
            {
                "test_case_id": test_case.id,
                "group_name": "General",
                "test_case_title": "Both slots",
                "test_case_text": "DATA",
                "system_prompt_text": "SYSTEM",
                "task_prompt_text": "TASK",
                "system_prompt_version_id": system_version.id,
                "task_prompt_version_id": task_version.id,
                "status": "ok",
            }
        ],
    )
    [result] = await list_run_results(scope, session, run.id)

    await session.commit()
    return {
        "system_prompt_id": system_prompt.id,
        "task_prompt_id": task_prompt.id,
        "system_version_id": system_version.id,
        "task_version_id": task_version.id,
        "test_case_id": test_case.id,
        "result_id": result.id,
    }


async def test_nulls_the_system_slot_when_its_prompt_is_deleted(
    session: AsyncSession, scope: Scope
):
    ids = await _seed_both_slots(session, scope)

    await delete_prompt(scope, session, ids["system_prompt_id"])
    await session.commit()

    async with async_session() as fresh:
        case = await fresh.get(CaseModel, ids["test_case_id"])
        assert case is not None
        assert case.system_prompt_id is None
        # The other slot is untouched: the two are independent references.
        assert case.task_prompt_id == ids["task_prompt_id"]


async def test_nulls_the_task_slot_when_its_prompt_is_deleted(
    session: AsyncSession, scope: Scope
):
    ids = await _seed_both_slots(session, scope)

    await delete_prompt(scope, session, ids["task_prompt_id"])
    await session.commit()

    async with async_session() as fresh:
        case = await fresh.get(CaseModel, ids["test_case_id"])
        assert case is not None
        assert case.task_prompt_id is None
        assert case.system_prompt_id == ids["system_prompt_id"]


async def test_nulls_the_system_version_column_keeping_both_frozen_texts(
    session: AsyncSession, scope: Scope
):
    """Deleting a prompt cascades its history away, and the result row loses
    only the *attribution* — never the text it actually sent.
    """
    ids = await _seed_both_slots(session, scope)

    await delete_prompt(scope, session, ids["system_prompt_id"])
    await session.commit()

    async with async_session() as fresh:
        # CASCADE from the prompt: the version rows are gone.
        remaining = (await fresh.scalars(select(PromptVersion))).all()
        assert [version.id for version in remaining] == [ids["task_version_id"]]

        result = await fresh.get(RunResult, ids["result_id"])
        assert result is not None
        assert result.system_prompt_version_id is None
        assert result.task_prompt_version_id == ids["task_version_id"]
        assert result.system_prompt_text == "SYSTEM"
        assert result.task_prompt_text == "TASK"


async def test_nulls_the_task_version_column_keeping_both_frozen_texts(
    session: AsyncSession, scope: Scope
):
    ids = await _seed_both_slots(session, scope)

    await delete_prompt(scope, session, ids["task_prompt_id"])
    await session.commit()

    async with async_session() as fresh:
        result = await fresh.get(RunResult, ids["result_id"])
        assert result is not None
        assert result.task_prompt_version_id is None
        assert result.system_prompt_version_id == ids["system_version_id"]
        assert result.system_prompt_text == "SYSTEM"
        assert result.task_prompt_text == "TASK"


async def test_a_content_less_test_case_round_trips_as_null(
    session: AsyncSession, scope: Scope
):
    """`test_cases.content` and `run_results.test_case_text` are both nullable
    now — a task prompt can be the whole user message. Stored as `NULL`, not
    coerced to `""`, so a case with no input is stored as what it is.
    """
    ids = await _seed_both_slots(session, scope)
    case = await create_test_case(
        session=session,
        scope=scope,
        group_id=(await session.get(CaseModel, ids["test_case_id"])).group_id,
        title="No input",
        task_prompt_id=ids["task_prompt_id"],
    )
    await session.commit()

    async with async_session() as fresh:
        stored = await fresh.get(CaseModel, case.id)
        assert stored is not None
        assert stored.content is None
