"""Cross-workspace isolation, against a real Postgres.

Ports the old suite's core (`git show master:tests/integration/workspaces.test.ts`)
onto the pivot's renamed tables and repository functions: a byte-identical
test case in two workspaces must never collapse into one row, a write naming
another workspace's row is refused rather than silently landing there, and a
foreign toolset id resolves to nothing rather than to someone else's
credentials.

The old suite's "delete guard" also asserted the friendly refusal message
("still holds 1 machine, 1 system prompt…") — that composition lives in the
customers *service* layer, which is Task 3.1's job and does not exist yet in
Phase 1. What this file exercises instead is the two things that service will
be built on: the `RESTRICT` constraint itself, and `count_customer_content`,
which is exactly what a future refusal message would be built from.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.repos.customers import count_customer_content, delete_customer
from app.repos.machines import create_machine, delete_machine
from app.repos.runs import create_run, scope_for_run
from app.repos.test_cases import (
    compare_test_case_rows,
    create_test_case,
    create_test_group,
    delete_test_group,
    replace_toolset_links,
)
from app.repos.toolsets import create_toolset, delete_toolset, list_mcp_servers
from app.scope import CrossCustomerError, Scope

CreateWorkspace = Callable[[str], Awaitable[tuple[int, Scope]]]

#: Byte-identical in both workspaces on purpose — the exact shape that made
#: the old results page's normalized-text fallback matching necessary: two
#: workspaces' identical content must never be mistaken for one row.
TEST_CASE_TITLE = "Order status"
TEST_CASE_TEXT = "Where is my order 4711?"


async def _build_workspace(
    session: AsyncSession, create_workspace: CreateWorkspace, name: str
) -> dict:
    customer_id, scope = await create_workspace(name)

    machine = await create_machine(
        scope, session, name=f"{name} box", base_url=f"http://127.0.0.1:9/{name}/v1"
    )
    toolset = await create_toolset(scope, session, name=f"{name} tools", kind="manual")
    group = await create_test_group(scope, session, name="General")
    test_case = await create_test_case(
        scope, session, group_id=group.id, title=TEST_CASE_TITLE, content=TEST_CASE_TEXT
    )

    return {
        "customer_id": customer_id,
        "scope": scope,
        "machine_id": machine.id,
        "toolset_id": toolset.id,
        "group_id": group.id,
        "test_case_id": test_case.id,
    }


async def test_byte_identical_test_case_never_collapses_across_workspaces(
    session: AsyncSession, create_workspace: CreateWorkspace
):
    a = await _build_workspace(session, create_workspace, "A")
    b = await _build_workspace(session, create_workspace, "B")

    rows_a = await compare_test_case_rows(a["scope"], session)
    assert [row.id for row in rows_a] == [a["test_case_id"]]

    rows_b = await compare_test_case_rows(b["scope"], session)
    assert [row.id for row in rows_b] == [b["test_case_id"]]


async def test_refuses_a_test_case_pointed_at_another_workspaces_group(
    session: AsyncSession, create_workspace: CreateWorkspace
):
    a = await _build_workspace(session, create_workspace, "A")
    b = await _build_workspace(session, create_workspace, "B")

    with pytest.raises(CrossCustomerError):
        await create_test_case(a["scope"], session, group_id=b["group_id"], title="x", content="y")


async def test_refuses_a_run_pointed_at_another_workspaces_machine(
    session: AsyncSession, create_workspace: CreateWorkspace
):
    a = await _build_workspace(session, create_workspace, "A")
    b = await _build_workspace(session, create_workspace, "B")

    with pytest.raises(CrossCustomerError):
        await create_run(
            a["scope"],
            session,
            machine_id=b["machine_id"],
            machine_snapshot="{}",
            model_id="qwen3-32b",
            group_names="[]",
        )


async def test_refuses_to_link_a_foreign_toolset_to_a_test_case(
    session: AsyncSession, create_workspace: CreateWorkspace
):
    a = await _build_workspace(session, create_workspace, "A")
    b = await _build_workspace(session, create_workspace, "B")

    with pytest.raises(CrossCustomerError):
        await replace_toolset_links(a["scope"], session, a["test_case_id"], [b["toolset_id"]])


async def test_resolves_no_mcp_server_for_a_foreign_toolset_id(
    session: AsyncSession, create_workspace: CreateWorkspace
):
    a = await _build_workspace(session, create_workspace, "A")
    b = await _build_workspace(session, create_workspace, "B")

    # What the executor does at run time: derive the scope from the run row,
    # then look the frozen toolset ids up live. A's toolset must be invisible
    # under B's scope, so the caller answers the model with an error string
    # instead of calling A's endpoint with A's credentials.
    assert await list_mcp_servers(b["scope"], session, [a["toolset_id"]]) == []

    servers = await list_mcp_servers(b["scope"], session, [b["toolset_id"]])
    assert [server.id for server in servers] == [b["toolset_id"]]


async def test_scope_for_run_derives_the_owning_workspace(
    session: AsyncSession, create_workspace: CreateWorkspace
):
    a = await _build_workspace(session, create_workspace, "A")

    run = await create_run(
        a["scope"],
        session,
        machine_id=a["machine_id"],
        machine_snapshot="{}",
        model_id="qwen3-32b",
        group_names="[]",
    )

    found = await scope_for_run(session, run.id)
    assert found is not None
    derived_scope, row = found
    assert row.id == run.id
    assert derived_scope.customer_id == a["customer_id"]


async def test_delete_guard_the_restrict_constraint_it_will_sit_in_front_of(
    session: AsyncSession, create_workspace: CreateWorkspace
):
    a = await _build_workspace(session, create_workspace, "A")

    counts = await count_customer_content(session, a["customer_id"])
    assert counts.machines == 1
    assert counts.toolsets == 1
    assert counts.test_groups == 1
    assert counts.total == 3

    with pytest.raises(IntegrityError):
        # A SAVEPOINT so the expected failure rolls back only itself, not the
        # whole test's transaction — the session stays usable afterwards.
        async with session.begin_nested():
            await delete_customer(session, a["customer_id"])

    still_there = await count_customer_content(session, a["customer_id"])
    assert still_there.total == 3

    # Emptying it in FK order lets the delete through — `delete_test_group`
    # cascades the test case and its toolset link.
    await delete_machine(a["scope"], session, a["machine_id"])
    await delete_toolset(a["scope"], session, a["toolset_id"])
    await delete_test_group(a["scope"], session, a["group_id"])

    emptied = await count_customer_content(session, a["customer_id"])
    assert emptied.total == 0

    await delete_customer(session, a["customer_id"])
