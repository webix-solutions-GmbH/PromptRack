"""Parameter groups — named request-param presets, workspace-scoped.

The one rule beyond plain scoped CRUD lives in `app.services.params`: a group's
`params` must pass ``validate_params(value, allow_null_values=True)`` (a group
is a patch that may unset an endpoint default), and the API layer calls it
before anything reaches these functions — the same split `message_assembly` and
`attribution` draw between pure rules and scoped reads.

Nothing here is referenced by FK from a run: run creation reads the selected
groups once, merges them, and freezes the merged params and the group *names*
onto the run — so editing or deleting a group never changes what a past run
sent or displays.
"""

from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ParamGroup
from app.repos.scoped import apply_where
from app.scope import Scope, scope_values, where_scoped


async def list_param_groups(scope: Scope, session: AsyncSession) -> list[ParamGroup]:
    statement = apply_where(select(ParamGroup), where_scoped(scope, ParamGroup)).order_by(
        ParamGroup.name.asc(), ParamGroup.id.asc()
    )
    return list((await session.scalars(statement)).all())


async def get_param_group(
    scope: Scope, session: AsyncSession, param_group_id: int
) -> ParamGroup | None:
    statement = apply_where(
        select(ParamGroup),
        where_scoped(scope, ParamGroup, ParamGroup.id == param_group_id),
    )
    return (await session.scalars(statement)).first()


async def list_param_groups_by_ids(
    scope: Scope, session: AsyncSession, param_group_ids: Sequence[int]
) -> list[ParamGroup]:
    """The selected groups, in the caller's selection order.

    A scoped read, so an id from another workspace simply comes back missing —
    run creation compares the count and refuses by id, the same way it treats a
    vanished test group.
    """
    if not param_group_ids:
        return []
    statement = apply_where(
        select(ParamGroup),
        where_scoped(scope, ParamGroup, ParamGroup.id.in_(list(param_group_ids))),
    )
    rows = {row.id: row for row in (await session.scalars(statement)).all()}
    return [rows[group_id] for group_id in param_group_ids if group_id in rows]


async def find_param_group_by_name(
    scope: Scope, session: AsyncSession, name: str
) -> list[ParamGroup]:
    """Every scoped group of that name — MCP ref resolution refuses an
    ambiguous name rather than guessing, so this returns the list.
    """
    statement = apply_where(
        select(ParamGroup),
        where_scoped(scope, ParamGroup, func.lower(ParamGroup.name) == func.lower(name)),
    ).order_by(ParamGroup.id.asc())
    return list((await session.scalars(statement)).all())


async def create_param_group(
    scope: Scope,
    session: AsyncSession,
    *,
    name: str,
    description: str | None = None,
    params: str,
) -> ParamGroup:
    group = ParamGroup(
        name=name, description=description, params=params, **scope_values(scope)
    )
    session.add(group)
    await session.flush()
    return group


async def update_param_group(
    scope: Scope, session: AsyncSession, param_group_id: int, values: Mapping[str, Any]
) -> None:
    if not values:
        return
    statement = apply_where(
        update(ParamGroup),
        where_scoped(scope, ParamGroup, ParamGroup.id == param_group_id),
    )
    await session.execute(statement.values(**values))


async def delete_param_group(
    scope: Scope, session: AsyncSession, param_group_id: int
) -> None:
    statement = apply_where(
        delete(ParamGroup),
        where_scoped(scope, ParamGroup, ParamGroup.id == param_group_id),
    )
    await session.execute(statement)
