"""Toolsets and the tools they offer.

Reads ask :func:`~app.scope.where_visible` so a **global** toolset (one the
Base workspace shares) can be selected by any workspace's test case; writes
keep asking :func:`~app.scope.where_scoped`, which is what makes a shared
toolset read-only outside Base without a permission layer. Setting
``is_global`` is refused anywhere but Base
(:func:`~app.repos.customers.assert_base_workspace`, from inside the two write
functions). *Un*-sharing one and deleting one are both guarded while another
workspace's test cases still select it — see :class:`ToolsetInUseError`.

A ``documents`` toolset's three retrieval tools are `tools` rows like any other,
synthesized here rather than authored or discovered — see
:func:`sync_document_tools`. The corpus those rows read from lives in
:mod:`app.repos.documents`.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Customer, TestCase, TestCaseToolset, TestGroup, Tool, Toolset
from app.repos.customers import assert_base_workspace, assert_same_customer
from app.repos.scoped import apply_where, scope_through_parent, utc_now
from app.scope import Scope, combine, scope_values, where_scoped, where_visible
from app.services.documents import DOCUMENT_TOOLS


class ToolsetInUseError(Exception):
    """A toolset still selected by *another* workspace's test cases.

    Raised because ``test_case_toolsets.toolset_id`` is ``ON DELETE CASCADE``:
    correct while a toolset and its test cases live in one workspace, and
    destructive the moment they do not, since the delete would quietly strip the
    toolset from every engagement's cases with nothing reporting the loss. The
    refusal names the damage instead — how many cases, in which workspaces — the
    same shape `delete_customer`'s guard established.

    The trigger is the **reference set**, never the `is_global` flag, and that
    distinction is the whole guard: keying off the flag left un-sharing as an
    unguarded door straight past it, since clearing `is_global` leaves every
    foreign link in place while making the row invisible to the workspaces
    holding them. Which is why un-sharing raises this too — a toolset that
    disappears from another workspace's editor and takes its links with it on
    the next save is the same silent loss, reached one step earlier.
    """


async def list_toolsets(scope: Scope, session: AsyncSession) -> list[Toolset]:
    statement = apply_where(select(Toolset), where_visible(scope, Toolset)).order_by(
        Toolset.name.asc()
    )
    return list((await session.scalars(statement)).all())


async def get_toolset(scope: Scope, session: AsyncSession, toolset_id: int) -> Toolset | None:
    statement = apply_where(
        select(Toolset), where_visible(scope, Toolset, Toolset.id == toolset_id)
    )
    return (await session.scalars(statement)).first()


async def find_toolset_by_name(
    scope: Scope, session: AsyncSession, name: str
) -> list[Toolset]:
    """Every visible toolset of that name — an ambiguous one is refused, never
    guessed. See :func:`app.repos.prompts.find_prompt_by_name`.

    "Visible" now includes the shared ones, which is also why a global toolset
    sharing a name with a local one is refused rather than resolved: an MCP
    caller naming "Invoice tools" must not silently get whichever of the two
    sorted first.
    """
    statement = apply_where(
        select(Toolset),
        where_visible(scope, Toolset, func.lower(Toolset.name) == func.lower(name)),
    ).order_by(Toolset.id.asc())
    return list((await session.scalars(statement)).all())


async def create_toolset(
    scope: Scope,
    session: AsyncSession,
    *,
    name: str,
    description: str | None = None,
    kind: str = "manual",
    mcp_url: str | None = None,
    mcp_headers: str | None = None,
    is_global: bool = False,
) -> Toolset:
    """Writes a toolset, seeding a ``documents`` one with its three tools.

    The seeding happens here rather than in the route so that no call site can
    create a documents toolset that offers nothing — the API, an MCP tool and a
    test fixture all get the same three rows, the same way `create_tool` and
    `create_test_case` keep their own invariants inside the repository.
    """
    if is_global:
        await assert_base_workspace(session, scope, subject="A toolset")
    toolset = Toolset(
        name=name,
        description=description,
        kind=kind,
        mcp_url=mcp_url,
        mcp_headers=mcp_headers,
        is_global=is_global,
        **scope_values(scope),
    )
    session.add(toolset)
    await session.flush()
    if kind == "documents":
        await sync_document_tools(scope, session, toolset.id)
    return toolset


async def update_toolset(
    scope: Scope, session: AsyncSession, toolset_id: int, values: Mapping[str, Any]
) -> None:
    """The ``is_global`` rule is checked on the post-patch state — see
    :func:`app.repos.endpoints.update_endpoint` for why that needs no merge.

    Clearing ``is_global`` is guarded the same way the delete is: un-sharing a
    toolset another workspace's cases still select would leave those links
    dangling behind a row that workspace can no longer see, which its next save
    of the case turns into a real unlink. Both refusals happen before the
    ``UPDATE``, so a refused patch writes nothing at all.

    A patch that switches ``kind`` to ``documents`` re-asserts the three
    retrieval tools, so a toolset converted after the fact is as usable as one
    created that way. Switching *away* deliberately does nothing: see
    :func:`sync_document_tools` on why the ``enabled`` flag belongs to the human.
    """
    if not values:
        return
    if values.get("is_global"):
        await assert_base_workspace(session, scope, subject="A toolset")
    elif "is_global" in values:
        await _assert_not_borrowed_elsewhere(scope, session, toolset_id, action="un-sharing")
    statement = apply_where(
        update(Toolset), where_scoped(scope, Toolset, Toolset.id == toolset_id)
    )
    result = await session.execute(statement.values(**values))
    # `rowcount` and not just the patch: a write against a row this workspace
    # only borrows is a no-op under the strict predicate, and it has to stay one
    # rather than becoming a refusal from the seeding underneath it.
    if values.get("kind") == "documents" and result.rowcount:
        await sync_document_tools(scope, session, toolset_id)


@dataclass(frozen=True)
class ToolsetReference:
    """One workspace's use of a toolset, for the guard's refusal sentence."""

    customer_id: int
    customer_name: str
    test_case_count: int


async def count_toolset_references(
    session: AsyncSession, toolset_id: int
) -> list[ToolsetReference]:
    """Which workspaces select this toolset, and how many cases in each.

    Deliberately unscoped, like `find_run_workspace`: the point of the guard is
    to report damage the caller's own workspace cannot see, and a refusal that
    only counted the caller's cases would say "0" while cascading away someone
    else's. It exposes nothing but workspace names the switcher already lists
    for every user.
    """
    rows = await session.execute(
        select(TestGroup.customer_id, Customer.name, func.count(TestCaseToolset.test_case_id))
        .select_from(TestCaseToolset)
        .join(TestCase, TestCaseToolset.test_case_id == TestCase.id)
        .join(TestGroup, TestCase.group_id == TestGroup.id)
        .join(Customer, TestGroup.customer_id == Customer.id)
        .where(TestCaseToolset.toolset_id == toolset_id)
        .group_by(TestGroup.customer_id, Customer.name)
        .order_by(TestGroup.customer_id.asc())
    )
    return [
        ToolsetReference(customer_id=customer_id, customer_name=name, test_case_count=count)
        for customer_id, name, count in rows.all()
    ]


async def _assert_not_borrowed_elsewhere(
    scope: Scope, session: AsyncSession, toolset_id: int, *, action: str
) -> None:
    """Refuses a destructive write while another workspace's cases select the row.

    Deliberately keyed on the references rather than on ``is_global``: the flag
    describes what the row is *right now*, and both writes this guards can
    change or remove the row's visibility while the links survive. Asking who
    holds a link is the question that stays true across either.

    The caller's **own** workspace is excluded, which is what keeps a local
    toolset deleting exactly as it always has: its cases are in the same
    workspace, so whoever deletes it can see what they are unlinking. Foreign
    references are only reachable at all through a global toolset
    (`assert_same_customer(..., allow_global=True)` is the only widening), so
    for a row that was never shared this costs one query and refuses nothing.
    """
    owned = (
        await session.scalars(
            apply_where(select(Toolset), where_scoped(scope, Toolset, Toolset.id == toolset_id))
        )
    ).first()
    if owned is None:
        # Not this workspace's row: the write is already a no-op under the
        # strict predicate, so there is nothing here to refuse.
        return

    elsewhere = [
        reference
        for reference in await count_toolset_references(session, toolset_id)
        if reference.customer_id != scope.customer_id
    ]
    if not elsewhere:
        return

    held = ", ".join(
        f"{reference.test_case_count} in {reference.customer_name}" for reference in elsewhere
    )
    total = sum(reference.test_case_count for reference in elsewhere)
    where = "another workspace" if len(elsewhere) == 1 else "other workspaces"
    raise ToolsetInUseError(
        f'The shared toolset "{owned.name}" is still selected by {total} test '
        f"case{'' if total == 1 else 's'} in {where} ({held}). Remove it from those "
        f"test cases before {action} it."
    )


async def delete_toolset(scope: Scope, session: AsyncSession, toolset_id: int) -> None:
    """Deletes a toolset this workspace owns, refusing one still in use elsewhere.

    The guard is `_assert_not_borrowed_elsewhere`, which is also what
    `update_toolset` asks before un-sharing — the two doors onto the same
    cascade, so they answer to the same question.
    """
    await _assert_not_borrowed_elsewhere(scope, session, toolset_id, action="deleting")

    statement = apply_where(
        delete(Toolset), where_scoped(scope, Toolset, Toolset.id == toolset_id)
    )
    await session.execute(statement)


@dataclass(frozen=True)
class McpServer:
    """A toolset's live endpoint.

    Read at execution time, never snapshotted: a server URL and its auth headers
    are credentials, so a moved endpoint must not break a run created before the
    move. The tool *definitions* travel with the run instead.
    """

    id: int
    mcp_url: str | None
    mcp_headers: str | None


async def list_mcp_servers(
    scope: Scope, session: AsyncSession, toolset_ids: Sequence[int]
) -> list[McpServer]:
    if not toolset_ids:
        return []
    statement = apply_where(
        select(Toolset.id, Toolset.mcp_url, Toolset.mcp_headers),
        where_visible(scope, Toolset, Toolset.id.in_(list(toolset_ids))),
    )
    rows = await session.execute(statement)
    return [
        McpServer(id=row.id, mcp_url=row.mcp_url, mcp_headers=row.mcp_headers)
        for row in rows.all()
    ]


# ---------------------------------------------------------------------------
# tools — scope inherited through `toolset_id`
#
# Read through visibility (a shared toolset is useless if its tools are not
# readable), written through ownership: `_tool_where` and `create_tool` keep
# the strict predicate, so a global toolset's tools are authored in Base and
# nowhere else. That asymmetry is the same one the toolset itself has.
# ---------------------------------------------------------------------------


async def list_tools(
    scope: Scope, session: AsyncSession, *, toolset_ids: Sequence[int] | None = None
) -> list[Tool]:
    if toolset_ids is not None and not toolset_ids:
        return []
    statement = apply_where(
        select(Tool).join(Toolset, Tool.toolset_id == Toolset.id),
        where_visible(
            scope,
            Toolset,
            None if toolset_ids is None else Tool.toolset_id.in_(list(toolset_ids)),
        ),
    ).order_by(Tool.name.asc())
    return list((await session.scalars(statement)).all())


async def create_tool(
    scope: Scope,
    session: AsyncSession,
    toolset_id: int,
    *,
    name: str,
    description: str | None = None,
    parameters_json: str = "{}",
    mock_response: str | None = None,
) -> Tool:
    """Writes a hand-authored tool.

    ``tools`` inherits its scope from the toolset it is inserted under, so that
    toolset has to be one this scope can see.

    A unique-violation from the ``(toolset_id, name)`` constraint is deliberately
    allowed to escape: the caller turns it into "this toolset already has a tool
    called …", which needs the original error to recognise it.
    """
    await assert_same_customer(session, scope, Toolset, toolset_id)
    now = utc_now()
    tool = Tool(
        toolset_id=toolset_id,
        name=name,
        description=description,
        parameters_json=parameters_json,
        mock_response=mock_response,
        source="manual",
        enabled=True,
        first_seen_at=now,
        last_seen_at=now,
    )
    session.add(tool)
    await session.flush()
    return tool


async def update_tool(
    scope: Scope, session: AsyncSession, tool_id: int, values: Mapping[str, Any]
) -> None:
    if not values:
        return
    # An explicit `last_seen_at` in the patch wins: discovery sets it itself.
    patch = {"last_seen_at": utc_now(), **values}
    statement = apply_where(update(Tool), _tool_where(scope, tool_id))
    await session.execute(statement.values(**patch))


async def delete_tool(scope: Scope, session: AsyncSession, tool_id: int) -> None:
    await session.execute(apply_where(delete(Tool), _tool_where(scope, tool_id)))


async def set_tool_enabled(
    scope: Scope, session: AsyncSession, tool_id: int, enabled: bool
) -> None:
    statement = apply_where(update(Tool), _tool_where(scope, tool_id))
    await session.execute(statement.values(enabled=enabled))


def _tool_where(scope: Scope, tool_id: int):
    """``tools`` is a child of ``toolsets``, so a write that only knows a tool id
    has to inherit its scope through the toolset it belongs to.
    """
    return combine(
        [
            Tool.id == tool_id,
            scope_through_parent(scope, Tool.toolset_id, Toolset, Toolset.id),
        ]
    )


@dataclass(frozen=True)
class McpToolDescriptor:
    """One tool as an MCP server describes it."""

    name: str
    description: str | None
    parameters_json: str


@dataclass(frozen=True)
class ToolSync:
    """What one discovery pass changed."""

    discovered: int
    retired: int


async def sync_discovered_tools(
    scope: Scope,
    session: AsyncSession,
    toolset_id: int,
    discovered: Sequence[McpToolDescriptor],
) -> ToolSync:
    """Applies what an MCP server just reported for one toolset.

    Mirrors endpoint model discovery: rows are upserted and never deleted, so a
    tool that has disappeared from the server is only disabled — a past run can
    still explain what it sent. A hand-written ``mock_response`` survives
    discovery; it is useful for exercising the tool without the server.

    Ownership-checked, not visibility-checked, and without ``allow_global``:
    the loop below writes ``tools`` rows by id, which is the one path that would
    otherwise slip past `_tool_where`'s strict predicate and let a workspace
    rewrite a *shared* toolset's definitions. Discovery is a write here, even
    though it looks like a probe.
    """
    await assert_same_customer(session, scope, Toolset, toolset_id)
    now = utc_now()
    existing = await list_tools(scope, session, toolset_ids=[toolset_id])
    by_name = {row.name: row for row in existing}

    for tool in discovered:
        row = by_name.get(tool.name)
        values = {
            "description": tool.description,
            "parameters_json": tool.parameters_json,
            "enabled": True,
            "last_seen_at": now,
        }
        if row is not None:
            await session.execute(update(Tool).where(Tool.id == row.id).values(**values))
        else:
            session.add(
                Tool(
                    toolset_id=toolset_id,
                    name=tool.name,
                    source="mcp",
                    first_seen_at=now,
                    **values,
                )
            )

    seen = {tool.name for tool in discovered}
    retired = [
        row.id for row in existing if row.source == "mcp" and row.enabled and row.name not in seen
    ]
    if retired:
        await session.execute(
            update(Tool).where(Tool.id.in_(retired)).values(enabled=False)
        )

    await session.flush()
    return ToolSync(discovered=len(discovered), retired=len(retired))


# ---------------------------------------------------------------------------
# documents — the three synthesized tools
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DocumentToolSync:
    """What one re-assertion of the three document tools changed."""

    created: int
    refreshed: int


async def sync_document_tools(
    scope: Scope, session: AsyncSession, toolset_id: int
) -> DocumentToolSync:
    """Asserts that a documents toolset offers exactly the three retrieval tools.

    They are **real `tools` rows**, and that is the entire design: because they
    are rows, `assert_tool_config`'s collision and "no enabled tools" checks,
    `tools_snapshot`, the toolset detail UI and the `enabled` flag all cover them
    with no code learning a new case. What makes them different from every other
    row is only where they come from — neither hand-authored like a `manual` tool
    nor reported by a server like an `mcp` one, but *synthesized* from
    `app.services.documents.DOCUMENT_TOOLS`, so every corpus in every engagement
    offers the same three functions with the same descriptions and the same
    schemas. A retrieval measurement that could be explained by a differently
    worded tool description would measure the description instead of the model.

    Called from `create_toolset` (and from `update_toolset` when a toolset is
    converted), so a documents toolset never exists without them; exposed as
    well, because a build that improves a description needs a way to push it
    onto corpora that already exist.

    **It never touches `enabled` on a row that already exists.** Disabling
    `search_documents` and leaving the model to navigate by `list_documents` and
    `read_document` alone is one of the more interesting things this feature can
    measure, and a sync that helpfully re-enabled it would silently destroy that
    test case. Only rows this call creates start out enabled.

    Ownership-checked without ``allow_global``, exactly like
    `sync_discovered_tools`: writing `tools` rows by id is the path that would
    otherwise let a borrowing workspace rewrite a shared toolset's definitions.
    """
    await assert_same_customer(session, scope, Toolset, toolset_id)
    now = utc_now()
    by_name = {
        row.name: row for row in await list_tools(scope, session, toolset_ids=[toolset_id])
    }

    created = 0
    refreshed = 0
    for tool in DOCUMENT_TOOLS:
        row = by_name.get(tool.name)
        if row is None:
            session.add(
                Tool(
                    toolset_id=toolset_id,
                    name=tool.name,
                    description=tool.description,
                    parameters_json=tool.parameters_json,
                    mock_response=None,
                    enabled=True,
                    source="documents",
                    first_seen_at=now,
                    last_seen_at=now,
                )
            )
            created += 1
            continue

        await session.execute(
            update(Tool)
            .where(Tool.id == row.id)
            .values(
                description=tool.description,
                parameters_json=tool.parameters_json,
                # A row that was authored by hand under one of these three names
                # (or discovered under it) becomes the synthesized one: two
                # answers to "what does read_document do" is the state nothing
                # downstream can resolve, and the corpus is the truthful one.
                source="documents",
                mock_response=None,
                last_seen_at=now,
            )
        )
        refreshed += 1

    await session.flush()
    return DocumentToolSync(created=created, refreshed=refreshed)
