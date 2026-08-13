"""Prompts — the versioned asset.

Only the prompt row itself lives here: ``prompts.content`` is the mutable
draft, and every read or write of it is a plain scoped root-table query. The
history (``prompt_versions``), the commit rule and the deployed/baseline
pointers live in :mod:`app.repos.prompt_versions`, which is where the
invariants that make a version immutable are worth reading in one place.
"""

from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Prompt
from app.repos.scoped import apply_where
from app.scope import Scope, scope_values, where_scoped


async def list_prompts(
    scope: Scope, session: AsyncSession, order: str = "name"
) -> list[Prompt]:
    statement = apply_where(select(Prompt), where_scoped(scope, Prompt))
    statement = statement.order_by(
        Prompt.updated_at.desc() if order == "updated" else Prompt.name.asc()
    )
    return list((await session.scalars(statement)).all())


async def get_prompt(scope: Scope, session: AsyncSession, prompt_id: int) -> Prompt | None:
    statement = apply_where(select(Prompt), where_scoped(scope, Prompt, Prompt.id == prompt_id))
    return (await session.scalars(statement)).first()


async def list_prompts_by_ids(
    scope: Scope, session: AsyncSession, prompt_ids: Sequence[int]
) -> list[Prompt]:
    """The named prompts, for building a lookup map.

    An empty id list answers without querying — ``IN ()`` is a pointless round
    trip.
    """
    if not prompt_ids:
        return []
    statement = apply_where(
        select(Prompt), where_scoped(scope, Prompt, Prompt.id.in_(list(prompt_ids)))
    )
    return list((await session.scalars(statement)).all())


async def find_prompt_by_name(
    scope: Scope, session: AsyncSession, name: str
) -> list[Prompt]:
    """Prompts whose name matches case-insensitively, inside the scope.

    Returns every match rather than one: a caller that relates a prompt by name
    (MCP does) must refuse an ambiguous name instead of guessing, and name
    resolution being scoped is what keeps it from ever reaching another
    workspace's row.
    """
    statement = apply_where(
        select(Prompt),
        where_scoped(scope, Prompt, func.lower(Prompt.name) == func.lower(name)),
    ).order_by(Prompt.id.asc())
    return list((await session.scalars(statement)).all())


async def create_prompt(
    scope: Scope, session: AsyncSession, *, name: str, content: str
) -> Prompt:
    """Creates a prompt with its draft content. It has no versions until the
    first explicit commit — an uncommitted prompt is a dirty working tree.
    """
    prompt = Prompt(name=name, content=content, **scope_values(scope))
    session.add(prompt)
    await session.flush()
    return prompt


async def update_prompt(
    scope: Scope, session: AsyncSession, prompt_id: int, values: Mapping[str, Any]
) -> None:
    """Patches the named columns only.

    This is how the draft is edited and, from :mod:`app.repos.prompt_versions`,
    how the deployed pointer is moved — the cross-reference checks that pointer
    needs belong there, not to every caller of this function.
    """
    if not values:
        return
    statement = apply_where(update(Prompt), where_scoped(scope, Prompt, Prompt.id == prompt_id))
    await session.execute(statement.values(**values))


async def delete_prompt(scope: Scope, session: AsyncSession, prompt_id: int) -> None:
    """Deletes the asset and, by cascade, its whole history.

    Past runs are unaffected: they carry their own frozen copy of the effective
    system prompt, and ``run_results.prompt_version_id`` is ``SET NULL``.
    """
    statement = apply_where(delete(Prompt), where_scoped(scope, Prompt, Prompt.id == prompt_id))
    await session.execute(statement)
