"""This app *as* an MCP server.

An agent (Claude Code, say) can push another project's real prompts and test
cases in here, commit a version, start a run against a registered endpoint and
read the measurements back — instead of retyping someone else's prompts into
the web UI by hand. The point is that the interesting test cases already exist
in other repositories: a customer's own agent repo is where the job is defined.

Ported from `git show master:src/lib/mcp/*` with the pivot's renames, and with
the hand-rolled JSON-RPC layer replaced by the official Python SDK's
`MCPServer` (the ergonomic FastMCP surface). Four things that were code in the
old server are now the SDK's job: protocol negotiation, `tools/list` schemas
(derived from each tool's signature), argument validation, and turning a raised
exception into `isError` tool *content* rather than a JSON-RPC error — which is
what lets the calling model read the message and fix its arguments, the same
reasoning `app.services.tool_loop` uses when it feeds a tool failure back to
the model.

What stayed ours, because it is policy rather than protocol:

* **Auth** (`McpAuthMiddleware`) — a per-user API token read from `x-api-key`
  *before* `Authorization: Bearer`, so a client behind a reverse proxy that
  also demands basic auth can send both credentials in one request. A browser
  session cookie is accepted too, so the endpoint can be poked from a signed-in
  tab. There is no "not configured" state: the endpoint is always on and
  *tokens* are the gate, which is also what gives every call an actor whose
  role decides which tools answer.
* **The workspace** (`app.mcp.customer`) — the server is stateless, so the
  workspace arrives with each call: `customer` argument, then `X-Customer`
  header, then the token's default, then a refusal listing what exists.
* **The read-only gate** — a viewer's token is refused every writing tool as
  `isError` content, not as a protocol error, for the same reason as above.
* **Epoch millis on the wire**, matching the old server: `get_run`/`list_runs`
  already emitted numbers and external agents parse them.

Deliberately absent, both carried over from the old surface: endpoints,
toolsets and tools are not writable here (a base URL with an API key and an MCP
server URL are credentials, and this app's line is content vs. credentials),
and neither are customer workspaces (creating an engagement is a human decision
with billing behind it). `deploy` joins them for the same reason spelled out in
the spec: marking a version deployed is a human claim about a customer's
production system.

Transport is streamable HTTP in **stateless** mode: every POST is
self-contained, nothing survives a restart, and more than one app process is
therefore safe.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import FastAPI
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.types import Receive, Send
from starlette.types import Scope as AsgiScope

from app.auth.guards import Actor, optional_actor
from app.auth.policy import can_write
from app.db import async_session
from app.mcp.customer import (
    CUSTOMER_ARG_DESCRIPTION,
    resolve_mcp_scope,
    scope_source_from_headers,
)
from app.mcp.refs import (
    McpToolError,
    RowRef,
    has_key,
    optional_row_ref,
    parse_row_ref,
    parse_row_refs,
    resolve_row_ref,
    truncate,
)
from app.models import (
    Prompt,
    PromptKind,
    PromptVersion,
    Run,
    RunResult,
    TestCase,
    TestGroup,
)
from app.repos.customers import (
    count_customer_content,
    count_test_cases_by_customer,
    list_customers,
)
from app.repos.endpoints import list_endpoint_models, list_endpoints, list_loaded_models
from app.repos.prompt_versions import (
    NoChangesError,
    NotAttributedError,
    VersionError,
    commit_version,
    get_version,
    list_version_refs,
    list_versions,
    set_baseline,
)
from app.repos.prompts import (
    PromptKindChangeError,
    PromptSlotError,
    create_prompt,
    find_prompt_by_name,
    list_prompts,
    update_prompt,
)
from app.repos.results import list_comparable_runs
from app.repos.runs import (
    get_run,
    get_run_result,
    list_result_ratings,
    list_result_statuses,
    list_run_results,
    list_runs,
    rate_result,
)
from app.repos.test_cases import (
    create_test_case,
    find_test_group_by_name,
    get_test_case,
    list_test_case_toolset_views,
    list_test_cases,
    list_test_groups,
    list_toolset_links,
    replace_toolset_links,
    test_case_counts_by_group,
    update_test_case,
)
from app.repos.test_cases import create_test_group as create_test_group_row
from app.repos.toolsets import list_toolsets
from app.scope import CrossCustomerError, Scope
from app.services.attribution import VersionRef, head_version, is_dirty
from app.services.executor import execute_run, run_in_background
from app.services.llm_info import parse_llm_info
from app.services.message_assembly import NoUserMessageError
from app.services.run_create import RunCreateError, create_run_record
from app.services.run_lock import is_run_executing
from app.services.tool_config import ToolConfigError, assert_tool_config, normalize_max_turns

logger = logging.getLogger(__name__)

#: Where the endpoint lives. One path, registered as a plain route on the
#: FastAPI app (not a mount), so `POST /mcp` is answered directly instead of
#: redirecting to `/mcp/` the way a `Mount` would. The path is shared with the
#: SPA's own `/mcp` settings route, which is why the route is POST-only — see
#: `mount_mcp`.
MCP_PATH = "/mcp"

SERVER_NAME = "promptrack"
SERVER_VERSION = "0.1.0"

DEFAULT_RUN_LIMIT = 20
DEFAULT_RESPONSE_CHARS = 4000

INSTRUCTIONS = (
    "PromptRack is the registry and test bench for the prompts behind a customer's agentic "
    "tools: prompts are versioned assets (draft, explicit commits, one deployed pointer), and "
    "test cases are the regression suite that proves a version still works — including on new "
    "models and new hardware. "
    "Author it from here: test groups and test cases, prompts and their commits, then runs "
    "against a registered endpoint (an OpenAI-compatible base URL, self-hosted or hosted) and "
    "the measurements they produce. "
    "Every call is scoped to one customer engagement's workspace: pass `customer` (name or id) "
    "on each call, or send an `X-Customer` header on the connection. `list_customers` lists "
    "them. Endpoints, toolsets and workspaces are deliberately read-only here — they hold "
    "credentials, or are a human decision — and marking a version deployed stays a human claim "
    "made in the UI. "
    "Every call acts as the owner of the API token it carries, with that account's role: a "
    "read-only account is refused every tool that writes."
)

mcp_server: MCPServer[Any] = MCPServer(
    name=SERVER_NAME,
    title="PromptRack",
    version=SERVER_VERSION,
    instructions=INSTRUCTIONS,
)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

#: Whether each registered tool writes. One declaration per tool: it becomes
#: both the `readOnlyHint` annotation a client sees and the gate a viewer's
#: token is refused by, so the two cannot drift apart.
_WRITES: dict[str, bool] = {}


def _tool(
    name: str, description: str, *, write: bool, destructive: bool = False
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Registers one tool.

    `structured_output=False` keeps the wire format the old server's: a single
    JSON text block. Nothing here has an output shape stable enough to be worth
    an output schema, and the text form is what every client can already read.
    """

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        _WRITES[name] = write
        mcp_server.add_tool(
            fn,
            name=name,
            description=description,
            annotations=ToolAnnotations(read_only_hint=not write, destructive_hint=destructive),
            structured_output=False,
        )
        return fn

    return decorator


# ---------------------------------------------------------------------------
# Per-call plumbing
# ---------------------------------------------------------------------------

#: The `customer` argument every scoped tool carries.
CustomerArg = Annotated[str | int | None, Field(description=CUSTOMER_ARG_DESCRIPTION)]


def _request(ctx: Context) -> Request | None:
    request = ctx.request_context.request
    return request if isinstance(request, Request) else None


def raw_arguments(ctx: Context) -> Mapping[str, Any]:
    """The arguments exactly as the caller sent them.

    The SDK hands a tool its *validated* arguments, where an omitted optional
    and an explicit `null` are both `None`. Patch semantics need the
    difference, so presence is read off the raw `tools/call` params — the same
    thing the old `hasKey` did.
    """
    params = ctx.request_context.params or {}
    arguments = params.get("arguments")
    return arguments if isinstance(arguments, Mapping) else {}


def _actor(ctx: Context) -> Actor:
    """Who this call acts as, as decided by `McpAuthMiddleware`."""
    request = _request(ctx)
    actor = getattr(request.state, "mcp_actor", None) if request is not None else None
    if not isinstance(actor, Actor):
        raise McpToolError(
            "This call carried no credentials. Send an API token as the x-api-key header "
            "(or as Authorization: Bearer <token>)."
        )
    return actor


@asynccontextmanager
async def _call(ctx: Context, tool: str) -> AsyncIterator[tuple[AsyncSession, Actor]]:
    """The session and the actor for one tool call, with the role gate.

    Every tool goes through here, which is what makes "a viewer's token is
    refused everything that writes" impossible to forget: there is no other way
    to a database session, and the tool's own registration is what says whether
    it writes.
    """
    actor = _actor(ctx)
    if _WRITES.get(tool, True) and not can_write(actor.role):
        raise McpToolError(f'The token\'s account is read-only; "{tool}" writes.')
    async with async_session() as session:
        yield session, actor


@asynccontextmanager
async def _scoped_call(
    ctx: Context, customer: Any, tool: str
) -> AsyncIterator[tuple[AsyncSession, Scope, Actor]]:
    """The same, plus the workspace this call names (see `app.mcp.customer`)."""
    async with _call(ctx, tool) as (session, actor):
        scope = await resolve_mcp_scope(
            session, customer, scope_source_from_headers(ctx.headers)
        )
        yield session, scope, actor


def _millis(value: datetime | None) -> int | None:
    """Epoch milliseconds — the wire format the old server established."""
    return None if value is None else int(value.timestamp() * 1000)


def _json_value(raw: str | None) -> Any:
    """Reads a stored JSON column back. Degrades to `None` rather than raising:
    a malformed snapshot must never keep a past run from being read.
    """
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


def _rating_tally(values: Sequence[str | None]) -> dict[str, int]:
    """`unrated` is derived, not counted, so a stored value this build does not
    recognise still shows up in the total instead of vanishing.
    """
    good = sum(1 for value in values if value == "good")
    meh = sum(1 for value in values if value == "meh")
    bad = sum(1 for value in values if value == "bad")
    return {
        "good": good,
        "meh": meh,
        "bad": bad,
        "unrated": len(values) - good - meh - bad,
    }


def _version_summary(version_id: int | None, refs: Sequence[VersionRef]) -> dict[str, int] | None:
    if version_id is None:
        return None
    for ref in refs:
        if ref.id == version_id:
            return {"id": ref.id, "version": ref.version}
    return None


# ---------------------------------------------------------------------------
# Customer workspaces
# ---------------------------------------------------------------------------


@_tool(
    "list_customers",
    "List the customer workspaces. Every other tool is scoped to exactly one: pass the chosen "
    'name (or id) as "customer" on each call, or set an "X-Customer" header on the connection '
    "so it applies to all of them. Archived workspaces are listed too — they still work, they "
    "are just hidden from the UI switcher.",
    # Read-only so a viewer's token can orient itself before being refused a write.
    write=False,
)
async def list_customers_tool(ctx: Context) -> dict[str, Any]:
    # The one tool that names no workspace, because it is how a caller learns
    # which ones exist.
    async with _call(ctx, "list_customers") as (session, _):
        rows = await list_customers(session)
        test_case_counts = await count_test_cases_by_customer(session)
        counts = [await count_customer_content(session, row.id) for row in rows]

        return {
            "customers": [
                {
                    "id": row.id,
                    "name": row.name,
                    "description": row.description,
                    "archived": row.archived_at is not None,
                    # The workspace that owns the shared endpoints and toolsets
                    # — where a global row is authored, and the one workspace
                    # that can be neither deleted nor archived.
                    "is_base": row.is_base,
                    "counts": {
                        "prompts": count.prompts,
                        "test_groups": count.test_groups,
                        "test_cases": test_case_counts.get(row.id, 0),
                        "endpoints": count.endpoints,
                        "runs": count.runs,
                    },
                    "created_at": _millis(row.created_at),
                }
                for row, count in zip(rows, counts, strict=True)
            ]
        }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@_tool(
    "list_endpoints",
    "List the endpoints this workspace can run against (an endpoint is an OpenAI-compatible "
    "base URL plus hardware notes) and every model ever seen on each, flagging which are "
    "currently loaded. Includes endpoints shared from the Base workspace, marked `is_global` — "
    "usable here, editable only in Base. API keys are never returned.",
    write=False,
)
async def list_endpoints_tool(ctx: Context, customer: CustomerArg = None) -> dict[str, Any]:
    async with _scoped_call(ctx, customer, "list_endpoints") as (session, scope, _):
        endpoints = await list_endpoints(scope, session, "name")
        models = await list_endpoint_models(scope, session)

        return {
            "endpoints": [
                {
                    "id": endpoint.id,
                    "name": endpoint.name,
                    "base_url": endpoint.base_url,
                    "cpu": endpoint.cpu,
                    "ram": endpoint.ram,
                    "gpu": endpoint.gpu,
                    "notes": endpoint.notes,
                    # Shared from Base, so an agent can tell "this box is the
                    # consultancy's" from "this box is this engagement's"
                    # without a second call.
                    "is_global": endpoint.is_global,
                    "models": [
                        {
                            "model_id": model.model_id,
                            "currently_loaded": model.currently_loaded,
                            "source": model.source,
                            "last_seen_at": _millis(model.last_seen_at),
                        }
                        for model in models
                        if model.endpoint_id == endpoint.id
                    ],
                }
                for endpoint in endpoints
            ]
        }


# ---------------------------------------------------------------------------
# Prompts — the versioned asset
# ---------------------------------------------------------------------------


async def _resolve_prompt(scope: Scope, session: AsyncSession, ref: RowRef) -> Prompt:
    """A prompt by id or name, refusing an ambiguous name.

    Name resolution runs over the *scoped* list, which is what keeps it from
    ever reaching another workspace's prompt.
    """
    return resolve_row_ref(ref, await list_prompts(scope, session, "name"), "prompt")


async def _resolve_slot(
    scope: Scope, session: AsyncSession, value: Any, label: str
) -> int | None:
    """One of a test case's two prompt slots: a name, an id, or `None` to empty it.

    Only *existence* is resolved here. Whether the prompt's `kind` fits the
    slot is `assert_prompt_slot`'s call, made from inside the repository
    function, so every write path is covered by the same check.
    """
    ref = optional_row_ref(value, label)
    if ref is None:
        return None
    return (await _resolve_prompt(scope, session, ref)).id


def _prompt_view(prompt: Prompt, refs: Sequence[VersionRef]) -> dict[str, Any]:
    head = head_version(refs)
    return {
        "id": prompt.id,
        "name": prompt.name,
        # Which channel this prompt is sent on: "system" as the system
        # message, "task" at the head of the user message. A property of the
        # asset, so it also says which of a test case's two slots can hold it.
        "kind": prompt.kind,
        # `content` is the draft: what the editor writes and what a run always
        # tests. `dirty` is the editor's own indicator.
        "content": prompt.content,
        "dirty": is_dirty(prompt.content, refs),
        "head_version": None if head is None else {"id": head.id, "version": head.version},
        "deployed_version": _version_summary(prompt.deployed_version_id, refs),
        "deployed_at": _millis(prompt.deployed_at),
        "created_at": _millis(prompt.created_at),
        "updated_at": _millis(prompt.updated_at),
    }


def _version_view(version: PromptVersion, *, include_content: bool = True) -> dict[str, Any]:
    view: dict[str, Any] = {
        "id": version.id,
        "prompt_id": version.prompt_id,
        "version": version.version,
        "message": version.message,
        "created_at": _millis(version.created_at),
        "created_by": version.created_by,
        "baseline_run_id": version.baseline_run_id,
    }
    if include_content:
        view["content"] = version.content
    return view


@_tool(
    "list_prompts",
    "List the prompt assets with their current draft text and version state: `dirty` means the "
    "draft differs from the newest commit, and `deployed_version` is the human claim about what "
    "is live at the customer. A test case references one of these in its system slot and/or its "
    'task slot; `kind` ("system" or "task") says which slot a prompt is eligible for.',
    write=False,
)
async def list_prompts_tool(ctx: Context, customer: CustomerArg = None) -> dict[str, Any]:
    async with _scoped_call(ctx, customer, "list_prompts") as (session, scope, _):
        prompts = await list_prompts(scope, session, "name")
        refs = await list_version_refs(scope, session, [prompt.id for prompt in prompts])
        return {
            "prompts": [_prompt_view(prompt, refs.get(prompt.id, [])) for prompt in prompts]
        }


#: The two channels a prompt can be sent on. Declared as a schema `enum` on
#: both write tools *and* re-checked at runtime by `_parse_kind`, so a client
#: that skips schema validation still gets a readable refusal instead of an
#: unknown value reaching the column.
KIND_VALUES = ("system", "task")


def _parse_kind(value: Any) -> PromptKind:
    """An unrecognised kind is **refused**, never coerced.

    Deliberately the opposite of `app.auth.policy.parse_role`, which degrades
    an unknown role to `viewer`: degrading is safe there because the fallback
    is the least privileged value, while here there is no safe fallback —
    guessing a channel would silently move the text between the system message
    and the user message.
    """
    if value in KIND_VALUES:
        return value  # type: ignore[return-value]
    known = " or ".join(f'"{kind}"' for kind in KIND_VALUES)
    raise McpToolError(f'"kind" must be {known}, not {value!r}.')


@_tool(
    "create_prompt",
    "Create a prompt asset. kind decides the channel it is sent on: "
    '"system" (default) frames the model and is sent as the system message; "task" is the '
    "instruction for one call and is sent at the head of the user message, ahead of the test "
    "case's own content. The text starts as an uncommitted draft; commit_prompt freezes it as "
    "v1. Fails if the name is taken; use update_prompt to change an existing one.",
    write=True,
)
async def create_prompt_tool(
    ctx: Context,
    name: Annotated[str, Field(description='Short label, e.g. "Helpdesk agent (prod)".')],
    content: Annotated[str, Field(description="The prompt itself, verbatim.")],
    kind: Annotated[
        Literal["system", "task"],
        Field(
            description='"system" (default) to send it as the system message, "task" to send '
            "it at the head of the user message."
        ),
    ] = "system",
    customer: CustomerArg = None,
) -> dict[str, Any]:
    async with _scoped_call(ctx, customer, "create_prompt") as (session, scope, _):
        cleaned = name.strip()
        if not cleaned or not content.strip():
            raise McpToolError('"name" and "content" are required.')
        prompt_kind = _parse_kind(kind)

        existing = await find_prompt_by_name(scope, session, cleaned)
        if existing:
            raise McpToolError(
                f'A prompt named "{existing[0].name}" already exists (id {existing[0].id}). '
                "Use update_prompt to change it."
            )

        prompt = await create_prompt(
            scope, session, name=cleaned, content=content, kind=prompt_kind
        )
        await session.commit()
        return {"prompt": _prompt_view(prompt, [])}


@_tool(
    "update_prompt",
    "Change a prompt's draft in place. This does not create a version: the draft is a working "
    "copy, and commit_prompt is what freezes it. Past runs are unaffected — each run froze the "
    "prompt texts it actually sent. Changing kind is refused while any test case references "
    "the prompt: that would move its text to the other channel behind those cases' backs.",
    write=True,
)
async def update_prompt_tool(
    ctx: Context,
    prompt: Annotated[str | int, Field(description="Name or id of the prompt to change.")],
    name: Annotated[str | None, Field(description="New name.")] = None,
    content: Annotated[str | None, Field(description="New draft text.")] = None,
    kind: Annotated[
        Literal["system", "task"] | None,
        Field(description="Move the prompt to the other channel. Refused while referenced."),
    ] = None,
    customer: CustomerArg = None,
) -> dict[str, Any]:
    async with _scoped_call(ctx, customer, "update_prompt") as (session, scope, _):
        target = await _resolve_prompt(scope, session, parse_row_ref(prompt, '"prompt"'))

        values: dict[str, Any] = {}
        if name is not None:
            if not name.strip():
                raise McpToolError('"name" cannot be blank.')
            values["name"] = name.strip()
        if content is not None:
            if not content.strip():
                raise McpToolError('"content" cannot be blank.')
            values["content"] = content
        if kind is not None:
            values["kind"] = _parse_kind(kind)

        if values:
            try:
                await update_prompt(scope, session, target.id, values)
            except PromptKindChangeError as exc:
                raise McpToolError(str(exc)) from exc
            await session.commit()
            # The write went out as a bulk UPDATE, so the instance this session
            # already holds is re-read rather than trusted — the same care
            # `app.api.runs` takes after archiving a run.
            await session.refresh(target)

        refs = await list_version_refs(scope, session, [target.id])
        return {"prompt": _prompt_view(target, refs.get(target.id, []))}


@_tool(
    "commit_prompt",
    "Freeze the current draft as the next immutable version, with a message. Refused when the "
    "draft is byte-identical to the newest version — history that records a commit which "
    "changed nothing is history nobody can read.",
    write=True,
)
async def commit_prompt_tool(
    ctx: Context,
    prompt: Annotated[str | int, Field(description="Name or id of the prompt to commit.")],
    message: Annotated[str, Field(description="What changed, and why.")],
    customer: CustomerArg = None,
) -> dict[str, Any]:
    async with _scoped_call(ctx, customer, "commit_prompt") as (session, scope, actor):
        if not message.strip():
            raise McpToolError('"message" is required.')
        target = await _resolve_prompt(scope, session, parse_row_ref(prompt, '"prompt"'))

        try:
            version = await commit_version(
                scope, session, target.id, message=message.strip(), user_id=actor.user_id
            )
        except (NoChangesError, VersionError) as exc:
            raise McpToolError(str(exc)) from exc
        await session.commit()
        return {"version": _version_view(version)}


@_tool(
    "list_prompt_versions",
    "List one prompt's commit history, newest first, with the deployed and baseline markers. "
    "The frozen text itself is left out — get_prompt_version returns one in full.",
    write=False,
)
async def list_prompt_versions_tool(
    ctx: Context,
    prompt: Annotated[str | int, Field(description="Name or id of the prompt.")],
    customer: CustomerArg = None,
) -> dict[str, Any]:
    async with _scoped_call(ctx, customer, "list_prompt_versions") as (session, scope, _):
        target = await _resolve_prompt(scope, session, parse_row_ref(prompt, '"prompt"'))
        versions = await list_versions(scope, session, target.id)
        return {
            "prompt": {"id": target.id, "name": target.name},
            "deployed_version_id": target.deployed_version_id,
            "versions": [_version_view(version, include_content=False) for version in versions],
        }


@_tool(
    "get_prompt_version",
    "Fetch one committed version in full, including its frozen text.",
    write=False,
)
async def get_prompt_version_tool(
    ctx: Context,
    version_id: Annotated[int, Field(description="Version id, from list_prompt_versions.")],
    customer: CustomerArg = None,
) -> dict[str, Any]:
    async with _scoped_call(ctx, customer, "get_prompt_version") as (session, scope, _):
        version = await get_version(scope, session, version_id)
        if version is None:
            raise McpToolError(f"No prompt version with id {version_id}.")
        return {"version": _version_view(version)}


@_tool(
    "set_baseline",
    "Attach a run to a version as its baseline: the known-good run that justifies deploying it, "
    "and the reference point a regression check after a model swap compares against. Refused "
    "unless that run's results are actually attributed to this version — a run of a dirty draft "
    "carries no attribution and can never be a baseline.",
    write=True,
)
async def set_baseline_tool(
    ctx: Context,
    version_id: Annotated[int, Field(description="Version id, from list_prompt_versions.")],
    run_id: Annotated[int, Field(description="Run id, from list_runs.")],
    customer: CustomerArg = None,
) -> dict[str, Any]:
    async with _scoped_call(ctx, customer, "set_baseline") as (session, scope, _):
        try:
            await set_baseline(scope, session, version_id, run_id)
        except (NotAttributedError, VersionError, CrossCustomerError) as exc:
            raise McpToolError(str(exc)) from exc
        await session.commit()

        version = await get_version(scope, session, version_id)
        if version is None:  # pragma: no cover - deleted between the two statements
            raise McpToolError(f"No prompt version with id {version_id}.")
        return {"version": _version_view(version, include_content=False)}


# ---------------------------------------------------------------------------
# Test groups and test cases
# ---------------------------------------------------------------------------


async def _resolve_group(scope: Scope, session: AsyncSession, ref: RowRef) -> TestGroup:
    return resolve_row_ref(
        ref, await list_test_groups(scope, session, "sort-id"), "test group"
    )


async def _resolve_toolsets(
    scope: Scope, session: AsyncSession, refs: Sequence[RowRef]
) -> list[int]:
    if not refs:
        return []
    rows = await list_toolsets(scope, session)
    return [resolve_row_ref(ref, rows, "toolset").id for ref in refs]


async def _test_case_views(
    scope: Scope,
    session: AsyncSession,
    cases: Sequence[TestCase],
    *,
    include_content: bool,
    max_content_chars: int,
) -> list[dict[str, Any]]:
    """Test cases as an MCP client sees them.

    Both prompt slots are reported twice over: as the referenced asset
    (`system_prompt` / `task_prompt`, id and name) and as the text those slots
    currently hold (`system_prompt_text` / `task_prompt_text`) — the same two
    key names `get_run_result` uses for the frozen copies, so reading a case
    and reading its result speak one vocabulary. `task_prompt_text` is the
    head of the user message; `content` is the rest of it.
    """
    if not cases:
        return []

    groups = {group.id: group for group in await list_test_groups(scope, session, "sort-id")}
    prompts = {
        prompt.id: prompt for prompt in await list_prompts(scope, session, "name")
    }
    links = await list_test_case_toolset_views(
        scope, session, [case.id for case in cases]
    )

    views: list[dict[str, Any]] = []
    for case in cases:
        # One id-keyed map serves both slots: a prompt has exactly one kind, so
        # the same prompt can never sit in both slots of one case.
        system_prompt = (
            prompts.get(case.system_prompt_id) if case.system_prompt_id is not None else None
        )
        task_prompt = (
            prompts.get(case.task_prompt_id) if case.task_prompt_id is not None else None
        )
        content = (
            truncate(case.content, max_content_chars)
            if include_content
            else truncate(None, 0)
        )
        views.append(
            {
                "id": case.id,
                "title": case.title,
                "group": {
                    "id": case.group_id,
                    "name": getattr(groups.get(case.group_id), "name", None),
                },
                "content": content.text,
                "content_truncated": content.truncated,
                "expected_output": case.expected_output,
                "system_prompt": (
                    None
                    if system_prompt is None
                    else {"id": system_prompt.id, "name": system_prompt.name}
                ),
                "task_prompt": (
                    None
                    if task_prompt is None
                    else {"id": task_prompt.id, "name": task_prompt.name}
                ),
                "system_prompt_text": (
                    None if system_prompt is None else system_prompt.content
                ),
                "task_prompt_text": None if task_prompt is None else task_prompt.content,
                "tool_mode": case.tool_mode,
                "tool_choice": case.tool_choice,
                "max_turns": case.max_turns,
                "toolsets": [
                    {"id": link.toolset_id, "name": link.name, "kind": link.kind}
                    for link in links
                    if link.test_case_id == case.id
                ],
                "created_at": _millis(case.created_at),
                "updated_at": _millis(case.updated_at),
            }
        )
    return views


async def _test_case_view_by_id(
    scope: Scope, session: AsyncSession, test_case_id: int
) -> dict[str, Any]:
    case = await get_test_case(scope, session, test_case_id)
    if case is None:
        raise McpToolError(f"No test case with id {test_case_id}.")
    views = await _test_case_views(
        scope, session, [case], include_content=True, max_content_chars=0
    )
    return views[0]


@_tool(
    "list_test_groups",
    "List every test group with its test-case count. A group is what a run selects, so test "
    "cases that should be run together belong in one group.",
    write=False,
)
async def list_test_groups_tool(ctx: Context, customer: CustomerArg = None) -> dict[str, Any]:
    async with _scoped_call(ctx, customer, "list_test_groups") as (session, scope, _):
        groups = await list_test_groups(scope, session, "sort-id")
        counts = await test_case_counts_by_group(scope, session)
        return {
            "groups": [
                {
                    "id": group.id,
                    "name": group.name,
                    "description": group.description,
                    "test_case_count": counts.get(group.id, 0),
                    "created_at": _millis(group.created_at),
                }
                for group in groups
            ]
        }


@_tool(
    "create_test_group",
    "Create a test group. If a group with the same name already exists it is returned unchanged "
    "(created: false), so pushing the same suite twice does not produce duplicate groups.",
    write=True,
)
async def create_test_group_tool(
    ctx: Context,
    name: Annotated[str, Field(description='Group name, e.g. "Odoo helpdesk replies".')],
    description: Annotated[
        str | None, Field(description="What this group tests, and for which app.")
    ] = None,
    customer: CustomerArg = None,
) -> dict[str, Any]:
    async with _scoped_call(ctx, customer, "create_test_group") as (session, scope, _):
        cleaned = name.strip()
        if not cleaned:
            raise McpToolError('"name" is required.')

        existing = await find_test_group_by_name(scope, session, cleaned)
        if existing:
            group = existing[0]
            return {
                "created": False,
                "group": {
                    "id": group.id,
                    "name": group.name,
                    "description": group.description,
                },
            }

        note = description.strip() if description else None
        group = await create_test_group_row(
            scope, session, name=cleaned, description=note or None
        )
        await session.commit()
        return {
            "created": True,
            "group": {"id": group.id, "name": group.name, "description": group.description},
        }


@_tool(
    "list_test_cases",
    "List test cases, optionally narrowed to one group. Includes each case's two prompt slots "
    "(the system prompt and the task prompt, with the text each currently holds) and its tool "
    "configuration.",
    write=False,
)
async def list_test_cases_tool(
    ctx: Context,
    group: Annotated[
        str | int | None,
        Field(description="Name or id of a test group. Omit for all test cases."),
    ] = None,
    include_content: Annotated[
        bool, Field(description="Include the test case's input text. Default true.")
    ] = True,
    max_content_chars: Annotated[
        int, Field(description="Truncate that text to this many characters. 0 means no limit.")
    ] = 0,
    customer: CustomerArg = None,
) -> dict[str, Any]:
    async with _scoped_call(ctx, customer, "list_test_cases") as (session, scope, _):
        group_id: int | None = None
        if group is not None:
            group_id = (await _resolve_group(scope, session, parse_row_ref(group, '"group"'))).id

        cases = await list_test_cases(scope, session, group_id=group_id)
        views = await _test_case_views(
            scope,
            session,
            cases,
            include_content=include_content,
            max_content_chars=max_content_chars,
        )
        return {"count": len(views), "test_cases": views}


@_tool(
    "create_test_case",
    "Create a test case in a group. A case holds no prompt text of its own: it names up to two "
    "prompt assets — system_prompt (sent as the system message) and task_prompt (sent at the "
    "head of the user message) — plus content, the data that follows the task prompt. At least "
    "one of task_prompt and content must have text in it, or the request has no user message. "
    "For an ordinary one-shot test leave the tool fields out. "
    'For a tool/API test set tool_mode to "definitions" (record what the model wanted to call, '
    'execute nothing) or "execute" (really run the calls and loop) and name the toolsets to '
    "offer; toolsets themselves are authored in the web UI, not here.",
    write=True,
)
async def create_test_case_tool(
    ctx: Context,
    group: Annotated[
        str | int,
        Field(description="Name or id of the test group. Create it with create_test_group."),
    ],
    title: Annotated[str, Field(description="Short label shown in lists and comparisons.")],
    content: Annotated[
        str | None,
        Field(
            description="The data half of the user message, sent after the task prompt. May be "
            "omitted when a task prompt is the whole user message."
        ),
    ] = None,
    expected_output: Annotated[
        str | None,
        Field(
            description="What a good answer looks like. The rubric shown next to the result "
            "when rating; never sent to the model."
        ),
    ] = None,
    system_prompt: Annotated[
        str | int | None,
        Field(description='Name or id of a kind="system" prompt, sent as the system message.'),
    ] = None,
    task_prompt: Annotated[
        str | int | None,
        Field(
            description='Name or id of a kind="task" prompt, sent at the head of the user '
            "message, ahead of content."
        ),
    ] = None,
    tool_mode: Annotated[
        Literal["none", "definitions", "execute"], Field(description='Default "none".')
    ] = "none",
    tool_choice: Annotated[
        Literal["auto", "required", "none"] | None,
        Field(description="Omit to leave tool_choice out of the request entirely."),
    ] = None,
    max_turns: Annotated[
        int | None,
        Field(description='Turn budget for tool_mode "execute". Default 6, maximum 20.'),
    ] = None,
    toolsets: Annotated[
        list[str | int] | None,
        Field(
            description="Names or ids of toolsets to offer. Several may be combined, but their "
            "tool names must not collide."
        ),
    ] = None,
    customer: CustomerArg = None,
) -> dict[str, Any]:
    async with _scoped_call(ctx, customer, "create_test_case") as (session, scope, _):
        if not title.strip():
            raise McpToolError('"title" is required.')

        group_row = await _resolve_group(scope, session, parse_row_ref(group, '"group"'))
        system_prompt_id = await _resolve_slot(
            scope, session, system_prompt, '"system_prompt"'
        )
        task_prompt_id = await _resolve_slot(scope, session, task_prompt, '"task_prompt"')
        toolset_ids = await _resolve_toolsets(
            scope, session, parse_row_refs(toolsets, "toolsets")
        )

        # The same rules the test-case editor enforces, run through the very
        # same functions, so a case authored here can never be one run creation
        # would later refuse. The slot kind check and the user-message guard
        # both live inside `create_test_case` itself.
        try:
            await assert_tool_config(
                scope,
                session,
                tool_mode=tool_mode,
                toolset_ids=toolset_ids,
                subject=f'Test case "{title.strip()}"',
            )
            case = await create_test_case(
                scope,
                session,
                group_id=group_row.id,
                title=title.strip(),
                content=_blank_to_none(content),
                expected_output=_blank_to_none(expected_output),
                system_prompt_id=system_prompt_id,
                task_prompt_id=task_prompt_id,
                tool_mode=tool_mode,
                tool_choice=tool_choice,
                max_turns=normalize_max_turns(max_turns),
            )
            await replace_toolset_links(scope, session, case.id, toolset_ids)
        except (
            ToolConfigError,
            CrossCustomerError,
            PromptSlotError,
            NoUserMessageError,
        ) as exc:
            raise McpToolError(str(exc)) from exc

        await session.commit()
        return {"test_case": await _test_case_view_by_id(scope, session, case.id)}


@_tool(
    "update_test_case",
    "Change fields of an existing test case. Only the fields you pass are touched; pass null to "
    "clear an optional one. Past runs keep the text they froze, so editing is safe.",
    write=True,
)
async def update_test_case_tool(
    ctx: Context,
    test_case_id: Annotated[int, Field(description="Test case id.")],
    group: Annotated[
        str | int | None, Field(description="Move the case to this group.")
    ] = None,
    title: Annotated[str | None, Field(description="New title.")] = None,
    content: Annotated[
        str | None, Field(description="New data half of the user message, or null to clear it.")
    ] = None,
    expected_output: Annotated[str | None, Field(description="New rubric, or null.")] = None,
    system_prompt: Annotated[
        str | int | None,
        Field(description='A kind="system" prompt asset, or null to empty the slot.'),
    ] = None,
    task_prompt: Annotated[
        str | int | None,
        Field(description='A kind="task" prompt asset, or null to empty the slot.'),
    ] = None,
    tool_mode: Literal["none", "definitions", "execute"] | None = None,
    tool_choice: Literal["auto", "required", "none"] | None = None,
    max_turns: int | None = None,
    toolsets: Annotated[
        list[str | int] | None,
        Field(description="Replaces the case's toolsets. Pass [] to offer none."),
    ] = None,
    customer: CustomerArg = None,
) -> dict[str, Any]:
    async with _scoped_call(ctx, customer, "update_test_case") as (session, scope, _):
        # Presence, not value: an omitted optional and an explicit null are the
        # same `None` by the time the SDK has validated the arguments, and only
        # the latter may clear a field.
        args = raw_arguments(ctx)
        existing = await get_test_case(scope, session, test_case_id)
        if existing is None:
            raise McpToolError(f"No test case with id {test_case_id}.")

        values: dict[str, Any] = {}
        if has_key(args, "group"):
            values["group_id"] = (
                await _resolve_group(scope, session, parse_row_ref(group, '"group"'))
            ).id
        if has_key(args, "title"):
            if not title or not title.strip():
                raise McpToolError('"title" cannot be blank.')
            values["title"] = title.strip()
        if has_key(args, "content"):
            # Blank content is legal now — a task prompt can be the whole user
            # message. `update_test_case` refuses only the case where clearing
            # it leaves the row with nothing to send.
            values["content"] = _blank_to_none(content)
        if has_key(args, "expected_output"):
            values["expected_output"] = _blank_to_none(expected_output)
        if has_key(args, "system_prompt"):
            values["system_prompt_id"] = await _resolve_slot(
                scope, session, system_prompt, '"system_prompt"'
            )
        if has_key(args, "task_prompt"):
            values["task_prompt_id"] = await _resolve_slot(
                scope, session, task_prompt, '"task_prompt"'
            )
        if has_key(args, "tool_mode"):
            values["tool_mode"] = tool_mode or "none"
        if has_key(args, "tool_choice"):
            values["tool_choice"] = tool_choice
        if has_key(args, "max_turns"):
            values["max_turns"] = normalize_max_turns(max_turns)

        # The tool configuration has to be checked as it will be *after* the
        # patch: switching tool_mode without naming toolsets keeps the ones
        # already linked, and validating against an empty set would refuse it
        # wrongly. `update_test_case` applies the same post-patch reasoning to
        # the user-message guard, which it can only do by reading the row.
        replace_links = has_key(args, "toolsets")
        if replace_links:
            toolset_ids = await _resolve_toolsets(
                scope, session, parse_row_refs(toolsets, "toolsets")
            )
        else:
            links = await list_toolset_links(scope, session, [test_case_id])
            toolset_ids = [link.toolset_id for link in links]

        try:
            await assert_tool_config(
                scope,
                session,
                tool_mode=values.get("tool_mode", existing.tool_mode),
                toolset_ids=toolset_ids,
                subject=f'Test case "{values.get("title", existing.title)}"',
            )
            if values:
                await update_test_case(scope, session, test_case_id, values)
            if replace_links:
                await replace_toolset_links(scope, session, test_case_id, toolset_ids)
        except (
            ToolConfigError,
            CrossCustomerError,
            PromptSlotError,
            NoUserMessageError,
        ) as exc:
            raise McpToolError(str(exc)) from exc

        await session.commit()
        return {"test_case": await _test_case_view_by_id(scope, session, test_case_id)}


def _blank_to_none(value: str | None) -> str | None:
    if value is None:
        return None
    return value if value.strip() else None


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------


def _start_background_execution(run_id: int) -> None:
    """Starts execution outside the call that asked for it.

    Safe only because the executor already persists every row as it finishes
    and leaves the rest `pending`: an interrupted run is recoverable by design,
    and polling `get_run` is the progress channel.
    """

    async def drive() -> None:
        try:
            await execute_run(run_id, lambda event: None)
        except Exception as exc:  # noqa: BLE001 - nothing left to report it to
            logger.error("[mcp] background execution of run %s failed: %s", run_id, exc)

    run_in_background(drive())


def _status_summary(statuses: Sequence[str], ratings: Mapping[str, int]) -> dict[str, Any]:
    """A run's progress and verdicts, as both `get_run` and `list_runs` report
    it. The tallies are passed in because the two read them differently: one
    already holds every row, the other gets them from a SQL aggregate.
    """
    return {
        "total": len(statuses),
        "ok": sum(1 for status in statuses if status == "ok"),
        "error": sum(1 for status in statuses if status == "error"),
        "pending": sum(1 for status in statuses if status == "pending"),
        "running": sum(1 for status in statuses if status == "running"),
        "ratings": dict(ratings),
    }


def _run_header(run: Run) -> dict[str, Any]:
    endpoint = _json_value(run.endpoint_snapshot) or {}
    return {
        "id": run.id,
        "created_at": _millis(run.created_at),
        "started_at": _millis(run.started_at),
        "finished_at": _millis(run.finished_at),
        "endpoint": endpoint.get("name"),
        "endpoint_id": run.endpoint_id,
        "base_url": endpoint.get("base_url"),
        "model": run.model_id,
        "params": _json_value(run.params),
        "comment": run.comment,
        "groups": _json_value(run.group_names) or [],
        "status": run.status,
        "archived": run.archived_at is not None,
    }


def _result_row(result: RunResult, response: str | None, truncated: bool) -> dict[str, Any]:
    row: dict[str, Any] = {
        "result_id": result.id,
        "test_case_id": result.test_case_id,
        # Attribution, not selection: which committed version each slot's
        # tested draft matched. Null means that slot tested a dirty draft, or
        # holds no prompt at all. One per slot — the two are independent.
        "system_prompt_version_id": result.system_prompt_version_id,
        "task_prompt_version_id": result.task_prompt_version_id,
        "group": result.group_name,
        "title": result.test_case_title,
        "status": result.status,
        "error": result.error,
        "expected_output": result.expected_output,
        "response": response,
        "response_truncated": truncated,
        "rating": result.rating,
        "rating_note": result.rating_note,
        "metrics": {
            "ttft_ms": result.ttft_ms,
            "duration_ms": result.duration_ms,
            "prompt_tokens": result.prompt_tokens,
            "completion_tokens": result.completion_tokens,
            "tokens_per_sec": result.tokens_per_sec,
            "tokens_estimated": result.tokens_estimated,
        },
        "tool_mode": result.tool_mode,
    }
    if result.tool_mode != "none":
        snapshot = _json_value(result.tools_snapshot) or []
        row["tools_offered"] = _snapshot_tool_names(snapshot)
        row["turn_count"] = result.turn_count
        row["tool_call_count"] = result.tool_call_count
        row["stopped_reason"] = result.stopped_reason
    return row


def _snapshot_entries(snapshot: Any) -> list[tuple[str, Mapping[str, Any]]]:
    """`(tool name, entry)` for each usable row of a frozen `tools_snapshot`.

    Tolerates a malformed entry rather than raising: a past run has to keep
    rendering whatever its snapshot turned out to hold.
    """
    entries: list[tuple[str, Mapping[str, Any]]] = []
    if not isinstance(snapshot, list):
        return entries
    for entry in snapshot:
        if not isinstance(entry, Mapping):
            continue
        definition = entry.get("definition")
        function = definition.get("function") if isinstance(definition, Mapping) else None
        name = function.get("name") if isinstance(function, Mapping) else None
        if isinstance(name, str):
            entries.append((name, entry))
    return entries


def _snapshot_tool_names(snapshot: Any) -> list[str]:
    return [name for name, _ in _snapshot_entries(snapshot)]


def _snapshot_tool_details(snapshot: Any) -> list[dict[str, Any]]:
    return [
        {"name": name, "toolset": entry.get("toolset_name"), "source": entry.get("source")}
        for name, entry in _snapshot_entries(snapshot)
    ]


@_tool(
    "create_run",
    "Create a run of one or more test groups against a model on an endpoint. Three texts — the "
    "system prompt, the task prompt and the case's own content — plus the tool definitions are "
    "frozen into the run separately, so later edits never rewrite it, and each result records "
    "which committed version each of the two prompts was at. Set execute: true to "
    "start it immediately; otherwise it stays pending and can be started with execute_run or "
    "from the UI.",
    write=True,
)
async def create_run_tool(
    ctx: Context,
    endpoint: Annotated[
        str | int, Field(description="Name or id of the endpoint to run against (list_endpoints).")
    ],
    groups: Annotated[
        list[str | int],
        Field(description="Names or ids of the test groups to run. Every case in them runs."),
    ],
    model: Annotated[
        str | None,
        Field(
            description="Model id as the endpoint names it. May be omitted when the endpoint "
            "reports exactly one currently loaded model."
        ),
    ] = None,
    temperature: Annotated[
        float | None, Field(description="Optional; 0-2. Omitted means the server default.")
    ] = None,
    max_tokens: Annotated[int | None, Field(description="Optional completion limit.")] = None,
    comment: Annotated[
        str | None, Field(description='Note describing the conditions, e.g. "Q4_K_M, 8k ctx".')
    ] = None,
    execute: Annotated[bool, Field(description="Start executing right away.")] = False,
    customer: CustomerArg = None,
) -> dict[str, Any]:
    async with _scoped_call(ctx, customer, "create_run") as (session, scope, _):
        endpoint_row = resolve_row_ref(
            parse_row_ref(endpoint, '"endpoint"'),
            await list_endpoints(scope, session, "name"),
            "endpoint",
        )

        group_refs = parse_row_refs(groups, "groups")
        if not group_refs:
            raise McpToolError('"groups" must name at least one test group.')
        group_ids = [(await _resolve_group(scope, session, ref)).id for ref in group_refs]

        model_id = (model or "").strip()
        if not model_id:
            loaded = await list_loaded_models(scope, session, endpoint_row.id)
            if len(loaded) == 1:
                model_id = loaded[0].model_id
            elif not loaded:
                raise McpToolError(
                    f'"model" is required: endpoint "{endpoint_row.name}" has no model marked as '
                    "currently loaded. Run Discover on the endpoint page, or pass the model id."
                )
            else:
                names = ", ".join(row.model_id for row in loaded)
                raise McpToolError(
                    f'"model" is required: endpoint "{endpoint_row.name}" has several loaded '
                    f"models ({names})."
                )

        params: dict[str, Any] = {}
        if temperature is not None:
            if not 0 <= temperature <= 2:
                raise McpToolError('"temperature" must be between 0 and 2.')
            params["temperature"] = temperature
        if max_tokens is not None:
            if max_tokens < 1:
                raise McpToolError('"max_tokens" must be a positive whole number.')
            params["max_tokens"] = max_tokens

        try:
            created = await create_run_record(
                scope,
                session,
                endpoint_id=endpoint_row.id,
                model_id=model_id,
                group_ids=group_ids,
                params=params or None,
                comment=comment,
            )
        except (
            RunCreateError,
            ToolConfigError,
            NoUserMessageError,
            CrossCustomerError,
        ) as exc:
            # These are refusals for the caller (an empty group, a tool test
            # without tools, a case with no user message), not server faults.
            raise McpToolError(str(exc)) from exc

        await session.commit()

        if execute:
            _start_background_execution(created.run_id)

        return {
            "run": {
                "id": created.run_id,
                "endpoint": created.endpoint_name,
                "model": created.model_id,
                "groups": created.group_names,
                "test_case_count": created.result_count,
                "status": "running" if execute else "pending",
            },
            "executing": execute,
            "note": (
                "Execution started in the background. Poll get_run for progress."
                if execute
                else "The run is pending. Call execute_run to start it, or press Start in the UI."
            ),
        }


@_tool(
    "execute_run",
    "Start (or resume) execution of a run in the background and return immediately. Only rows "
    "still pending are executed, so this doubles as Resume. Poll get_run for progress.",
    write=True,
)
async def execute_run_tool(
    ctx: Context,
    run_id: Annotated[int, Field(description="Run id.")],
    customer: CustomerArg = None,
) -> dict[str, Any]:
    async with _scoped_call(ctx, customer, "execute_run") as (session, scope, _):
        run = await get_run(scope, session, run_id)
        if run is None:
            raise McpToolError(f"No run with id {run_id}.")
        if await is_run_executing(session, run_id):
            raise McpToolError(f"Run {run_id} is already executing.")

        statuses = await list_result_statuses(scope, session, run_id)
        pending = sum(1 for row in statuses if row.status in ("pending", "running"))
        if pending == 0:
            return {
                "started": False,
                "run_id": run_id,
                "status": run.status,
                "note": "Nothing left to execute. Errored rows are not retried automatically; "
                "recreate the run to re-measure.",
            }

        _start_background_execution(run_id)
        return {
            "started": True,
            "run_id": run_id,
            "pending": pending,
            "note": "Execution runs in the background; poll get_run for progress.",
        }


@_tool(
    "list_runs",
    "List runs newest first, with progress and rating tallies. Archived runs are excluded "
    "unless asked for.",
    write=False,
)
async def list_runs_tool(
    ctx: Context,
    status: Annotated[
        Literal["pending", "running", "completed", "failed"] | None,
        Field(description="Only runs in this state."),
    ] = None,
    archived: Annotated[
        Literal["exclude", "only", "all"], Field(description='Default "exclude".')
    ] = "exclude",
    model: Annotated[
        str | None, Field(description="Only runs whose model id contains this substring.")
    ] = None,
    limit: Annotated[int, Field(description="How many runs to return.")] = DEFAULT_RUN_LIMIT,
    customer: CustomerArg = None,
) -> dict[str, Any]:
    async with _scoped_call(ctx, customer, "list_runs") as (session, scope, _):
        capped = max(1, limit)
        # The model substring stays out of SQL: it is applied *before* the
        # limit, so pushing it down would need LIKE-escaping the caller's value
        # for no gain at this table size.
        runs = await list_runs(
            scope,
            session,
            status=status,
            archived=archived,
            limit=None if model else capped,
        )
        if model:
            wanted = model.lower()
            runs = [run for run in runs if wanted in run.model_id.lower()]
        runs = runs[:capped]

        # One aggregate for the whole workspace rather than per run: it is what
        # the results picker already reads, and `avg_tokens_per_sec` needs a
        # SQL average anyway.
        tallies = {row.run.id: row for row in await list_comparable_runs(scope, session)}

        views: list[dict[str, Any]] = []
        for run in runs:
            statuses = [row.status for row in await list_result_statuses(scope, session, run.id)]
            tally = tallies.get(run.id)
            rated = (
                {"good": 0, "meh": 0, "bad": 0}
                if tally is None
                else {"good": tally.good, "meh": tally.meh, "bad": tally.bad}
            )
            views.append(
                {
                    **_run_header(run),
                    "results": _status_summary(
                        statuses,
                        {**rated, "unrated": len(statuses) - sum(rated.values())},
                    ),
                    "avg_tokens_per_sec": (
                        None
                        if tally is None or tally.avg_rate is None
                        else round(tally.avg_rate, 2)
                    ),
                }
            )
        return {"count": len(views), "runs": views}


@_tool(
    "get_run",
    "Fetch one run with every result: status, measurements (TTFT, duration, tokens, tokens/s), "
    "manual rating, the response text and which prompt version it tested. Use this to poll a "
    "running execution and to read the outcome.",
    write=False,
)
async def get_run_tool(
    ctx: Context,
    run_id: Annotated[int, Field(description="Run id.")],
    include_responses: Annotated[
        bool, Field(description="Include response text. Default true.")
    ] = True,
    max_response_chars: Annotated[
        int,
        Field(
            description="Truncate each response to this many characters; 0 means no limit "
            "(get_run_result also returns one in full)."
        ),
    ] = DEFAULT_RESPONSE_CHARS,
    rating: Annotated[
        Literal["good", "meh", "bad", "unrated"] | None,
        Field(description="Only results with this manual verdict."),
    ] = None,
    customer: CustomerArg = None,
) -> dict[str, Any]:
    async with _scoped_call(ctx, customer, "get_run") as (session, scope, _):
        run = await get_run(scope, session, run_id)
        if run is None:
            raise McpToolError(f"No run with id {run_id}.")

        results = await list_run_results(scope, session, run_id)
        visible = [
            result
            for result in results
            if rating is None
            or (result.rating is None if rating == "unrated" else result.rating == rating)
        ]

        rows = []
        for result in visible:
            shortened = (
                truncate(result.response_text, max_response_chars)
                if include_responses
                else truncate(None, 0)
            )
            rows.append(_result_row(result, shortened.text, shortened.truncated))

        info = parse_llm_info(run.llm_info)
        return {
            "run": {
                **_run_header(run),
                "llm_info": None if info is None else info.to_json(),
                "executing": await is_run_executing(session, run_id),
                "results": _status_summary(
                    [result.status for result in results],
                    _rating_tally([result.rating for result in results]),
                ),
            },
            "results": rows,
        }


@_tool(
    "get_run_result",
    "Fetch one result in full: the three frozen texts it was sent (system_prompt_text, "
    "task_prompt_text and test_case_text, the last being the case's own content), the "
    "untruncated response, and for a tool test the whole transcript (every tool call, its "
    "arguments and what came back) with per-turn metrics.",
    write=False,
)
async def get_run_result_tool(
    ctx: Context,
    result_id: Annotated[int, Field(description="Result id, as returned by get_run.")],
    include_transcript: Annotated[
        bool, Field(description="Include the tool-call transcript. Default true.")
    ] = True,
    customer: CustomerArg = None,
) -> dict[str, Any]:
    async with _scoped_call(ctx, customer, "get_run_result") as (session, scope, _):
        result = await get_run_result(scope, session, result_id)
        if result is None:
            raise McpToolError(f"No run result with id {result_id}.")

        view: dict[str, Any] = {
            "result_id": result.id,
            "run_id": result.run_id,
            "test_case_id": result.test_case_id,
            "system_prompt_version_id": result.system_prompt_version_id,
            "task_prompt_version_id": result.task_prompt_version_id,
            "group": result.group_name,
            "title": result.test_case_title,
            "test_case_text": result.test_case_text,
            "system_prompt_text": result.system_prompt_text,
            "task_prompt_text": result.task_prompt_text,
            "expected_output": result.expected_output,
            "status": result.status,
            "error": result.error,
            "response": result.response_text,
            "rating": result.rating,
            "rating_note": result.rating_note,
            "metrics": {
                "ttft_ms": result.ttft_ms,
                "duration_ms": result.duration_ms,
                "prompt_tokens": result.prompt_tokens,
                "completion_tokens": result.completion_tokens,
                "tokens_per_sec": result.tokens_per_sec,
                "tokens_estimated": result.tokens_estimated,
            },
            "started_at": _millis(result.started_at),
            "finished_at": _millis(result.finished_at),
            "tool_mode": result.tool_mode,
        }

        if result.tool_mode != "none":
            snapshot = _json_value(result.tools_snapshot) or []
            view["tool_choice"] = result.tool_choice
            view["max_turns"] = result.max_turns
            view["tools_offered"] = _snapshot_tool_details(snapshot)
            view["turn_count"] = result.turn_count
            view["tool_call_count"] = result.tool_call_count
            view["stopped_reason"] = result.stopped_reason
            view["turns"] = _json_value(result.turns_json)
            if include_transcript:
                view["transcript"] = _json_value(result.transcript_json)

        return {"result": view}


@_tool(
    "set_rating",
    'Set or clear one result\'s manual verdict: "good", "meh", "bad", or "unrated" to clear it. '
    "Writes the same column the UI writes, so nothing afterwards distinguishes this from a "
    "hand-clicked rating — record why in `note`. The grading rubric for a result is its "
    "`expected_output` (see get_run_result); much of it is mechanically checkable (a canary "
    "string in `response`, or whether a given tool appears in the transcript) and needs no judge "
    "model. Results that have not answered yet (pending/running) are refused. Omitting `note` "
    'keeps any existing note; passing "" clears it.',
    write=True,
)
async def set_rating_tool(
    ctx: Context,
    result_id: Annotated[int, Field(description="Result id, from get_run or get_run_result.")],
    rating: Annotated[
        Literal["good", "meh", "bad", "unrated"],
        Field(
            description="good = would ship this. meh = not wrong, but not good enough — often a "
            "sign the test case or the prompt needs work rather than the model. bad = wrong or "
            "unusable. unrated = clear the verdict."
        ),
    ],
    note: Annotated[
        str | None,
        Field(
            description="Why this verdict. Shown beside the rating in the UI. Worth stating "
            "which check decided it, since the rating itself carries no record of having been "
            "set by an agent."
        ),
    ] = None,
    customer: CustomerArg = None,
) -> dict[str, Any]:
    async with _scoped_call(ctx, customer, "set_rating") as (session, scope, _):
        args = raw_arguments(ctx)
        result = await get_run_result(scope, session, result_id)
        if result is None:
            raise McpToolError(f"No run result with id {result_id}.")
        # `execute_run` is fire-and-forget, so a grading loop can trivially
        # outrun it and would otherwise leave a verdict on a row that has not
        # answered yet.
        if result.status in ("pending", "running"):
            raise McpToolError(
                f'Result {result_id} ("{result.test_case_title}") is still {result.status}, so '
                "there is nothing to judge yet. Poll get_run until it reports ok or error."
            )

        written = await rate_result(
            scope,
            session,
            result_id,
            rating=None if rating == "unrated" else rating,
            rating_note=_blank_to_none(note),
            write_note=has_key(args, "note"),
        )
        if written is None:  # pragma: no cover - deleted between the two statements
            raise McpToolError(f"No run result with id {result_id}.")
        await session.commit()

        # The run's tally, so a grading loop can watch its own progress (and
        # spot what it has not reached yet) without a second call.
        siblings = await list_result_ratings(scope, session, written.run_id)
        return {
            "result": {
                "result_id": result.id,
                "run_id": written.run_id,
                "title": result.test_case_title,
                "status": result.status,
                "rating": written.rating,
                "rating_note": written.rating_note,
            },
            "run": {"run_id": written.run_id, "ratings": _rating_tally(siblings)},
        }


# ---------------------------------------------------------------------------
# Transport: authentication, the ASGI app, mounting
# ---------------------------------------------------------------------------


class McpAuthMiddleware:
    """Authenticates every MCP request and hands the actor to the tools.

    An ASGI middleware rather than a per-tool lookup for two reasons: an
    unauthenticated caller gets one HTTP 401 with a `WWW-Authenticate`
    challenge instead of a tool-shaped refusal per call, and the token's
    `last_used_at` is bumped once per request rather than once per tool.

    The actor rides on `scope["state"]`, which is the *same* dict the SDK's
    transport builds its `Request` from — a `ContextVar` would not survive,
    because a stateless session runs each message in its own task, started from
    the session manager's task group rather than from this request's task.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: AsgiScope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":  # pragma: no cover - no websocket transport here
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive)
        async with async_session() as session:
            actor = await optional_actor(request, session)

        if actor is None:
            await _unauthorized(scope, receive, send)
            return

        scope.setdefault("state", {})["mcp_actor"] = actor
        await self.app(scope, receive, send)


async def _unauthorized(scope: AsgiScope, receive: Receive, send: Send) -> None:
    response = JSONResponse(
        {
            "jsonrpc": "2.0",
            "id": None,
            "error": {
                "code": -32001,
                "message": "Missing or invalid API token. Create one under Account -> API "
                "tokens and send it as the x-api-key header (or as "
                "Authorization: Bearer <token>).",
            },
            "server": SERVER_NAME,
        },
        status_code=401,
        headers={"WWW-Authenticate": f'Bearer realm="{SERVER_NAME}-mcp"'},
    )
    await response(scope, receive, send)


#: The MCP endpoint as an ASGI app.
#:
#: `stateless_http` because nothing may survive a restart or need to be shared
#: between processes, `json_response` because a single JSON answer per POST is
#: what the old server sent and what proxies handle without buffering surprises,
#: and DNS-rebinding protection off because the app is served behind a reverse
#: proxy under a deployment's own hostname — the token is the gate here, not the
#: Host header.
_mcp_asgi_app = mcp_server.streamable_http_app(
    streamable_http_path=MCP_PATH,
    stateless_http=True,
    json_response=True,
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)


@asynccontextmanager
async def mcp_lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Runs the streamable-HTTP session manager for the app's lifetime.

    A mounted sub-app never sees the lifespan protocol, so the manager's task
    group has to be entered by the host application — without it every request
    fails on "task group is not initialized".
    """
    del app
    async with mcp_server.session_manager.run():
        yield


def mount_mcp(app: FastAPI) -> None:
    """Registers `POST /mcp` on the FastAPI app.

    A plain `Route` rather than `app.mount()`: a mount would only match `/mcp/`
    and answer the documented path with a 307 redirect, which not every MCP
    client follows on a POST.

    **POST-only, deliberately.** The SPA has a client-side settings route at the
    same path, so `GET /mcp` has to reach the catch-all in `app.main` and serve
    the shell: a browser opening the URL gets the management page, an MCP client
    POSTs the protocol to it. Starlette answers a path match with the wrong
    method as `Match.PARTIAL` and keeps looking for a full match, so the
    catch-all's GET wins instead of this route 405ing it.
    """
    app.router.routes.append(
        Route(MCP_PATH, endpoint=McpAuthMiddleware(_mcp_asgi_app), methods=["POST"], name="mcp")
    )
