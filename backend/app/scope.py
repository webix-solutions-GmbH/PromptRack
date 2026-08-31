"""Who a query is allowed to see.

Every repository function takes a :class:`Scope` as its first argument, and a
`Scope` can only be produced by one of the three constructors below. That is
the whole mechanism: "a query without a scope" is not something a caller can
write, and the three constructors are the complete, grep-able list of ways a
workspace is decided.

* :func:`scope_for_customer` — the request's active workspace (resolved from
  the signed-in user; the auth layer is what calls it).
* :func:`scope_from_row` — derived from a row that already carries its own
  workspace, which is how background work stays scoped. The executor runs
  outside any request (MCP ``execute_run`` is fire-and-forget), so it reads the
  run row and takes the scope *from* it.
* :func:`system_scope` — the deliberate escape hatch that means "every
  workspace". Reads under it span all of them; writes raise, because a new row
  has no defensible workspace to land in.

Since endpoints and toolsets became shareable there are **two** questions, not
one: *what may I write to* (:func:`scope_where`, ownership, unchanged) and
*what may I see* (:func:`visible_where`, ownership plus the global rows). Read
:func:`visible_where`'s docstring before adding a seam of your own — which of
the two a query asks for is the whole of "read-only outside Base".

This module deliberately does not import :mod:`app.db`: it is importable by the
database-free unit tests, and nothing here needs a connection.
"""

from collections.abc import Iterable, Sequence
from dataclasses import InitVar, dataclass
from typing import Literal

from sqlalchemy import ColumnElement, and_, or_
from sqlalchemy.orm import InstrumentedAttribute

from app.models import Endpoint, ParamGroup, Prompt, Run, TestGroup, Toolset

#: Where a scope came from: a user's session, a row it was derived from, or the
#: system escape hatch.
ScopeOrigin = Literal["session", "row", "system"]


class ScopeError(Exception):
    """A scope was used for something it does not permit."""


class CrossCustomerError(ScopeError):
    """A write would have pointed a row at another workspace's row.

    Raised by :func:`app.repos.customers.assert_same_customer`. A missing id and
    a foreign id produce the same message on purpose: to this caller the row
    does not exist, and it has no business learning that it exists elsewhere.
    """


# Anything that is not this object cannot construct a `Scope`. It is module
# level and never exported, so `Scope(1, "session")` from the outside raises.
_CONSTRUCTOR_KEY = object()


@dataclass(frozen=True)
class Scope:
    """The workspace a query runs in.

    Immutable and unforgeable: the `_key` init-only field guards ``__init__``,
    so the only ways to get one are the three constructors in this module.

    ``customer_id`` is ``None`` only for a :func:`system_scope`. Everything else
    names exactly one workspace.
    """

    customer_id: int | None
    origin: ScopeOrigin
    _key: InitVar[object] = None

    def __post_init__(self, _key: object) -> None:
        if _key is not _CONSTRUCTOR_KEY:
            raise ScopeError(
                "A Scope cannot be constructed directly — use scope_for_customer(), "
                "scope_from_row() or system_scope()."
            )


def _make_scope(customer_id: int | None, origin: ScopeOrigin) -> Scope:
    return Scope(customer_id, origin, _CONSTRUCTOR_KEY)


def scope_for_customer(customer_id: int) -> Scope:
    """The scope of the current request: the signed-in user's active workspace.

    The active workspace lives on the user row rather than in a cookie, so it is
    impossible to forge from the client and survives a session refresh. This
    function is what turns that id into a scope; resolving *which* id belongs to
    the request is the auth layer's job.
    """
    return _make_scope(customer_id, "session")


def scope_from_row(customer_id: int) -> Scope:
    """Derived from a row that already carries its workspace (background work)."""
    return _make_scope(customer_id, "row")


def system_scope(reason: str) -> Scope:
    """Explicit, grep-able escape hatch spanning every workspace.

    ``reason`` is documentation — it exists so a reader of the call site learns
    why this query is allowed to cross workspaces.
    """
    del reason
    return _make_scope(None, "system")


def require_customer_id(scope: Scope) -> int:
    """The workspace a scope names, or a raise.

    Only a system scope can fail this, and only where a workspace is
    structurally required — any insert, since a new row has to land in exactly
    one workspace.
    """
    if scope.customer_id is None:
        raise ScopeError(
            "This operation needs a customer workspace, but it ran under the system scope."
        )
    return scope.customer_id


#: The six root tables that carry `customer_id`. Every other table inherits its
#: scope through a foreign key to one of these.
type ScopedRoot = (
    type[Endpoint]
    | type[Prompt]
    | type[Toolset]
    | type[TestGroup]
    | type[Run]
    | type[ParamGroup]
)


def scope_where(scope: Scope, model: ScopedRoot) -> ColumnElement[bool] | None:
    """Restricts a root-table query to the scope's workspace — **ownership**.

    ``None`` means "no predicate", which for a system scope is the documented
    "every workspace" read.

    This is the predicate every ``UPDATE`` and ``DELETE`` uses, and the one
    :func:`scope_values` mirrors on an insert. It says nothing about global
    rows on purpose: a workspace that can see a shared endpoint still may not
    rename it, and keeping that fact in the predicate rather than in a guard is
    what makes "read-only outside Base" cost no new permission layer.
    """
    if scope.customer_id is None:
        return None
    return model.customer_id == scope.customer_id


#: The root tables whose rows can be shared: an endpoint is a base URL plus an
#: API key, a toolset an MCP URL plus headers, and those are exactly the two
#: things a consultancy registers once and reuses across every engagement.
#: Nothing else is here, and nothing else may be added: prompts, test groups,
#: test cases and runs are the engagement's own material, and keeping one
#: customer's suite out of another's is the whole reason a workspace exists.
_SHAREABLE: dict[ScopedRoot, InstrumentedAttribute[bool]] = {
    Endpoint: Endpoint.is_global,
    Toolset: Toolset.is_global,
}


def visible_where(scope: Scope, model: ScopedRoot) -> ColumnElement[bool] | None:
    """Ownership, OR the global rows every workspace may read.

    Identical to :func:`scope_where` for every table but the two in
    :data:`_SHAREABLE`, where it additionally admits rows flagged
    ``is_global``. Only **read** paths take it; a write asks
    :func:`scope_where`, which is why a shared endpoint can be selected for a
    run from any workspace and renamed from none but its own.

    **The failure direction is deliberate and must stay this way.** A read path
    that forgets to opt in does not see globals — a shared endpoint missing
    from a picker, i.e. a missing feature someone reports. The inverse design
    (a permissive default, opted out of for writes) would turn the identical
    omission into a cross-workspace disclosure. Nothing here may ever become
    the default for that reason, the same reasoning that keeps
    :func:`system_scope` an explicit, grep-able call rather than an implicit
    state.

    A system scope still returns ``None``: "every workspace" already includes
    the global rows, and narrowing it here would make the escape hatch see less
    than an ordinary scope does.
    """
    own = scope_where(scope, model)
    if own is None:
        return None
    shared = _SHAREABLE.get(model)
    if shared is None:
        return own
    return or_(own, shared.is_(True))


def scope_values(scope: Scope) -> dict[str, int]:
    """The columns a new root row must carry."""
    return {"customer_id": require_customer_id(scope)}


def combine(
    conditions: Iterable[ColumnElement[bool] | None],
) -> ColumnElement[bool] | None:
    """``AND`` that tolerates ``None`` and collapses to ``None`` when empty.

    A single condition comes back unwrapped, so a predicate never gains a layer
    of parentheses just for passing through here.
    """
    present = [condition for condition in conditions if condition is not None]
    if not present:
        return None
    if len(present) == 1:
        return present[0]
    return and_(*present)


def where_scoped(
    scope: Scope,
    model: ScopedRoot,
    *conditions: ColumnElement[bool] | None,
) -> ColumnElement[bool] | None:
    """The scope predicate for ``model`` AND-ed with the caller's own conditions."""
    return combine([scope_where(scope, model), *conditions])


def where_visible(
    scope: Scope,
    model: ScopedRoot,
    *conditions: ColumnElement[bool] | None,
) -> ColumnElement[bool] | None:
    """:func:`where_scoped`'s read-side twin — see :func:`visible_where`.

    Spelled as its own function rather than a flag on ``where_scoped`` so a
    call site says which of the two questions it is asking, and a reviewer
    grepping for the shared-row surface finds every one of them.
    """
    return combine([visible_where(scope, model), *conditions])


@dataclass(frozen=True)
class CustomerOption:
    """A workspace as the switcher and the MCP ``list_customers`` tool see it."""

    id: int
    name: str
    archived: bool


def resolve_active_customer_id(
    preferred: int | None,
    customers: Sequence[CustomerOption],
) -> int | None:
    """Which workspace a user lands in.

    ``preferred`` is their stored ``active_customer_id``; it is ignored when it
    names a workspace that no longer exists or has been archived, because a
    stale pointer must degrade to a working session rather than an empty app.
    Falls back to the oldest live workspace. An install whose every workspace is
    archived still gets one — an unusable app is worse than a hidden workspace
    showing through.
    """
    live = [customer for customer in customers if not customer.archived]
    if preferred is not None and any(customer.id == preferred for customer in live):
        return preferred
    if live:
        return live[0].id
    if customers:
        return customers[0].id
    return None
