"""Schema behavior only a real Postgres can show: the FK actions Task 1.1
declared, and `timestamptz`/`bool`/`double precision` round-tripping through
asyncpg. Ports `git show master:tests/integration/schema.test.ts` onto the
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
from app.models import Machine, MachineModel, Run, RunResult, Tool

# Aliased away from the `Test`-prefixed names: pytest's default collector
# treats any class visible at test-module level whose *name* starts with
# `Test` as a candidate test class, and warns when it can't be instantiated
# with no arguments (these are SQLAlchemy declarative models).
from app.models import TestCase as CaseModel
from app.models import TestCaseToolset as CaseToolsetModel
from app.repos.machines import create_machine, delete_machine, sync_discovered_models
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
    machine = await create_machine(scope, session, name="test-box", base_url="http://x/v1")
    await sync_discovered_models(scope, session, machine.id, ["qwen3-32b"])

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
        machine_id=machine.id,
        machine_snapshot='{"name":"test-box"}',
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
        "machine_id": machine.id,
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

        machine = await fresh.get(Machine, ids["machine_id"])
        assert machine is not None
        assert isinstance(machine.created_at, datetime)
        assert machine.created_at.tzinfo is not None

        models = (
            await fresh.scalars(select(MachineModel).where(MachineModel.machine_id == machine.id))
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


async def test_nulls_run_machine_id_when_machine_deleted_keeping_the_run(
    session: AsyncSession, scope: Scope
):
    ids = await _seed_everything(session, scope)

    await delete_machine(scope, session, ids["machine_id"])
    await session.commit()

    async with async_session() as fresh:
        run = await fresh.get(Run, ids["run_id"])
        assert run is not None
        assert run.machine_id is None
        # machine_models is a cascade, unlike runs.
        assert (await fresh.scalars(select(MachineModel))).all() == []


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
