"""Machines (endpoints) and the models ever seen on them."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Machine, MachineModel
from app.repos.customers import assert_same_customer
from app.repos.scoped import apply_where, utc_now
from app.scope import Scope, scope_values, where_scoped


async def list_machines(
    scope: Scope, session: AsyncSession, order: str = "name"
) -> list[Machine]:
    statement = apply_where(select(Machine), where_scoped(scope, Machine))
    statement = statement.order_by(
        Machine.created_at.desc() if order == "created" else Machine.name.asc()
    )
    return list((await session.scalars(statement)).all())


async def get_machine(scope: Scope, session: AsyncSession, machine_id: int) -> Machine | None:
    statement = apply_where(
        select(Machine), where_scoped(scope, Machine, Machine.id == machine_id)
    )
    return (await session.scalars(statement)).first()


async def create_machine(
    scope: Scope,
    session: AsyncSession,
    *,
    name: str,
    base_url: str,
    api_key: str | None = None,
    cpu: str | None = None,
    ram: str | None = None,
    gpu: str | None = None,
    notes: str | None = None,
) -> Machine:
    machine = Machine(
        name=name,
        base_url=base_url,
        api_key=api_key,
        cpu=cpu,
        ram=ram,
        gpu=gpu,
        notes=notes,
        **scope_values(scope),
    )
    session.add(machine)
    await session.flush()
    return machine


async def update_machine(
    scope: Scope, session: AsyncSession, machine_id: int, values: Mapping[str, Any]
) -> None:
    """Patches the named columns only; ``updated_at`` follows from the column's
    own ``onupdate``.
    """
    if not values:
        return
    statement = apply_where(
        update(Machine), where_scoped(scope, Machine, Machine.id == machine_id)
    )
    await session.execute(statement.values(**values))


async def delete_machine(scope: Scope, session: AsyncSession, machine_id: int) -> None:
    statement = apply_where(
        delete(Machine), where_scoped(scope, Machine, Machine.id == machine_id)
    )
    await session.execute(statement)


# ---------------------------------------------------------------------------
# machine_models — scope inherited through `machine_id`
# ---------------------------------------------------------------------------


async def list_machine_models(
    scope: Scope,
    session: AsyncSession,
    *,
    machine_id: int | None = None,
    order: str = "last-seen",
) -> list[MachineModel]:
    """Every model ever seen, newest sighting first.

    ``loaded-first`` additionally floats the currently loaded ones to the top —
    the "Currently loaded" group on the new-run page depends on that order.
    """
    statement = apply_where(
        select(MachineModel).join(Machine, MachineModel.machine_id == Machine.id),
        where_scoped(
            scope,
            Machine,
            None if machine_id is None else MachineModel.machine_id == machine_id,
        ),
    )
    if order == "loaded-first":
        statement = statement.order_by(
            MachineModel.currently_loaded.desc(), MachineModel.last_seen_at.desc()
        )
    else:
        statement = statement.order_by(MachineModel.last_seen_at.desc())
    return list((await session.scalars(statement)).all())


async def list_loaded_models(
    scope: Scope, session: AsyncSession, machine_id: int
) -> list[MachineModel]:
    statement = apply_where(
        select(MachineModel).join(Machine, MachineModel.machine_id == Machine.id),
        where_scoped(
            scope,
            Machine,
            MachineModel.machine_id == machine_id,
            MachineModel.currently_loaded.is_(True),
        ),
    )
    return list((await session.scalars(statement)).all())


@dataclass(frozen=True)
class MachineModelCounts:
    """How many models a machine has ever shown, and how many are loaded now."""

    total: int
    loaded: int


async def machine_model_counts(
    scope: Scope, session: AsyncSession
) -> dict[int, MachineModelCounts]:
    statement = apply_where(
        select(
            MachineModel.machine_id,
            func.count(),
            func.count().filter(MachineModel.currently_loaded.is_(True)),
        ).join(Machine, MachineModel.machine_id == Machine.id),
        where_scoped(scope, Machine),
    ).group_by(MachineModel.machine_id)

    rows = await session.execute(statement)
    return {
        machine_id: MachineModelCounts(total=total, loaded=loaded)
        for machine_id, total, loaded in rows.all()
    }


async def touch_machine_model(
    scope: Scope,
    session: AsyncSession,
    *,
    machine_id: int,
    model_id: str,
    source: str,
    at: datetime | None = None,
) -> None:
    """Records that a model was seen on a machine.

    A row that already exists only gets its ``last_seen_at`` bumped: ``source``
    says how the model was *first* learned about, and ``currently_loaded``
    belongs to discovery. Nothing is ever deleted from this table.

    The machine is checked first because this can *insert*: a child write
    carries its parent's key, and an unchecked key would file the sighting in
    another workspace, where the scoped reads would then happily return it.
    """
    await assert_same_customer(session, scope, Machine, machine_id)
    seen_at = at or utc_now()
    statement = apply_where(
        select(MachineModel.id).join(Machine, MachineModel.machine_id == Machine.id),
        where_scoped(
            scope,
            Machine,
            MachineModel.machine_id == machine_id,
            MachineModel.model_id == model_id,
        ),
    )
    existing = (await session.scalars(statement)).first()

    if existing is not None:
        await session.execute(
            update(MachineModel)
            .where(MachineModel.id == existing)
            .values(last_seen_at=seen_at)
        )
        return

    session.add(
        MachineModel(
            machine_id=machine_id,
            model_id=model_id,
            source=source,
            currently_loaded=False,
            first_seen_at=seen_at,
            last_seen_at=seen_at,
        )
    )
    await session.flush()


@dataclass(frozen=True)
class DiscoverySync:
    """What one discovery pass changed."""

    discovered: int
    retired: int


async def sync_discovered_models(
    scope: Scope, session: AsyncSession, machine_id: int, model_ids: Sequence[str]
) -> DiscoverySync:
    """Applies what ``/v1/models`` just reported for one machine.

    Discovered models are upserted and flagged loaded; previously seen models
    that are absent only lose the flag. Rows are never deleted — the history of
    what has run on a machine is the point of the table.

    Inserts, so the machine has to be one this scope can see (see
    :func:`touch_machine_model`).
    """
    await assert_same_customer(session, scope, Machine, machine_id)
    seen_at = utc_now()
    existing = await list_machine_models(scope, session, machine_id=machine_id)
    by_model_id = {row.model_id: row for row in existing}

    for model_id in model_ids:
        row = by_model_id.get(model_id)
        if row is not None:
            await session.execute(
                update(MachineModel)
                .where(MachineModel.id == row.id)
                .values(last_seen_at=seen_at, currently_loaded=True)
            )
        else:
            session.add(
                MachineModel(
                    machine_id=machine_id,
                    model_id=model_id,
                    source="discovered",
                    currently_loaded=True,
                    first_seen_at=seen_at,
                    last_seen_at=seen_at,
                )
            )

    discovered = set(model_ids)
    retired = [
        row.id for row in existing if row.currently_loaded and row.model_id not in discovered
    ]
    if retired:
        await session.execute(
            update(MachineModel)
            .where(MachineModel.id.in_(retired))
            .values(currently_loaded=False)
        )

    await session.flush()
    return DiscoverySync(discovered=len(model_ids), retired=len(retired))
