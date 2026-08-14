"""Endpoints — a base URL and every model ever seen on it.

Reads here ask :func:`~app.scope.where_visible`, so a **global** endpoint (one
the Base workspace shares) shows up from every workspace; writes keep asking
:func:`~app.scope.where_scoped`, which is the whole of "a shared endpoint is
read-only outside Base" — the ``UPDATE`` simply matches no row. Setting
``is_global`` at all is refused outside Base by
:func:`~app.repos.customers.assert_base_workspace`, called from inside both
write functions below so no call site can forget it. *Clearing* it is refused
too while another workspace still has a run to finish against the row — see
:class:`EndpointInUseError`, and `app.repos.toolsets` for the same guard on the
other shareable table.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import Insert, insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Customer, Endpoint, EndpointModel, Run
from app.repos.customers import assert_base_workspace, assert_same_customer
from app.repos.scoped import apply_where, utc_now
from app.scope import Scope, scope_values, where_scoped, where_visible

#: The two run statuses that still have work left for the executor. It is the
#: same vocabulary `update_run_status` writes at the end of a pass — `pending`
#: exactly when result rows remain, `running` while a pass is live or was
#: killed mid-flight and its rows are waiting to be reclaimed.
UNFINISHED_RUN_STATUSES = ("pending", "running")


class EndpointInUseError(Exception):
    """A shared endpoint another workspace still has an unfinished run against.

    Raised by un-sharing, and only by un-sharing. An endpoint's
    `base_url`/`api_key` are read **live** at execution time — deliberately, so
    a moved endpoint does not break Resume — while everything a run *displays*
    was frozen into `endpoint_snapshot` at creation, credentials excluded.
    Clearing `is_global` therefore changes nothing the borrowing workspace's run
    can see and everything it needs: `_resolve_endpoint` stops finding the row,
    falls through to the snapshot, and sends the next pending result out with
    **no API key at all**. A shared endpoint quietly demoted to an
    unauthenticated one, surfacing as a provider 401 in a workspace that
    changed nothing.

    Deleting the row is *not* guarded here, and the difference is the whole
    reason this one is: a deleted endpoint is gone, the snapshot fallback is the
    documented answer to that, and it behaves identically for an endpoint a
    workspace owns itself. Un-sharing alters nothing about the row except who
    can see it, which is exactly why doing it silently is a defect rather than a
    consequence.
    """


async def list_endpoints(
    scope: Scope, session: AsyncSession, order: str = "name"
) -> list[Endpoint]:
    statement = apply_where(select(Endpoint), where_visible(scope, Endpoint))
    statement = statement.order_by(
        Endpoint.created_at.desc() if order == "created" else Endpoint.name.asc()
    )
    return list((await session.scalars(statement)).all())


async def get_endpoint(scope: Scope, session: AsyncSession, endpoint_id: int) -> Endpoint | None:
    statement = apply_where(
        select(Endpoint), where_visible(scope, Endpoint, Endpoint.id == endpoint_id)
    )
    return (await session.scalars(statement)).first()


async def create_endpoint(
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
    is_global: bool = False,
) -> Endpoint:
    if is_global:
        await assert_base_workspace(session, scope, subject="An endpoint")
    endpoint = Endpoint(
        name=name,
        base_url=base_url,
        api_key=api_key,
        cpu=cpu,
        ram=ram,
        gpu=gpu,
        notes=notes,
        is_global=is_global,
        **scope_values(scope),
    )
    session.add(endpoint)
    await session.flush()
    return endpoint


async def update_endpoint(
    scope: Scope, session: AsyncSession, endpoint_id: int, values: Mapping[str, Any]
) -> None:
    """Patches the named columns only; ``updated_at`` follows from the column's
    own ``onupdate``.

    The post-patch value of ``is_global`` is what is checked, which for this one
    column needs no merge: a row that is already global necessarily lives in
    Base, since Base is the only place the flag could have been set, so a patch
    that does not name the column has nothing left to re-check.

    Clearing ``is_global`` is guarded, the way `update_toolset`'s is: un-sharing
    an endpoint another workspace still has a run to finish against strands that
    run behind a row it can no longer see, and the next Resume sends its
    requests out unauthenticated — see :class:`EndpointInUseError`. The refusal
    happens before the ``UPDATE``, so a refused patch writes nothing at all.
    """
    if not values:
        return
    if values.get("is_global"):
        await assert_base_workspace(session, scope, subject="An endpoint")
    elif "is_global" in values:
        await _assert_not_borrowed_elsewhere(scope, session, endpoint_id)
    statement = apply_where(
        update(Endpoint), where_scoped(scope, Endpoint, Endpoint.id == endpoint_id)
    )
    await session.execute(statement.values(**values))


@dataclass(frozen=True)
class EndpointReference:
    """One workspace's unfinished runs against an endpoint, for the refusal."""

    customer_id: int
    customer_name: str
    run_count: int


async def count_unfinished_runs(
    session: AsyncSession, endpoint_id: int
) -> list[EndpointReference]:
    """Which workspaces still have a run to finish against this endpoint.

    Deliberately unscoped, exactly like `count_toolset_references` and
    `find_run_workspace`: the point of the guard is to report damage the
    caller's own workspace cannot see, and a count taken under Base's scope
    would say "0" while stranding someone else's run. It exposes nothing but
    workspace names the switcher already lists for every user.

    Keyed on the run's own status rather than on its ``pending`` result rows,
    because the two are the same fact — the executor sets a run back to
    ``pending`` exactly when result rows remain — and the status is the column
    Resume itself reads.
    """
    rows = await session.execute(
        select(Run.customer_id, Customer.name, func.count(Run.id))
        .select_from(Run)
        .join(Customer, Run.customer_id == Customer.id)
        .where(Run.endpoint_id == endpoint_id, Run.status.in_(UNFINISHED_RUN_STATUSES))
        .group_by(Run.customer_id, Customer.name)
        .order_by(Run.customer_id.asc())
    )
    return [
        EndpointReference(customer_id=customer_id, customer_name=name, run_count=count)
        for customer_id, name, count in rows.all()
    ]


async def _assert_not_borrowed_elsewhere(
    scope: Scope, session: AsyncSession, endpoint_id: int
) -> None:
    """Refuses un-sharing while another workspace still has a run to finish.

    Shaped after `app.repos.toolsets._assert_not_borrowed_elsewhere`, down to
    reading the row through the **strict** predicate first: a workspace that
    merely borrows the endpoint refuses nothing, because its ``UPDATE`` is
    already a no-op, and the early return is also what lets the message below
    name the row.

    Excluding the caller's own workspace is what keeps a local endpoint
    behaving exactly as it always has — its runs are its own, and whoever
    un-shares a row that was never shared strands nobody. A *foreign* run
    against an endpoint can only exist through a global one in the first place
    (`create_run`'s ``allow_global=True`` is the only widening), so on an
    unshared row this costs one query and refuses nothing.
    """
    owned = (
        await session.scalars(
            apply_where(
                select(Endpoint), where_scoped(scope, Endpoint, Endpoint.id == endpoint_id)
            )
        )
    ).first()
    if owned is None:
        # Not this workspace's row: the write is already a no-op under the
        # strict predicate, so there is nothing here to refuse.
        return

    elsewhere = [
        reference
        for reference in await count_unfinished_runs(session, endpoint_id)
        if reference.customer_id != scope.customer_id
    ]
    if not elsewhere:
        return

    held = ", ".join(
        f"{reference.run_count} in {reference.customer_name}" for reference in elsewhere
    )
    total = sum(reference.run_count for reference in elsewhere)
    where = "another workspace" if len(elsewhere) == 1 else "other workspaces"
    raise EndpointInUseError(
        f'The shared endpoint "{owned.name}" still has {total} unfinished '
        f"run{'' if total == 1 else 's'} in {where} ({held}). Those runs read this "
        "endpoint's credentials live at execution time, so let them finish "
        "before un-sharing it."
    )


async def delete_endpoint(scope: Scope, session: AsyncSession, endpoint_id: int) -> None:
    statement = apply_where(
        delete(Endpoint), where_scoped(scope, Endpoint, Endpoint.id == endpoint_id)
    )
    await session.execute(statement)


# ---------------------------------------------------------------------------
# endpoint_models — scope inherited through `endpoint_id`
#
# Inherited through *visibility*, not ownership, and both directions of that
# are deliberate. A global endpoint's model history has to be readable from
# every workspace or the new-run page offers a shared box with no models on it;
# and it has to be *writable* from every workspace, because discovery, a manual
# add and every run all record a sighting, and on shared hardware those runs
# come from whichever engagement booked it. See `EndpointModel`'s docstring:
# one shared box, one shared history, and nothing customer-specific in a row.
# ---------------------------------------------------------------------------


async def list_endpoint_models(
    scope: Scope,
    session: AsyncSession,
    *,
    endpoint_id: int | None = None,
    order: str = "last-seen",
) -> list[EndpointModel]:
    """Every model ever seen, newest sighting first.

    ``loaded-first`` additionally floats the currently loaded ones to the top —
    the "Currently loaded" group on the new-run page depends on that order.
    """
    statement = apply_where(
        select(EndpointModel).join(Endpoint, EndpointModel.endpoint_id == Endpoint.id),
        where_visible(
            scope,
            Endpoint,
            None if endpoint_id is None else EndpointModel.endpoint_id == endpoint_id,
        ),
    )
    if order == "loaded-first":
        statement = statement.order_by(
            EndpointModel.currently_loaded.desc(), EndpointModel.last_seen_at.desc()
        )
    else:
        statement = statement.order_by(EndpointModel.last_seen_at.desc())
    return list((await session.scalars(statement)).all())


async def list_loaded_models(
    scope: Scope, session: AsyncSession, endpoint_id: int
) -> list[EndpointModel]:
    statement = apply_where(
        select(EndpointModel).join(Endpoint, EndpointModel.endpoint_id == Endpoint.id),
        where_visible(
            scope,
            Endpoint,
            EndpointModel.endpoint_id == endpoint_id,
            EndpointModel.currently_loaded.is_(True),
        ),
    )
    return list((await session.scalars(statement)).all())


@dataclass(frozen=True)
class EndpointModelCounts:
    """How many models an endpoint has ever shown, and how many are loaded now."""

    total: int
    loaded: int


async def endpoint_model_counts(
    scope: Scope, session: AsyncSession
) -> dict[int, EndpointModelCounts]:
    statement = apply_where(
        select(
            EndpointModel.endpoint_id,
            func.count(),
            func.count().filter(EndpointModel.currently_loaded.is_(True)),
        ).join(Endpoint, EndpointModel.endpoint_id == Endpoint.id),
        where_visible(scope, Endpoint),
    ).group_by(EndpointModel.endpoint_id)

    rows = await session.execute(statement)
    return {
        endpoint_id: EndpointModelCounts(total=total, loaded=loaded)
        for endpoint_id, total, loaded in rows.all()
    }


def _sighting_upsert(
    *,
    endpoint_id: int,
    model_id: str,
    source: str,
    seen_at: datetime,
    currently_loaded: bool,
    on_conflict: Mapping[str, Any],
) -> Insert:
    """One ``endpoint_models`` sighting, as a real upsert.

    ``ON CONFLICT`` rather than the select-then-branch this replaces, because
    that branch became a lost race the moment an endpoint could be shared: two
    workspaces recording the *first* sighting of a model on the same global box
    both read no row, both insert, and the second commit dies on
    ``uq_endpoint_models_endpoint_id_model_id``. That would take a whole run
    with it, since `create_run_record` writes the sighting inside the same
    transaction as the run row and every one of its result rows — the exact
    atomicity that transaction exists to provide, turned against itself.
    Postgres serialises the two inserters here instead: the loser waits for the
    winner's commit and then runs the update.

    ``index_elements`` names the two columns rather than the constraint, so
    renaming the constraint cannot silently turn this back into a plain insert.

    ``on_conflict`` is spelled out by each caller rather than defaulted, because
    *what survives a re-sighting* is the whole decision being made here and the
    two callers answer it differently.
    """
    return (
        insert(EndpointModel)
        .values(
            endpoint_id=endpoint_id,
            model_id=model_id,
            source=source,
            currently_loaded=currently_loaded,
            first_seen_at=seen_at,
            last_seen_at=seen_at,
        )
        .on_conflict_do_update(
            index_elements=[EndpointModel.endpoint_id, EndpointModel.model_id],
            set_=dict(on_conflict),
        )
    )


async def touch_endpoint_model(
    scope: Scope,
    session: AsyncSession,
    *,
    endpoint_id: int,
    model_id: str,
    source: str,
    at: datetime | None = None,
) -> None:
    """Records that a model was seen on an endpoint.

    A row that already exists only gets its ``last_seen_at`` bumped: ``source``
    says how the model was *first* learned about, and ``currently_loaded``
    belongs to discovery. Nothing is ever deleted from this table.

    The endpoint is checked first because this can *insert*: a child write
    carries its parent's key, and an unchecked key would file the sighting in
    another workspace, where the scoped reads would then happily return it.
    ``allow_global`` because this is where a run records its own model sighting
    — refusing it would make a shared endpoint unusable for a run, which is the
    one thing sharing exists for (see the section banner above).

    That check is also the only scoping the write below needs: it establishes
    the parent is visible from here, and `endpoint_models` inherits its scope
    through exactly that key.
    """
    await assert_same_customer(session, scope, Endpoint, endpoint_id, allow_global=True)
    seen_at = at or utc_now()
    await session.execute(
        _sighting_upsert(
            endpoint_id=endpoint_id,
            model_id=model_id,
            source=source,
            seen_at=seen_at,
            currently_loaded=False,
            # The timestamp and nothing else. `source` is the record of how this
            # model was *first* learned about and `currently_loaded` is
            # discovery's column to write, so a run's sighting overwrites
            # neither — a run against a loaded model must not report it
            # unloaded.
            on_conflict={"last_seen_at": seen_at},
        )
    )


@dataclass(frozen=True)
class DiscoverySync:
    """What one discovery pass changed."""

    discovered: int
    retired: int


async def sync_discovered_models(
    scope: Scope, session: AsyncSession, endpoint_id: int, model_ids: Sequence[str]
) -> DiscoverySync:
    """Applies what ``/v1/models`` just reported for one endpoint.

    Discovered models are upserted and flagged loaded; previously seen models
    that are absent only lose the flag. Rows are never deleted — the history of
    what has run on an endpoint is the point of the table.

    Inserts, so the endpoint has to be one this scope can see (see
    :func:`touch_endpoint_model`, including why a global one counts): the
    new-run page probes the selected endpoint for every role on page load, and
    a shared box that answered that probe with a refusal would be unusable.

    Upserted for the same reason `touch_endpoint_model` is, and it is the same
    race: two workspaces discovering the same shared box at once — which is
    precisely what the new-run page's page-load probe makes routine — would
    both read no row for a newly served model and both insert it. The read
    below survives only because `retired` still needs to know what was loaded
    *before* this pass; it is no longer what decides insert-versus-update.
    """
    await assert_same_customer(session, scope, Endpoint, endpoint_id, allow_global=True)
    seen_at = utc_now()
    existing = await list_endpoint_models(scope, session, endpoint_id=endpoint_id)

    for model_id in model_ids:
        await session.execute(
            _sighting_upsert(
                endpoint_id=endpoint_id,
                model_id=model_id,
                source="discovered",
                seen_at=seen_at,
                currently_loaded=True,
                # `currently_loaded` *is* what this pass reports, so it is
                # written either way; `source` still keeps saying how the model
                # was first learned about, which for a model a run named before
                # discovery ever saw it stays "run".
                on_conflict={"last_seen_at": seen_at, "currently_loaded": True},
            )
        )

    discovered = set(model_ids)
    retired = [
        row.id for row in existing if row.currently_loaded and row.model_id not in discovered
    ]
    if retired:
        await session.execute(
            update(EndpointModel)
            .where(EndpointModel.id.in_(retired))
            .values(currently_loaded=False)
        )

    await session.flush()
    return DiscoverySync(discovered=len(model_ids), retired=len(retired))
