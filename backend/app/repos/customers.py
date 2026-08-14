"""Customer workspaces.

The one family of queries that is *about* workspaces rather than inside one, so
these functions take no :class:`~app.scope.Scope`: a scope is derived *from* a
customer, and asking "which workspaces exist" under one workspace's scope would
be circular. Authorization is the role gate at the API boundary — every
signed-in user may see and switch into every workspace, which is what "a
workspace is a label, not a tenant" means.

:func:`assert_same_customer` lives here too, because it is the one check the
database cannot make — and so does :func:`assert_base_workspace`, the rule that
only the Base workspace may own a global endpoint or toolset, for the same
reason: "which workspace is Base" is a fact about this table.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Select, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Customer, Endpoint, Prompt, Run, TestCase, TestGroup, Toolset
from app.scope import (
    CrossCustomerError,
    CustomerOption,
    Scope,
    ScopedRoot,
    require_customer_id,
    visible_where,
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

    endpoints: int
    prompts: int
    toolsets: int
    test_groups: int
    runs: int

    @property
    def total(self) -> int:
        return self.endpoints + self.prompts + self.toolsets + self.test_groups + self.runs


async def count_customer_content(
    session: AsyncSession, customer_id: int
) -> CustomerContentCounts:
    counts: dict[str, int] = {}
    for key, model in (
        ("endpoints", Endpoint),
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


async def find_endpoint_workspace(
    session: AsyncSession, endpoint_id: int
) -> WorkspaceRef | None:
    """The same, for an endpoint detail page."""
    statement = (
        select(Customer.id, Customer.name)
        .join(Endpoint, Endpoint.customer_id == Customer.id)
        .where(Endpoint.id == endpoint_id)
    )
    return await _find_workspace(session, statement)


async def _find_workspace(
    session: AsyncSession, statement: Select[tuple[int, str]]
) -> WorkspaceRef | None:
    row = (await session.execute(statement)).first()
    return None if row is None else WorkspaceRef(id=row.id, name=row.name)


# ---------------------------------------------------------------------------
# The Base workspace — the one that may own global endpoints and toolsets
# ---------------------------------------------------------------------------


class NotBaseWorkspaceError(Exception):
    """A row was asked to become global outside the Base workspace.

    Its own class rather than a `CrossCustomerError` because it is the opposite
    complaint: the row is right here in this workspace, and what is refused is
    the *sharing*, not the reference. Callers map it to 400 — the request is
    well-formed and simply asks for something only Base may ask for.
    """


async def base_customer_id(session: AsyncSession) -> int | None:
    """The id of the Base workspace, or ``None`` on an install that has none.

    Ordered and limited rather than trusting uniqueness: nothing in the schema
    stops a second flagged row (`is_base` is a plain boolean, and a partial
    unique index would still have to be repaired by hand if one ever appeared),
    so the oldest flagged workspace wins and the answer stays a single id.

    ``None`` — no workspace carries the flag — makes :func:`assert_base_workspace`
    refuse everything, which is the right way round: an install with no Base has
    nowhere for a global row to live.
    """
    return await session.scalar(
        select(Customer.id).where(Customer.is_base.is_(True)).order_by(Customer.id.asc()).limit(1)
    )


async def assert_base_workspace(session: AsyncSession, scope: Scope, *, subject: str) -> None:
    """Refuses marking a row global anywhere but Base.

    Called from inside the endpoint and toolset repositories on create *and* on
    update, the same way `assert_prompt_slot` and `assert_user_message` are
    called from inside their repositories: the rule cannot be forgotten by a
    route, an MCP tool or a script.

    The check is on the *scope*, not on the row, and that is exact rather than a
    shortcut: a create lands in `scope_values(scope)`'s workspace and an update
    is filtered by `scope_where`, so the row a write touches is always the
    scope's own.
    """
    customer_id = require_customer_id(scope)
    base_id = await base_customer_id(session)
    if base_id is not None and customer_id == base_id:
        return
    raise NotBaseWorkspaceError(
        f"{subject} can only be shared from the Base workspace. "
        "Switch to Base and create it there, or leave it local to this workspace."
    )


#: How each root table is named in a refusal. `assert_same_customer` only ever
#: sees these five, since they are the only tables a cross-root reference can
#: point at.
_ROOT_LABELS: dict[ScopedRoot, str] = {
    Endpoint: "endpoint",
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
    *,
    allow_global: bool = False,
) -> None:
    """Refuses a write that would point a row at another workspace's row.

    The database cannot express this: children inherit scope through their
    parent, so a link table has no ``customer_id`` to constrain, and adding one
    would denormalise the column onto every child table and need composite
    ``(id, customer_id)`` foreign keys everywhere. The places it can happen are
    exactly the places two roots meet — a test case's group, a test case's
    prompt, a test case's toolsets, a run's endpoint, a version's baseline run.

    Called from *inside* the repository functions rather than from each caller,
    so no call site can forget it.

    ``allow_global`` widens the check from ownership to
    :func:`~app.scope.visible_where`, for the references that may legitimately
    name a shared row. Keyword-only and defaulting to ``False``, so a positional
    slip cannot enable it and every other call site keeps refusing globals
    untouched. The exception list is **five** call sites, not the two the
    original design named, and every one of them is load-bearing — written out
    here because a reader who only sees the design's two will read the other
    three as a leak:

    * ``app/repos/runs.py`` — a run's endpoint. A run against a shared box is
      the whole point of sharing one.
    * ``app/repos/test_cases.py`` — a test case's toolsets, on the link write.
    * ``app/services/tool_config.py`` — the *same* toolset rule, checked again
      at run creation. It is one shared function called from both authoring and
      run creation precisely so a case that saves cannot be one a run then
      refuses; a stricter check here would refuse exactly what the link write
      above allows.
    * ``app/repos/endpoints.py``, :func:`~app.repos.endpoints.touch_endpoint_model`
      — the model sighting a run records against the endpoint it just ran on.
      That write is inside `create_run_record`'s transaction, so refusing it
      would refuse the run.
    * ``app/repos/endpoints.py``, :func:`~app.repos.endpoints.sync_discovered_models`
      — discovery. The new-run page probes the selected endpoint on page load
      for every role, so a shared box that refused it would be unselectable.

    Both endpoint entries write ``endpoint_models``, a table with no
    ``customer_id`` of its own and nothing customer-specific in a row — see
    :class:`~app.models.endpoints.EndpointModel` on why one shared box having
    one shared history is intended rather than a leak.

    A missing id and a foreign id are reported identically: to this caller the
    row does not exist, and it has no business learning that it exists
    elsewhere.
    """
    customer_id = require_customer_id(scope)
    wanted = sorted({row_id} if isinstance(row_id, int) else set(row_id))
    if not wanted:
        return

    reachable = table.customer_id == customer_id
    if allow_global:
        # Routed through the one definition of "shareable" rather than spelled
        # out again here. `require_customer_id` already refused a system scope,
        # so this is never the "every workspace" `None`.
        shared = visible_where(scope, table)
        if shared is not None:
            reachable = shared

    found = set(
        (await session.scalars(select(table.id).where(reachable, table.id.in_(wanted)))).all()
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
