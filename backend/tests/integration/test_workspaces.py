"""Cross-workspace isolation, against a real Postgres.

Ports the old suite's core (`git show legacy-nextjs:tests/integration/workspaces.test.ts`)
onto the pivot's renamed tables and repository functions: a byte-identical
test case in two workspaces must never collapse into one row, a write naming
another workspace's row is refused rather than silently landing there, and a
foreign toolset id resolves to nothing rather than to someone else's
credentials.

The old suite's "delete guard" also asserted the friendly refusal message
("still holds 1 endpoint, 1 system prompt…") — that composition lives in the
customers *service* layer, which is Task 3.1's job and does not exist yet in
Phase 1. What this file exercises instead is the two things that service will
be built on: the `RESTRICT` constraint itself, and `count_customer_content`,
which is exactly what a future refusal message would be built from.

`TestGlobals` at the bottom is the deliberate exception to everything above:
the one place rows *do* cross a workspace boundary. It is here rather than in a
file of its own because "which reads see a shared row and which writes still
refuse it" is the same question the rest of this file asks, only answered the
other way — and the two have to be read together to see that the isolation
above survived the sharing. The Base workspace's *own* rules that live at the
API boundary (it cannot be deleted or archived) are in
`test_customers_api.py`, next to the delete-guard tests they extend.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import pytest
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import async_session
from app.models import Customer
from app.repos.customers import (
    NotBaseWorkspaceError,
    base_customer_id,
    count_customer_content,
    delete_customer,
)
from app.repos.endpoints import (
    EndpointInUseError,
    create_endpoint,
    delete_endpoint,
    get_endpoint,
    list_endpoint_models,
    list_endpoints,
    sync_discovered_models,
    touch_endpoint_model,
    update_endpoint,
)
from app.repos.prompts import PromptSlotError, create_prompt, delete_prompt
from app.repos.runs import (
    create_run,
    list_run_results,
    list_runs,
    scope_for_run,
    update_run_status,
)
from app.repos.test_cases import (
    compare_test_case_rows,
    create_test_case,
    create_test_group,
    delete_test_group,
    list_snapshot_tool_rows,
    list_test_case_toolset_views,
    replace_toolset_links,
    update_test_case,
)
from app.repos.toolsets import (
    ToolsetInUseError,
    create_tool,
    create_toolset,
    delete_toolset,
    get_toolset,
    list_mcp_servers,
    list_tools,
    list_toolsets,
    update_toolset,
)
from app.scope import CrossCustomerError, Scope
from app.services.executor import _resolve_endpoint
from app.services.run_create import create_run_record
from app.services.tool_config import assert_tool_config

CreateWorkspace = Callable[[str], Awaitable[tuple[int, Scope]]]

#: The model every shared-endpoint test below books, so that "first sighting"
#: means the same thing in each of them.
SHARED_MODEL = "qwen3-32b"


async def _no_probe(base_url: str, api_key: str | None, model_id: str) -> None:
    """`create_run_record`'s network call, stubbed — see `test_run_create.py`.

    These tests are about which rows a run creation writes, and the probe is a
    socket to an endpoint that does not exist.
    """
    del base_url, api_key, model_id
    return None

#: Byte-identical in both workspaces on purpose — the exact shape that made
#: the old results page's normalized-text fallback matching necessary: two
#: workspaces' identical content must never be mistaken for one row.
TEST_CASE_TITLE = "Order status"
TEST_CASE_TEXT = "Where is my order 4711?"


async def _build_workspace(
    session: AsyncSession, create_workspace: CreateWorkspace, name: str
) -> dict:
    customer_id, scope = await create_workspace(name)

    endpoint = await create_endpoint(
        scope, session, name=f"{name} box", base_url=f"http://127.0.0.1:9/{name}/v1"
    )
    toolset = await create_toolset(scope, session, name=f"{name} tools", kind="manual")
    # Byte-identical *names* in both workspaces, like the test case below: a
    # slot or a join resolved by name rather than by id would cross here.
    system_prompt = await create_prompt(
        scope, session, name="Framing", content=f"{name} system draft", kind="system"
    )
    task_prompt = await create_prompt(
        scope, session, name="Instruction", content=f"{name} task draft", kind="task"
    )
    group = await create_test_group(scope, session, name="General")
    test_case = await create_test_case(
        scope, session, group_id=group.id, title=TEST_CASE_TITLE, content=TEST_CASE_TEXT
    )

    return {
        "customer_id": customer_id,
        "scope": scope,
        "endpoint_id": endpoint.id,
        "toolset_id": toolset.id,
        "system_prompt_id": system_prompt.id,
        "task_prompt_id": task_prompt.id,
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


async def test_refuses_a_test_case_pointed_at_another_workspaces_prompt(
    session: AsyncSession, create_workspace: CreateWorkspace
):
    """Both slots, both directions.

    `assert_prompt_slot` answers with the *same* refusal a missing row gets
    (`CrossCustomerError`, "no longer exists in this workspace") rather than
    the wrong-kind one — as far as this workspace is concerned that prompt does
    not exist, and saying anything else would leak that it does.
    """
    a = await _build_workspace(session, create_workspace, "A")
    b = await _build_workspace(session, create_workspace, "B")

    with pytest.raises(CrossCustomerError):
        await create_test_case(
            a["scope"],
            session,
            group_id=a["group_id"],
            title="x",
            content="y",
            system_prompt_id=b["system_prompt_id"],
        )

    with pytest.raises(CrossCustomerError):
        await create_test_case(
            a["scope"],
            session,
            group_id=a["group_id"],
            title="x",
            content="y",
            task_prompt_id=b["task_prompt_id"],
        )


async def test_a_wrong_kind_prompt_is_a_different_refusal_from_a_foreign_one(
    session: AsyncSession, create_workspace: CreateWorkspace
):
    # Inside one workspace the row really is there, so the refusal says so —
    # which is what makes the two exception types worth keeping apart.
    a = await _build_workspace(session, create_workspace, "A")

    with pytest.raises(PromptSlotError):
        await create_test_case(
            a["scope"],
            session,
            group_id=a["group_id"],
            title="x",
            content="y",
            system_prompt_id=a["task_prompt_id"],
        )


async def test_model_mode_rows_never_carry_another_workspaces_prompt_draft(
    session: AsyncSession, create_workspace: CreateWorkspace
):
    """`compare_test_case_rows` joins each slot's live draft for the "edited
    since" comparison, and both joins are scoped through the same group.

    Byte-identical prompt names in both workspaces is the shape that would
    expose a join written on name rather than on id.
    """
    a = await _build_workspace(session, create_workspace, "A")
    b = await _build_workspace(session, create_workspace, "B")

    for workspace in (a, b):
        await update_test_case(
            workspace["scope"],
            session,
            workspace["test_case_id"],
            {
                "system_prompt_id": workspace["system_prompt_id"],
                "task_prompt_id": workspace["task_prompt_id"],
            },
        )
    session.expire_all()

    [row_a] = await compare_test_case_rows(a["scope"], session)
    assert row_a.system_prompt_text == "A system draft"
    assert row_a.task_prompt_text == "A task draft"

    [row_b] = await compare_test_case_rows(b["scope"], session)
    assert row_b.system_prompt_text == "B system draft"
    assert row_b.task_prompt_text == "B task draft"


async def test_refuses_a_run_pointed_at_another_workspaces_endpoint(
    session: AsyncSession, create_workspace: CreateWorkspace
):
    a = await _build_workspace(session, create_workspace, "A")
    b = await _build_workspace(session, create_workspace, "B")

    with pytest.raises(CrossCustomerError):
        await create_run(
            a["scope"],
            session,
            endpoint_id=b["endpoint_id"],
            endpoint_snapshot="{}",
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
        endpoint_id=a["endpoint_id"],
        endpoint_snapshot="{}",
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
    assert counts.endpoints == 1
    assert counts.toolsets == 1
    assert counts.test_groups == 1
    # One prompt per kind — both are root rows this workspace holds, and the
    # future refusal message has to name them.
    assert counts.prompts == 2
    assert counts.total == 5

    with pytest.raises(IntegrityError):
        # A SAVEPOINT so the expected failure rolls back only itself, not the
        # whole test's transaction — the session stays usable afterwards.
        async with session.begin_nested():
            await delete_customer(session, a["customer_id"])

    still_there = await count_customer_content(session, a["customer_id"])
    assert still_there.total == 5

    # Emptying it in FK order lets the delete through — `delete_test_group`
    # cascades the test case and its toolset link, and `delete_prompt`
    # cascades each prompt's version history.
    await delete_endpoint(a["scope"], session, a["endpoint_id"])
    await delete_toolset(a["scope"], session, a["toolset_id"])
    await delete_test_group(a["scope"], session, a["group_id"])
    await delete_prompt(a["scope"], session, a["system_prompt_id"])
    await delete_prompt(a["scope"], session, a["task_prompt_id"])

    emptied = await count_customer_content(session, a["customer_id"])
    assert emptied.total == 0

    await delete_customer(session, a["customer_id"])


# ---------------------------------------------------------------------------
# Global endpoints and toolsets — the one thing that *does* cross a workspace
# ---------------------------------------------------------------------------


async def _make_base(
    session: AsyncSession, create_workspace: CreateWorkspace, name: str = "Base"
) -> tuple[int, Scope]:
    """A Base workspace, flagged the way the migration flags it.

    Written as a direct UPDATE rather than through a repository on purpose:
    there is no application path that creates or moves the Base flag, and a
    test helper that invented one would be testing a surface that does not
    exist. The `TRUNCATE` between tests takes the migration's own Base with it,
    so every test that needs one makes its own.
    """
    customer_id, scope = await create_workspace(name)
    await session.execute(
        update(Customer).where(Customer.id == customer_id).values(is_base=True)
    )
    return customer_id, scope


class TestGlobals:
    async def test_a_global_endpoint_is_visible_from_another_workspace(
        self, session: AsyncSession, create_workspace: CreateWorkspace
    ):
        base_id, base = await _make_base(session, create_workspace)
        assert await base_customer_id(session) == base_id
        _, a = await create_workspace("A")

        shared = await create_endpoint(
            base, session, name="DGX Spark", base_url="http://127.0.0.1:9/v1", is_global=True
        )
        await touch_endpoint_model(
            base, session, endpoint_id=shared.id, model_id="qwen3-32b", source="manual"
        )

        # Visible as a row, by list and by id, and its model history comes with
        # it — a shared box with no models on it would be unusable on the
        # new-run page, which is the whole reason `scope_through_parent` gained
        # the same opt-in.
        assert [row.id for row in await list_endpoints(a, session)] == [shared.id]
        found = await get_endpoint(a, session, shared.id)
        assert found is not None and found.is_global
        models = await list_endpoint_models(a, session, endpoint_id=shared.id)
        assert [row.model_id for row in models] == ["qwen3-32b"]

    async def test_a_local_base_endpoint_stays_invisible_elsewhere(
        self, session: AsyncSession, create_workspace: CreateWorkspace
    ):
        # Base is an ordinary workspace in every other respect — it owns groups,
        # cases and prompts of its own — so being Base must not make its
        # *unshared* rows readable. Only the flag shares anything.
        _, base = await _make_base(session, create_workspace)
        _, a = await create_workspace("A")

        private = await create_endpoint(
            base, session, name="Base laptop", base_url="http://127.0.0.1:9/local/v1"
        )
        assert await list_endpoints(a, session) == []
        assert await get_endpoint(a, session, private.id) is None

    async def test_a_global_endpoint_is_not_editable_or_deletable_from_elsewhere(
        self, session: AsyncSession, create_workspace: CreateWorkspace
    ):
        """The claim the design rests on: `scope_where` staying strict is the
        whole of "read-only outside Base", with no guard of its own.

        Asserted at the repository level, where the predicate is, rather than
        through the API's 403 — the API refusal exists so a caller is not told a
        no-op succeeded, but if this ever passed the refusal would be cosmetic.
        """
        _, base = await _make_base(session, create_workspace)
        _, a = await create_workspace("A")

        shared_id = (
            await create_endpoint(
                base,
                session,
                name="DGX Spark",
                base_url="http://127.0.0.1:9/v1",
                is_global=True,
            )
        ).id

        await update_endpoint(a, session, shared_id, {"name": "hijacked"})
        session.expire_all()
        still = await get_endpoint(base, session, shared_id)
        assert still is not None and still.name == "DGX Spark"

        await delete_endpoint(a, session, shared_id)
        assert await get_endpoint(base, session, shared_id) is not None

        # And the owner can still do both.
        await update_endpoint(base, session, shared_id, {"name": "DGX Spark 2"})
        session.expire_all()
        renamed = await get_endpoint(base, session, shared_id)
        assert renamed is not None and renamed.name == "DGX Spark 2"

    async def test_a_global_toolset_is_not_editable_from_elsewhere(
        self, session: AsyncSession, create_workspace: CreateWorkspace
    ):
        _, base = await _make_base(session, create_workspace)
        _, a = await create_workspace("A")

        shared_id = (await create_toolset(base, session, name="Mock ERP", is_global=True)).id
        assert [row.id for row in await list_toolsets(a, session)] == [shared_id]

        await update_toolset(a, session, shared_id, {"name": "hijacked"})
        await delete_toolset(a, session, shared_id)
        session.expire_all()
        still = await get_toolset(base, session, shared_id)
        assert still is not None and still.name == "Mock ERP"

    async def test_is_global_is_refused_outside_base(
        self, session: AsyncSession, create_workspace: CreateWorkspace
    ):
        """Authoring a global is switching into Base, not asking nicely.

        Both tables, and both on create and on update — the check lives inside
        the repository functions, so this is what any route, MCP tool or script
        would hit.
        """
        await _make_base(session, create_workspace)
        _, a = await create_workspace("A")

        with pytest.raises(NotBaseWorkspaceError, match="Base workspace"):
            await create_endpoint(
                a, session, name="Mine", base_url="http://127.0.0.1:9/v1", is_global=True
            )
        with pytest.raises(NotBaseWorkspaceError, match="Base workspace"):
            await create_toolset(a, session, name="Mine", is_global=True)

        endpoint_id = (
            await create_endpoint(a, session, name="Mine", base_url="http://127.0.0.1:9/v1")
        ).id
        toolset_id = (await create_toolset(a, session, name="Mine")).id
        with pytest.raises(NotBaseWorkspaceError, match="Base workspace"):
            await update_endpoint(a, session, endpoint_id, {"is_global": True})
        with pytest.raises(NotBaseWorkspaceError, match="Base workspace"):
            await update_toolset(a, session, toolset_id, {"is_global": True})

        # Nothing was written: the refusal happens before the UPDATE, exactly
        # like `update_prompt`'s kind guard.
        session.expire_all()
        endpoint = await get_endpoint(a, session, endpoint_id)
        toolset = await get_toolset(a, session, toolset_id)
        assert endpoint is not None and endpoint.is_global is False
        assert toolset is not None and toolset.is_global is False

    async def test_another_workspaces_test_case_can_select_a_global_toolset(
        self, session: AsyncSession, create_workspace: CreateWorkspace
    ):
        """Both halves of "a test case's toolsets": the validating half
        (`assert_tool_config`) and the link write (`replace_toolset_links`).

        A stricter check in either would refuse exactly what the other allows,
        which is why both carry `allow_global=True`.
        """
        _, base = await _make_base(session, create_workspace)
        _, a = await create_workspace("A")

        shared = await create_toolset(base, session, name="Mock ERP", is_global=True)
        await create_tool(base, session, shared.id, name="lookup_order", mock_response="{}")

        group = await create_test_group(a, session, name="General")
        case = await create_test_case(
            a,
            session,
            group_id=group.id,
            title="Order status",
            content="Where is 4711?",
            tool_mode="execute",
        )

        # The tools travel through to the run snapshot, which is the read run
        # creation actually freezes from.
        assert [tool.name for tool in await list_tools(a, session, toolset_ids=[shared.id])] == [
            "lookup_order"
        ]
        await assert_tool_config(
            a, session, tool_mode="execute", toolset_ids=[shared.id], subject="Test case"
        )
        await replace_toolset_links(a, session, case.id, [shared.id])

        views = await list_test_case_toolset_views(a, session, [case.id])
        assert [view.name for view in views] == ["Mock ERP"]
        snapshot = await list_snapshot_tool_rows(a, session, [case.id])
        assert [row.tool_name for row in snapshot] == ["lookup_order"]

        # And a *local* Base toolset is still refused, so the widening is the
        # flag and not the workspace.
        private = await create_toolset(base, session, name="Base only")
        with pytest.raises(CrossCustomerError):
            await replace_toolset_links(a, session, case.id, [private.id])

    async def test_deleting_a_global_toolset_names_what_still_uses_it(
        self, session: AsyncSession, create_workspace: CreateWorkspace
    ):
        """`test_case_toolsets.toolset_id` cascades, so an ungated delete would
        strip a shared toolset out of every engagement's cases in silence. The
        refusal counts them per workspace, the way `delete_customer`'s does.
        """
        _, base = await _make_base(session, create_workspace)
        _, a = await create_workspace("A")
        _, b = await create_workspace("B")

        shared_id = (await create_toolset(base, session, name="Mock ERP", is_global=True)).id
        await create_tool(base, session, shared_id, name="lookup_order", mock_response="{}")

        linked: list[tuple[Scope, int]] = []
        for workspace, titles in ((a, ("One", "Two")), (b, ("Three",))):
            group = await create_test_group(workspace, session, name="General")
            for title in titles:
                case = await create_test_case(
                    workspace, session, group_id=group.id, title=title, content="x"
                )
                await replace_toolset_links(workspace, session, case.id, [shared_id])
                linked.append((workspace, case.id))

        with pytest.raises(ToolsetInUseError) as refusal:
            await delete_toolset(base, session, shared_id)
        message = str(refusal.value)
        assert "3 test cases" in message
        assert "2 in A" in message
        assert "1 in B" in message

        session.expire_all()
        assert await get_toolset(base, session, shared_id) is not None

        # Un-sharing is the same refusal, because it is the same loss one step
        # earlier: clearing `is_global` leaves all three links in place behind a
        # row A and B can no longer see, and a delete keyed on the *flag* would
        # then wave the cascade straight through.
        with pytest.raises(ToolsetInUseError) as unshare:
            await update_toolset(base, session, shared_id, {"is_global": False})
        assert "3 test cases" in str(unshare.value)
        session.expire_all()
        still_shared = await get_toolset(base, session, shared_id)
        assert still_shared is not None and still_shared.is_global is True

        # Unlinked everywhere, it deletes. (A *local* toolset never trips the
        # guard at all — its cases are in the same workspace, so whoever deletes
        # it can see exactly what they are unlinking.)
        for workspace, case_id in linked:
            await replace_toolset_links(workspace, session, case_id, [])
        await delete_toolset(base, session, shared_id)
        assert await get_toolset(base, session, shared_id) is None

    async def test_un_sharing_then_deleting_cannot_slip_past_the_guard(
        self, session: AsyncSession, create_workspace: CreateWorkspace
    ):
        """The guard is keyed on who holds a link, not on `is_global`.

        Keyed on the flag, this sequence was a silent cross-workspace cascade
        with a refusal message that pointed straight at it: un-share (unchecked),
        then delete (a row no longer global, so unguarded), and A's test case
        loses its toolset with nothing said.
        """
        _, base = await _make_base(session, create_workspace)
        _, a = await create_workspace("A")

        shared_id = (await create_toolset(base, session, name="Mock ERP", is_global=True)).id
        group = await create_test_group(a, session, name="General")
        case_id = (
            await create_test_case(a, session, group_id=group.id, title="One", content="x")
        ).id
        await replace_toolset_links(a, session, case_id, [shared_id])

        with pytest.raises(ToolsetInUseError, match="1 test case in another workspace"):
            await update_toolset(base, session, shared_id, {"is_global": False})
        with pytest.raises(ToolsetInUseError):
            await delete_toolset(base, session, shared_id)

        session.expire_all()
        assert await get_toolset(base, session, shared_id) is not None
        views = await list_test_case_toolset_views(a, session, [case_id])
        assert [view.name for view in views] == ["Mock ERP"]

    async def test_a_run_against_a_global_endpoint_lands_in_the_running_workspace(
        self, session: AsyncSession, create_workspace: CreateWorkspace
    ):
        """The run is A's; only the endpoint is borrowed.

        And the model sighting lands on the *shared* endpoint's history, which
        is the accumulation `EndpointModel`'s docstring calls intended: one box,
        one history, whichever engagement booked it.
        """
        _, base = await _make_base(session, create_workspace)
        a_id, a = await create_workspace("A")

        shared = await create_endpoint(
            base, session, name="DGX Spark", base_url="http://127.0.0.1:9/v1", is_global=True
        )

        run = await create_run(
            a,
            session,
            endpoint_id=shared.id,
            endpoint_snapshot="{}",
            model_id="qwen3-32b",
            group_names="[]",
        )
        await touch_endpoint_model(
            a, session, endpoint_id=shared.id, model_id="qwen3-32b", source="run"
        )
        await session.flush()

        assert run.customer_id == a_id
        found = await scope_for_run(session, run.id)
        assert found is not None and found[0].customer_id == a_id

        # One row, not one per workspace: the second sighting bumped the first
        # rather than colliding with the unique `(endpoint_id, model_id)`.
        await touch_endpoint_model(
            base, session, endpoint_id=shared.id, model_id="qwen3-32b", source="manual"
        )
        history = await list_endpoint_models(base, session, endpoint_id=shared.id)
        assert [row.model_id for row in history] == ["qwen3-32b"]

    async def test_two_workspaces_first_sighting_of_one_model_keeps_both_runs(
        self, session: AsyncSession, create_workspace: CreateWorkspace
    ):
        """Concurrent first sightings on a shared endpoint must not cost a run.

        `endpoint_models` is written inside `create_run_record`'s transaction,
        next to the run row and every one of its result rows — so a failure
        there is not a missing history entry, it is a lost run. As a
        read-then-insert that was reachable the moment an endpoint could be
        shared: two workspaces booking the same global box with a model it has
        never served both find no row, both insert, and the second commit dies
        on `uq_endpoint_models_endpoint_id_model_id`.

        The interleaving is **forced**, not hoped for. Each booking gets its own
        session, A holds its transaction open, and B reaches the same key and
        blocks on it in Postgres until A resolves — which is exactly the window
        the old code had no answer for and `ON CONFLICT` now serialises.
        """
        _, base = await _make_base(session, create_workspace)
        _, a = await create_workspace("A")
        _, b = await create_workspace("B")

        shared_id = (
            await create_endpoint(
                base,
                session,
                name="DGX Spark",
                base_url="http://127.0.0.1:9/v1",
                is_global=True,
            )
        ).id

        group_ids: dict[str, int] = {}
        for workspace, name in ((a, "A"), (b, "B")):
            group = await create_test_group(workspace, session, name="General")
            await create_test_case(
                workspace, session, group_id=group.id, title=f"{name} case", content="x"
            )
            group_ids[name] = group.id
        # Committed on purpose: the two sessions below are real, separate
        # connections, and nothing uncommitted here would be visible to them.
        await session.commit()

        async def book(scope: Scope, group_id: int, own: AsyncSession):
            return await create_run_record(
                scope,
                own,
                endpoint_id=shared_id,
                model_id=SHARED_MODEL,
                group_ids=[group_id],
                probe=_no_probe,
            )

        async with async_session() as session_a, async_session() as session_b:
            created_a = await book(a, group_ids["A"], session_a)
            booking_b = asyncio.create_task(book(b, group_ids["B"], session_b))
            # Let B get as far as the key A already holds uncommitted. Without
            # this the two coroutines never overlap and the test asserts
            # nothing about concurrency at all.
            await asyncio.sleep(0.25)
            await session_a.commit()
            created_b = await booking_b
            await session_b.commit()

        assert created_a.run_id != created_b.run_id
        assert len(await list_run_results(a, session, created_a.run_id)) == 1
        assert len(await list_run_results(b, session, created_b.run_id)) == 1

        # And one shared history row, not one per workspace and not a duplicate.
        history = await list_endpoint_models(base, session, endpoint_id=shared_id)
        assert [(row.model_id, row.source, row.currently_loaded) for row in history] == [
            (SHARED_MODEL, "run", False)
        ]

        # The two columns a re-sighting must leave alone, in both directions:
        # discovery owns `currently_loaded`, and `source` keeps saying how the
        # model was *first* learned about — here a run, before discovery ever
        # saw the box.
        await sync_discovered_models(base, session, shared_id, [SHARED_MODEL])
        await touch_endpoint_model(
            a, session, endpoint_id=shared_id, model_id=SHARED_MODEL, source="run"
        )
        session.expire_all()
        settled = await list_endpoint_models(base, session, endpoint_id=shared_id)
        assert [(row.source, row.currently_loaded) for row in settled] == [("run", True)]

    async def test_un_sharing_an_endpoint_mid_run_elsewhere_is_refused(
        self, session: AsyncSession, create_workspace: CreateWorkspace
    ):
        """Clearing `is_global` must not quietly strip a run of its API key.

        A run reads `base_url`/`api_key` **live** at execution — deliberately,
        so a *moved* endpoint does not break Resume — and its
        `endpoint_snapshot` carries no credentials, equally deliberately.
        Un-sharing makes the row invisible to the workspace holding the run
        without deleting it, so `_resolve_endpoint` fell through to that
        credential-free snapshot and the next pending row went out
        unauthenticated: a 401 in a workspace that changed nothing.

        Asserting the resolved credential first is the point of the test. The
        refusal is only the fix; what has to stay true is what the executor
        reaches for.
        """
        _, base = await _make_base(session, create_workspace)
        _, a = await create_workspace("A")

        shared_id = (
            await create_endpoint(
                base,
                session,
                name="DGX Spark",
                base_url="http://127.0.0.1:9/v1",
                api_key="sk-shared",
                is_global=True,
            )
        ).id

        base_group = await create_test_group(base, session, name="Baseline")
        await create_test_case(
            base, session, group_id=base_group.id, title="Base case", content="x"
        )
        base_run = await create_run_record(
            base,
            session,
            endpoint_id=shared_id,
            model_id=SHARED_MODEL,
            group_ids=[base_group.id],
            probe=_no_probe,
        )

        a_group = await create_test_group(a, session, name="General")
        await create_test_case(a, session, group_id=a_group.id, title="One", content="x")
        a_run = await create_run_record(
            a,
            session,
            endpoint_id=shared_id,
            model_id=SHARED_MODEL,
            group_ids=[a_group.id],
            probe=_no_probe,
        )

        # What the guard protects: A's executor resolves the live row, key and
        # all, from a scope derived from the run itself.
        found = await scope_for_run(session, a_run.run_id)
        assert found is not None
        resolved = await _resolve_endpoint(found[0], session, found[1])
        assert resolved.api_key == "sk-shared"

        with pytest.raises(
            EndpointInUseError, match="1 unfinished run in another workspace"
        ):
            await update_endpoint(base, session, shared_id, {"is_global": False})

        # Nothing written: the refusal is before the UPDATE, like every other
        # guard in these repositories.
        session.expire_all()
        still = await get_endpoint(base, session, shared_id)
        assert still is not None and still.is_global is True

        # Editing a borrowed-from row is untouched — the guard is on
        # un-sharing, which is the one write that changes who can see it.
        await update_endpoint(base, session, shared_id, {"name": "DGX Spark 2"})
        session.expire_all()
        renamed = await get_endpoint(base, session, shared_id)
        assert renamed is not None and renamed.name == "DGX Spark 2"

        # Base's *own* unfinished run never counts (it is still pending here),
        # and once A's is finished there is nothing left to strand.
        await update_run_status(a, session, a_run.run_id, status="completed")
        await update_endpoint(base, session, shared_id, {"is_global": False})
        session.expire_all()
        unshared = await get_endpoint(base, session, shared_id)
        assert unshared is not None and unshared.is_global is False
        base_still_pending = await list_runs(base, session, run_ids=[base_run.run_id])
        assert [run.status for run in base_still_pending] == ["pending"]
