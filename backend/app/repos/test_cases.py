"""Test groups, test cases, and which toolsets a test case pulls in.

Both children inherit their workspace: a test case through its group, a link row
through both ends at once. The three cross-root references a test case can hold
— its group, its prompt, its toolsets — are checked with
:func:`~app.repos.customers.assert_same_customer` from inside these functions,
because the database cannot express them.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Prompt, TestCase, TestCaseToolset, TestGroup, Tool, Toolset
from app.repos.customers import assert_same_customer
from app.repos.scoped import apply_where, scope_through_parent
from app.scope import (
    CrossCustomerError,
    Scope,
    combine,
    scope_values,
    where_scoped,
)

# ---------------------------------------------------------------------------
# Test groups
# ---------------------------------------------------------------------------


async def list_test_groups(
    scope: Scope, session: AsyncSession, order: str = "sort-name"
) -> list[TestGroup]:
    statement = apply_where(select(TestGroup), where_scoped(scope, TestGroup))
    statement = statement.order_by(
        TestGroup.sort_order.asc(),
        TestGroup.id.asc() if order == "sort-id" else TestGroup.name.asc(),
    )
    return list((await session.scalars(statement)).all())


async def get_test_group(
    scope: Scope, session: AsyncSession, group_id: int
) -> TestGroup | None:
    statement = apply_where(
        select(TestGroup), where_scoped(scope, TestGroup, TestGroup.id == group_id)
    )
    return (await session.scalars(statement)).first()


async def list_test_groups_by_ids(
    scope: Scope, session: AsyncSession, group_ids: Sequence[int]
) -> list[TestGroup]:
    if not group_ids:
        return []
    statement = apply_where(
        select(TestGroup),
        where_scoped(scope, TestGroup, TestGroup.id.in_(list(group_ids))),
    ).order_by(TestGroup.sort_order.asc(), TestGroup.id.asc())
    return list((await session.scalars(statement)).all())


async def find_test_group_by_name(
    scope: Scope, session: AsyncSession, name: str
) -> list[TestGroup]:
    """Every scoped group of that name.

    Two callers need it: MCP's name-idempotent ``create_test_group`` (pushing a
    suite twice must not duplicate it) and ref resolution, which refuses an
    ambiguous name rather than guessing.
    """
    statement = apply_where(
        select(TestGroup),
        where_scoped(scope, TestGroup, func.lower(TestGroup.name) == func.lower(name)),
    ).order_by(TestGroup.id.asc())
    return list((await session.scalars(statement)).all())


async def create_test_group(
    scope: Scope,
    session: AsyncSession,
    *,
    name: str,
    description: str | None = None,
    sort_order: int = 0,
) -> TestGroup:
    group = TestGroup(
        name=name, description=description, sort_order=sort_order, **scope_values(scope)
    )
    session.add(group)
    await session.flush()
    return group


async def update_test_group(
    scope: Scope, session: AsyncSession, group_id: int, values: Mapping[str, Any]
) -> None:
    if not values:
        return
    statement = apply_where(
        update(TestGroup), where_scoped(scope, TestGroup, TestGroup.id == group_id)
    )
    await session.execute(statement.values(**values))


async def delete_test_group(scope: Scope, session: AsyncSession, group_id: int) -> None:
    statement = apply_where(
        delete(TestGroup), where_scoped(scope, TestGroup, TestGroup.id == group_id)
    )
    await session.execute(statement)


async def test_case_counts_by_group(scope: Scope, session: AsyncSession) -> dict[int, int]:
    statement = apply_where(
        select(TestCase.group_id, func.count()).join(
            TestGroup, TestCase.group_id == TestGroup.id
        ),
        where_scoped(scope, TestGroup),
    ).group_by(TestCase.group_id)
    rows = await session.execute(statement)
    return {group_id: count for group_id, count in rows.all()}


async def count_test_cases(scope: Scope, session: AsyncSession) -> int:
    statement = apply_where(
        select(func.count())
        .select_from(TestCase)
        .join(TestGroup, TestCase.group_id == TestGroup.id),
        where_scoped(scope, TestGroup),
    )
    return await session.scalar(statement) or 0


# ---------------------------------------------------------------------------
# Test cases — scope inherited through `group_id`
# ---------------------------------------------------------------------------


async def list_test_cases(
    scope: Scope,
    session: AsyncSession,
    *,
    group_id: int | None = None,
    group_ids: Sequence[int] | None = None,
) -> list[TestCase]:
    """Test cases, in run order.

    Narrowed to one group or a set of groups it is ``sort_order, id`` — the
    order a run materializes its results in. Unnarrowed it additionally groups
    by ``group_id`` first.
    """
    if group_ids is not None and not group_ids:
        return []

    if group_id is not None:
        narrowed = TestCase.group_id == group_id
    elif group_ids is not None:
        narrowed = TestCase.group_id.in_(list(group_ids))
    else:
        narrowed = None

    statement = apply_where(
        select(TestCase).join(TestGroup, TestCase.group_id == TestGroup.id),
        where_scoped(scope, TestGroup, narrowed),
    )
    if narrowed is None:
        statement = statement.order_by(TestCase.group_id.asc())
    statement = statement.order_by(TestCase.sort_order.asc(), TestCase.id.asc())
    return list((await session.scalars(statement)).all())


async def get_test_case(
    scope: Scope, session: AsyncSession, test_case_id: int
) -> TestCase | None:
    statement = apply_where(
        select(TestCase).join(TestGroup, TestCase.group_id == TestGroup.id),
        where_scoped(scope, TestGroup, TestCase.id == test_case_id),
    )
    return (await session.scalars(statement)).first()


async def create_test_case(
    scope: Scope,
    session: AsyncSession,
    *,
    group_id: int,
    title: str,
    content: str,
    expected_output: str | None = None,
    prompt_id: int | None = None,
    mode: str = "append",
    custom_text: str | None = None,
    tool_mode: str = "none",
    tool_choice: str | None = None,
    max_turns: int = 6,
    sort_order: int = 0,
) -> TestCase:
    """Creates a test case under a group this scope can see.

    A guessed group id would otherwise file a case in someone else's workspace,
    where every later read would then find it. The referenced prompt is the
    second cross-root reference and gets the same treatment — it is what run
    creation later resolves and freezes into every result row.
    """
    await assert_same_customer(session, scope, TestGroup, group_id)
    if prompt_id is not None:
        await assert_same_customer(session, scope, Prompt, prompt_id)

    test_case = TestCase(
        group_id=group_id,
        title=title,
        content=content,
        expected_output=expected_output,
        prompt_id=prompt_id,
        mode=mode,
        custom_text=custom_text,
        tool_mode=tool_mode,
        tool_choice=tool_choice,
        max_turns=max_turns,
        sort_order=sort_order,
    )
    session.add(test_case)
    await session.flush()
    return test_case


async def update_test_case(
    scope: Scope, session: AsyncSession, test_case_id: int, values: Mapping[str, Any]
) -> None:
    """Patches the named columns only — the same two cross-root references are
    checked, but only when the patch actually names them.
    """
    if not values:
        return
    if values.get("group_id") is not None:
        await assert_same_customer(session, scope, TestGroup, values["group_id"])
    if values.get("prompt_id") is not None:
        await assert_same_customer(session, scope, Prompt, values["prompt_id"])

    statement = apply_where(update(TestCase), _test_case_where(scope, test_case_id))
    await session.execute(statement.values(**values))


async def delete_test_case(scope: Scope, session: AsyncSession, test_case_id: int) -> None:
    await session.execute(apply_where(delete(TestCase), _test_case_where(scope, test_case_id)))


def _test_case_where(scope: Scope, test_case_id: int):
    """``test_cases`` is a child of ``test_groups``, so a write that only knows a
    case id inherits its scope through the group it belongs to.
    """
    return combine(
        [
            TestCase.id == test_case_id,
            scope_through_parent(scope, TestCase.group_id, TestGroup, TestGroup.id),
        ]
    )


@dataclass(frozen=True)
class CompareTestCaseRow:
    """A live test case joined to its group — the rows of ``/results`` in model
    mode.
    """

    id: int
    group_id: int
    group_name: str
    title: str
    text: str


async def compare_test_case_rows(
    scope: Scope, session: AsyncSession
) -> list[CompareTestCaseRow]:
    statement = apply_where(
        select(
            TestCase.id,
            TestCase.group_id,
            TestGroup.name,
            TestCase.title,
            TestCase.content,
        ).join(TestGroup, TestCase.group_id == TestGroup.id),
        where_scoped(scope, TestGroup),
    ).order_by(
        TestGroup.sort_order.asc(),
        TestGroup.name.asc(),
        TestCase.sort_order.asc(),
        TestCase.id.asc(),
    )
    rows = await session.execute(statement)
    return [
        CompareTestCaseRow(
            id=row[0], group_id=row[1], group_name=row[2], title=row[3], text=row[4]
        )
        for row in rows.all()
    ]


# ---------------------------------------------------------------------------
# test_case_toolsets — a link row inherits scope through *both* ends
# ---------------------------------------------------------------------------


async def replace_toolset_links(
    scope: Scope, session: AsyncSession, test_case_id: int, toolset_ids: Sequence[int]
) -> None:
    """Replaces a test case's toolset links.

    Rewriting the set is simpler than diffing it, and the table holds a handful
    of rows per case. The link order is the caller's order.

    A link row has no ``customer_id`` of its own, so this is the only place the
    pairing can be checked: both ends have to be in the caller's workspace.
    """
    if await get_test_case(scope, session, test_case_id) is None:
        raise CrossCustomerError(
            f"The selected test case (id {test_case_id}) no longer exists in this workspace."
        )
    await assert_same_customer(session, scope, Toolset, toolset_ids)

    await session.execute(
        delete(TestCaseToolset).where(TestCaseToolset.test_case_id == test_case_id)
    )
    if not toolset_ids:
        return
    await session.execute(
        insert(TestCaseToolset),
        [
            {"test_case_id": test_case_id, "toolset_id": toolset_id, "sort_order": index}
            for index, toolset_id in enumerate(toolset_ids)
        ],
    )


async def list_toolset_links(
    scope: Scope, session: AsyncSession, test_case_ids: Sequence[int] | None = None
) -> list[TestCaseToolset]:
    if test_case_ids is not None and not test_case_ids:
        return []
    statement = apply_where(
        select(TestCaseToolset)
        .join(TestCase, TestCaseToolset.test_case_id == TestCase.id)
        .join(TestGroup, TestCase.group_id == TestGroup.id),
        where_scoped(
            scope,
            TestGroup,
            None
            if test_case_ids is None
            else TestCaseToolset.test_case_id.in_(list(test_case_ids)),
        ),
    ).order_by(TestCaseToolset.test_case_id.asc(), TestCaseToolset.sort_order.asc())
    return list((await session.scalars(statement)).all())


@dataclass(frozen=True)
class TestCaseToolsetView:
    """A test case's toolsets, named — what the editor and the MCP views show."""

    test_case_id: int
    toolset_id: int
    name: str
    kind: str
    sort_order: int


async def list_test_case_toolset_views(
    scope: Scope, session: AsyncSession, test_case_ids: Sequence[int]
) -> list[TestCaseToolsetView]:
    if not test_case_ids:
        return []
    statement = apply_where(
        select(
            TestCaseToolset.test_case_id,
            Toolset.id,
            Toolset.name,
            Toolset.kind,
            TestCaseToolset.sort_order,
        ).join(Toolset, TestCaseToolset.toolset_id == Toolset.id),
        where_scoped(
            scope, Toolset, TestCaseToolset.test_case_id.in_(list(test_case_ids))
        ),
    ).order_by(TestCaseToolset.test_case_id.asc(), TestCaseToolset.sort_order.asc())
    rows = await session.execute(statement)
    return [
        TestCaseToolsetView(
            test_case_id=row[0],
            toolset_id=row[1],
            name=row[2],
            kind=row[3],
            sort_order=row[4],
        )
        for row in rows.all()
    ]


@dataclass(frozen=True)
class SnapshotToolRow:
    """One tool as it will be frozen into a run."""

    test_case_id: int
    toolset_id: int
    toolset_name: str
    tool_name: str
    description: str | None
    parameters_json: str
    mock_response: str | None
    enabled: bool
    source: str


async def list_snapshot_tool_rows(
    scope: Scope, session: AsyncSession, test_case_ids: Sequence[int]
) -> list[SnapshotToolRow]:
    """The test case -> toolset -> tool traversal run creation snapshots from.

    Scoped on ``toolsets``, the tools' own parent, which is what closes the
    cross-workspace path: a case linked to a foreign toolset contributes no
    tools at all, and the "no enabled tools" refusal then fires instead of a
    foreign definition reaching the model.

    Ordered so a run's frozen tool list is reproducible: by test case, then by
    the order the case lists its toolsets in, then by tool name.
    """
    if not test_case_ids:
        return []
    statement = apply_where(
        select(
            TestCaseToolset.test_case_id,
            Toolset.id,
            Toolset.name,
            Tool.name,
            Tool.description,
            Tool.parameters_json,
            Tool.mock_response,
            Tool.enabled,
            Tool.source,
        )
        .join(Toolset, TestCaseToolset.toolset_id == Toolset.id)
        .join(Tool, Tool.toolset_id == Toolset.id),
        where_scoped(
            scope, Toolset, TestCaseToolset.test_case_id.in_(list(test_case_ids))
        ),
    ).order_by(
        TestCaseToolset.test_case_id.asc(),
        TestCaseToolset.sort_order.asc(),
        Toolset.id.asc(),
        Tool.name.asc(),
    )
    rows = await session.execute(statement)
    return [
        SnapshotToolRow(
            test_case_id=row[0],
            toolset_id=row[1],
            toolset_name=row[2],
            tool_name=row[3],
            description=row[4],
            parameters_json=row[5],
            mock_response=row[6],
            enabled=row[7],
            source=row[8],
        )
        for row in rows.all()
    ]
