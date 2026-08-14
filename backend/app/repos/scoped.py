"""Shared machinery for the repositories."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import ColumnElement, Delete, Select, Update, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from app.scope import Scope, ScopedRoot, scope_where, visible_where


def utc_now() -> datetime:
    """An aware "now".

    Every timestamp column is ``timestamptz`` and server code therefore always
    holds aware datetimes; a naive one would be interpreted in the process's
    local zone on the way in.
    """
    return datetime.now(UTC)


def apply_where[S: (Select[Any], Update, Delete)](
    statement: S, condition: ColumnElement[bool] | None
) -> S:
    """Adds a predicate to a statement, or leaves it alone when there is none.

    ``None`` is what :func:`app.scope.where_scoped` returns for a system scope —
    "every workspace" — and SQLAlchemy refuses a literal ``None`` in
    ``.where()``, so the distinction is made here rather than in every query.
    """
    if condition is None:
        return statement
    return statement.where(condition)


def scope_through_parent(
    scope: Scope,
    child_fk: InstrumentedAttribute[Any],
    parent: ScopedRoot,
    parent_id: InstrumentedAttribute[Any],
    *,
    visible: bool = False,
) -> ColumnElement[bool] | None:
    """Restricts a child row to parents that are in scope.

    Child tables never carry ``customer_id`` themselves — they inherit it
    through their foreign key. A read can express that as a join; an UPDATE or
    DELETE cannot, which is what this subquery is for.

    ``visible=True`` inherits through :func:`~app.scope.visible_where` instead
    of ownership, so a global endpoint's ``endpoint_models`` and a global
    toolset's ``tools`` come along with the parent a workspace can see. It is
    opt-in for the same reason the seam itself is: a child read that forgets it
    shows nothing shared, which is a missing feature and never a leak. A write
    must not pass it — a shared parent's children are still only editable where
    the parent lives.
    """
    parent_scope = visible_where(scope, parent) if visible else scope_where(scope, parent)
    if parent_scope is None:
        return None
    return child_fk.in_(select(parent_id).where(parent_scope))


@asynccontextmanager
async def transaction(session: AsyncSession) -> AsyncIterator[AsyncSession]:
    """Runs a block as one unit of work.

    The only way a transaction leaves this layer, which is what keeps "these
    writes are one unit" a data-access concern rather than something every
    caller could get subtly wrong (run creation freezing a run, its result rows
    and a model sighting together is the case that needs it).

    A session that is already in a transaction gets a SAVEPOINT, so nesting is
    safe — notably under the integration harness, which wraps each test.
    """
    if session.in_transaction():
        async with session.begin_nested():
            yield session
    else:
        async with session.begin():
            yield session
