"""Toolsets and the tools they offer."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Tool, Toolset
from app.repos.customers import assert_same_customer
from app.repos.scoped import apply_where, scope_through_parent, utc_now
from app.scope import Scope, combine, scope_values, where_scoped


async def list_toolsets(scope: Scope, session: AsyncSession) -> list[Toolset]:
    statement = apply_where(select(Toolset), where_scoped(scope, Toolset)).order_by(
        Toolset.name.asc()
    )
    return list((await session.scalars(statement)).all())


async def get_toolset(scope: Scope, session: AsyncSession, toolset_id: int) -> Toolset | None:
    statement = apply_where(
        select(Toolset), where_scoped(scope, Toolset, Toolset.id == toolset_id)
    )
    return (await session.scalars(statement)).first()


async def find_toolset_by_name(
    scope: Scope, session: AsyncSession, name: str
) -> list[Toolset]:
    """Every scoped toolset of that name — an ambiguous one is refused, never
    guessed. See :func:`app.repos.prompts.find_prompt_by_name`.
    """
    statement = apply_where(
        select(Toolset),
        where_scoped(scope, Toolset, func.lower(Toolset.name) == func.lower(name)),
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
) -> Toolset:
    toolset = Toolset(
        name=name,
        description=description,
        kind=kind,
        mcp_url=mcp_url,
        mcp_headers=mcp_headers,
        **scope_values(scope),
    )
    session.add(toolset)
    await session.flush()
    return toolset


async def update_toolset(
    scope: Scope, session: AsyncSession, toolset_id: int, values: Mapping[str, Any]
) -> None:
    if not values:
        return
    statement = apply_where(
        update(Toolset), where_scoped(scope, Toolset, Toolset.id == toolset_id)
    )
    await session.execute(statement.values(**values))


async def delete_toolset(scope: Scope, session: AsyncSession, toolset_id: int) -> None:
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
        where_scoped(scope, Toolset, Toolset.id.in_(list(toolset_ids))),
    )
    rows = await session.execute(statement)
    return [
        McpServer(id=row.id, mcp_url=row.mcp_url, mcp_headers=row.mcp_headers)
        for row in rows.all()
    ]


# ---------------------------------------------------------------------------
# tools — scope inherited through `toolset_id`
# ---------------------------------------------------------------------------


async def list_tools(
    scope: Scope, session: AsyncSession, *, toolset_ids: Sequence[int] | None = None
) -> list[Tool]:
    if toolset_ids is not None and not toolset_ids:
        return []
    statement = apply_where(
        select(Tool).join(Toolset, Tool.toolset_id == Toolset.id),
        where_scoped(
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

    Mirrors machine model discovery: rows are upserted and never deleted, so a
    tool that has disappeared from the server is only disabled — a past run can
    still explain what it sent. A hand-written ``mock_response`` survives
    discovery; it is useful for exercising the tool without the server.
    """
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
