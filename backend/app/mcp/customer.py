"""Which customer workspace one MCP call runs in.

The MCP server is stateless by design — no session id is issued, so there is
nowhere to "switch workspace" between calls and the workspace has to arrive
with each request. Three ways, in precedence order:

1. an explicit `customer` argument on the call,
2. an `X-Customer` header on the connection (set once in the client's
   `mcp.json` and applied to every call),
3. the token's own default workspace.

Nothing is guessed. With none of the three present the call is refused with the
list of workspaces, because a write with no defined destination is worse than
an error the calling model can act on.

Calls that name a row by globally-unique id (a run or a result) are the one
exception: the id names exactly one row, so the workspace does not
disambiguate anything — `resolve_row_scope` takes it from the row itself, and
a workspace the caller *did* name can only agree or contradict. A
contradiction is refused naming the row's actual workspace, never silently
overridden: workspaces are labels rather than tenants (any signed-in user can
switch into any of them), so naming the right one reveals nothing, while a
silent override would hide that the caller is confused about where a run
lives.

The scope it produces comes from `scope_from_row`: an MCP call names a customer
*row* and derives its workspace from that, exactly as background work does —
`scope_for_customer` means "the signed-in user's active workspace", which is
the one thing this server does not have.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.mcp.refs import (
    McpToolError,
    Named,
    RowRef,
    optional_row_ref,
    parse_row_ref,
    resolve_row_ref,
)
from app.repos.customers import list_customer_options
from app.scope import Scope, scope_from_row

CUSTOMER_HEADER = "x-customer"
CUSTOMER_ARG_KEY = "customer"

#: The description every tool's `customer` argument carries. Deliberately not a
#: required field: the header can supply it, which a JSON Schema cannot
#: express — so the runtime refusal carries the explanation instead.
CUSTOMER_ARG_DESCRIPTION = (
    "Name or id of the customer workspace this call applies to. Required unless the "
    "connection sends an X-Customer header. list_customers shows what exists."
)

#: The same argument on tools that name a row by globally-unique id.
ROW_CUSTOMER_ARG_DESCRIPTION = (
    "Optional here: the id names exactly one row, so the workspace is resolved from the "
    "row itself. If passed (or sent as an X-Customer header) it must name that same "
    "workspace — a contradiction is refused naming the row's actual one."
)


@dataclass(frozen=True)
class McpScopeSource:
    """What the *connection* said about the workspace, as opposed to the call."""

    #: `X-Customer: acme` — set once on the connection, applies to every call.
    header: RowRef | None
    #: The token's own workspace.
    #:
    #: Always None today: `api_tokens` carries no customer column, and giving a
    #: token a home workspace is a separate decision from making the surface
    #: workspace-aware. The precedence chain is written out anyway so adding the
    #: column later changes one line and no call site.
    token_default: int | None = None


def scope_source_from_headers(headers: Mapping[str, str] | None) -> McpScopeSource:
    """Reads `X-Customer` off a request. An empty header counts as absent."""
    raw = (headers or {}).get(CUSTOMER_HEADER)
    value = raw.strip() if raw else ""
    if not value:
        return McpScopeSource(header=None)
    return McpScopeSource(header=parse_row_ref(value, f'The "{CUSTOMER_HEADER}" header'))


def pick_customer_ref(argument: Any, source: McpScopeSource) -> RowRef | None:
    """The precedence chain, as a pure function of the call and the connection."""
    explicit = optional_row_ref(argument, f'"{CUSTOMER_ARG_KEY}"')
    if explicit is not None:
        return explicit
    if source.header is not None:
        return source.header
    if source.token_default is not None:
        return RowRef.by_id(source.token_default)
    return None


def resolve_customer_ref(ref: RowRef | None, rows: Sequence[Named]) -> int:
    """Turns a ref into a workspace id, or refuses with something actionable.

    Split out from `resolve_mcp_scope` so the whole decision is testable
    without a database — only the workspace list comes from one.
    """
    if ref is None:
        known = ", ".join(f"{row.name} ({row.id})" for row in rows)
        suffix = f" Known workspaces: {known}." if known else ""
        raise McpToolError(
            "Every call is scoped to one customer workspace. Pass "
            f'"{CUSTOMER_ARG_KEY}" (name or id) with this call, or send an '
            f'"{CUSTOMER_HEADER}" header on the connection.{suffix}'
        )
    return resolve_row_ref(ref, rows, "customer workspace").id


async def resolve_mcp_scope(
    session: AsyncSession, argument: Any, source: McpScopeSource
) -> Scope:
    """The workspace scope for one tool call."""
    rows = await list_customer_options(session)
    return scope_from_row(resolve_customer_ref(pick_customer_ref(argument, source), rows))


def resolve_row_customer_id(
    argument: Any,
    source: McpScopeSource,
    *,
    owner_id: int,
    rows: Sequence[Named],
    row: str,
) -> int:
    """The workspace for a call that names a row by globally-unique id.

    Pure, like `resolve_customer_ref` above and split out for the same reason:
    the whole decision is testable without a database — only `rows` comes from
    one. See the module docstring for why a contradiction is refused rather
    than overridden in either direction.
    """
    ref = pick_customer_ref(argument, source)
    if ref is None:
        return owner_id
    named = resolve_row_ref(ref, rows, "customer workspace").id
    if named != owner_id:
        owner = next((r.name for r in rows if r.id == owner_id), None)
        label = f'"{owner}" ({owner_id})' if owner else f"workspace id {owner_id}"
        raise McpToolError(
            f"{row} belongs to the workspace {label}, not the one this call named. "
            f'Pass that as "{CUSTOMER_ARG_KEY}", or omit it — a call that names a row '
            "by id resolves the workspace from the row itself."
        )
    return owner_id


async def resolve_row_scope(
    session: AsyncSession,
    argument: Any,
    source: McpScopeSource,
    *,
    owner_id: int,
    row: str,
) -> Scope:
    """`resolve_mcp_scope`'s twin for id-addressed calls.

    The workspace list is only read when the caller actually named one — the
    common case (no argument, no header) is the row's own workspace with no
    extra query.
    """
    if pick_customer_ref(argument, source) is None:
        return scope_from_row(owner_id)
    rows = await list_customer_options(session)
    return scope_from_row(
        resolve_row_customer_id(argument, source, owner_id=owner_id, rows=rows, row=row)
    )
