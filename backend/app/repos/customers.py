"""Customer workspaces.

The one family of queries that is *about* workspaces rather than inside one, so
these functions take no :class:`~app.scope.Scope`: a scope is derived *from* a
customer, and asking "which workspaces exist" under one workspace's scope would
be circular. Authorization is the role gate at the API boundary — every
signed-in user may see and switch into every workspace, which is what "a
workspace is a label, not a tenant" means.

:func:`assert_same_customer` lives here too, because it is the one check the
database cannot make.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Select, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Customer, Machine, Prompt, Run, TestCase, TestGroup, Toolset
from app.scope import (
    CrossCustomerError,
    CustomerOption,
    Scope,
    ScopedRoot,
    require_customer_id,
)


async def list_customers(session: AsyncSession) -> list[Customer]:
    """Every workspace, oldest first — the order the first one is created in."""
    rows = await session.scalars(select(Customer).order_by(Customer.id.asc()))
    return list(rows.all())


async def list_customer_options(session: AsyncSession) -> list[CustomerOption]:
    """The switcher's view: id, name, and whether it is hidden."""
    rows = await session.execute(
        select(Customer.id, Customer.name, Customer.archived_at).order_by(Customer.id.asc())
    )
    return [
        CustomerOption(id=row.id, name=row.name, archived=row.archived_at is not None)
        for row in rows.all()
    ]


async def get_customer(session: AsyncSession, customer_id: int) -> Customer | None:
    return await session.get(Customer, customer_id)


async def find_customer_by_name(
    session: AsyncSession, name: str, except_id: int | None = None
) -> Customer | None:
    """A workspace whose name matches case-insensitively, ignoring one id.

    The unique index would refuse a duplicate anyway; this exists so the caller
    can name the workspace that is in the way instead of surfacing a constraint
    violation.
    """
    statement: Select[tuple[Customer]] = select(Customer).where(
        func.lower(Customer.name) == func.lower(name)
    )
    if except_id is not None:
        statement = statement.where(Customer.id != except_id)
    return (await session.scalars(statement)).first()


async def create_customer(
    session: AsyncSession, *, name: str, description: str | None = None
) -> Customer:
    customer = Customer(name=name, description=description)
    session.add(customer)
    await session.flush()
    return customer


async def update_customer(
    session: AsyncSession, customer_id: int, *, name: str, description: str | None
) -> None:
    await session.execute(
        update(Customer)
        .where(Customer.id == customer_id)
        .values(name=name, description=description)
    )


async def set_customer_archived(
    session: AsyncSession, customer_id: int, archived_at: datetime | None
) -> None:
    """Archiving is the soft path a workspace takes instead of deletion."""
    await session.execute(
        update(Customer).where(Customer.id == customer_id).values(archived_at=archived_at)
    )


async def delete_customer(session: AsyncSession, customer_id: int) -> None:
    """The FK ``RESTRICT`` is the backstop; :func:`count_customer_content` is
    what lets the caller answer with a sentence instead of a constraint
    violation.
    """
    await session.execute(delete(Customer).where(Customer.id == customer_id))


@dataclass(frozen=True)
class CustomerContentCounts:
    """What a workspace holds — the delete guard's message and the list page's
    columns. One entry per root table, which is exactly what ``RESTRICT``
    guards.
    """

    machines: int
    prompts: int
    toolsets: int
    test_groups: int
    runs: int

    @property
    def total(self) -> int:
        return self.machines + self.prompts + self.toolsets + self.test_groups + self.runs


async def count_customer_content(
    session: AsyncSession, customer_id: int
) -> CustomerContentCounts:
    counts: dict[str, int] = {}
    for key, model in (
        ("machines", Machine),
        ("prompts", Prompt),
        ("toolsets", Toolset),
        ("test_groups", TestGroup),
        ("runs", Run),
    ):
        counts[key] = (
            await session.scalar(
                select(func.count()).select_from(model).where(model.customer_id == customer_id)
            )
            or 0
        )
    return CustomerContentCounts(**counts)


async def count_test_cases_by_customer(session: AsyncSession) -> dict[int, int]:
    """Test cases per workspace, for the MCP ``list_customers`` view.

    Test cases inherit their workspace through their group, so the count has to
    be taken over the join rather than off the child table.
    """
    rows = await session.execute(
        select(TestGroup.customer_id, func.count(TestCase.id))
        .select_from(TestCase)
        .join(TestGroup, TestCase.group_id == TestGroup.id)
        .group_by(TestGroup.customer_id)
    )
    return {customer_id: count for customer_id, count in rows.all()}


@dataclass(frozen=True)
class WorkspaceRef:
    """Just enough of a workspace to offer switching into it."""

    id: int
    name: str


async def find_run_workspace(session: AsyncSession, run_id: int) -> WorkspaceRef | None:
    """Which workspace a run lives in — deliberately unscoped.

    A deep link into another workspace has to render "switch to X" rather than
    "not found", because a link shared between colleagues must work and "not
    found" would be a lie. It exposes nothing but a workspace name the switcher
    already lists for every user anyway.
    """
    statement = (
        select(Customer.id, Customer.name)
        .join(Run, Run.customer_id == Customer.id)
        .where(Run.id == run_id)
    )
    return await _find_workspace(session, statement)


async def find_machine_workspace(
    session: AsyncSession, machine_id: int
) -> WorkspaceRef | None:
    """The same, for a machine detail page."""
    statement = (
        select(Customer.id, Customer.name)
        .join(Machine, Machine.customer_id == Customer.id)
        .where(Machine.id == machine_id)
    )
    return await _find_workspace(session, statement)


async def _find_workspace(
    session: AsyncSession, statement: Select[tuple[int, str]]
) -> WorkspaceRef | None:
    row = (await session.execute(statement)).first()
    return None if row is None else WorkspaceRef(id=row.id, name=row.name)


#: How each root table is named in a refusal. `assert_same_customer` only ever
#: sees these five, since they are the only tables a cross-root reference can
#: point at.
_ROOT_LABELS: dict[ScopedRoot, str] = {
    Machine: "machine",
    Toolset: "toolset",
    TestGroup: "test group",
    Prompt: "prompt",
    Run: "run",
}


async def assert_same_customer(
    session: AsyncSession,
    scope: Scope,
    table: ScopedRoot,
    row_id: int | Iterable[int],
) -> None:
    """Refuses a write that would point a row at another workspace's row.

    The database cannot express this: children inherit scope through their
    parent, so a link table has no ``customer_id`` to constrain, and adding one
    would denormalise the column onto every child table and need composite
    ``(id, customer_id)`` foreign keys everywhere. The places it can happen are
    exactly the places two roots meet — a test case's group, a test case's
    prompt, a test case's toolsets, a run's machine, a version's baseline run.

    Called from *inside* the repository functions rather than from each caller,
    so no call site can forget it.

    A missing id and a foreign id are reported identically: to this caller the
    row does not exist, and it has no business learning that it exists
    elsewhere.
    """
    customer_id = require_customer_id(scope)
    wanted = sorted({row_id} if isinstance(row_id, int) else set(row_id))
    if not wanted:
        return

    found = set(
        (
            await session.scalars(
                select(table.id).where(
                    table.customer_id == customer_id, table.id.in_(wanted)
                )
            )
        ).all()
    )
    missing = [row for row in wanted if row not in found]
    if not missing:
        return

    label = _ROOT_LABELS[table]
    if len(missing) == 1:
        raise CrossCustomerError(
            f"The selected {label} (id {missing[0]}) no longer exists in this workspace."
        )
    ids = ", ".join(str(row) for row in missing)
    raise CrossCustomerError(
        f"The selected {label}s (ids {ids}) no longer exist in this workspace."
    )
