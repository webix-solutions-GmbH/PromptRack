"""`/api/toolsets` — tool bundles and the tools they offer, plus MCP discovery.

Toolset CRUD (it holds `mcp_url` + `mcp_headers`, i.e. credentials) is
`Admin`; the tools *inside* one are `Writer`, and `POST /{id}/discover` sits
at `Writer` too — it only ever reveals tool names/descriptions, never the
headers it authenticates with — the same split `app.api.machines` makes
between machine CRUD and `POST /discover`. Reading is `CurrentUser`: every
role needs the list to build a test case.

`mcp_headers` is treated exactly like a machine's `api_key`: never
round-tripped back to the client (a `ToolsetView` carries `has_mcp_headers`
instead), and write-only/patch-like on `PUT` — omit to leave the stored value
untouched, send `""`/`null` to clear it, send a value to replace it. `mcp_url`
is not a credential and is returned and replaced like any other field.
Switching `kind` to `manual` always clears both, mirroring
`git show master:src/actions/toolsets.ts`'s `toolsetFields`.
"""

import json
import re
from collections import Counter
from datetime import datetime

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.guards import Admin, CurrentScope, CurrentUser, DbSession, Writer
from app.models import Tool, Toolset, ToolsetKind, ToolSource
from app.repos.toolsets import McpToolDescriptor as SyncedToolDescriptor
from app.repos.toolsets import (
    create_tool,
    create_toolset,
    delete_tool,
    delete_toolset,
    get_toolset,
    list_tools,
    list_toolsets,
    set_tool_enabled,
    sync_discovered_tools,
    update_tool,
    update_toolset,
)
from app.scope import Scope
from app.services.mcp_client import McpClientError, list_mcp_tools

router = APIRouter(prefix="/toolsets", tags=["toolsets"])

_URL_RE = re.compile(r"^https?://", re.IGNORECASE)
_TOOL_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
_EMPTY_SCHEMA = '{"type": "object", "properties": {}}'


# --------------------------------------------------------------------------
# Wire shapes
# --------------------------------------------------------------------------


class ToolView(BaseModel):
    id: int
    toolset_id: int
    name: str
    description: str | None
    parameters_json: str
    mock_response: str | None
    enabled: bool
    source: ToolSource
    first_seen_at: datetime
    last_seen_at: datetime


class ToolsetView(BaseModel):
    id: int
    name: str
    description: str | None
    kind: ToolsetKind
    mcp_url: str | None
    has_mcp_headers: bool
    #: Both counts, because discovery disables a vanished tool rather than
    #: deleting it: "3/5 enabled" is the only honest summary of an MCP toolset
    #: whose server has moved on.
    tool_count: int
    enabled_tool_count: int
    created_at: datetime
    updated_at: datetime


class ToolsetDetailView(ToolsetView):
    tools: list[ToolView]


class ToolsetWriteRequest(BaseModel):
    name: str = Field(min_length=1)
    description: str | None = None
    kind: ToolsetKind = "manual"
    mcp_url: str | None = None
    #: Write-only credential — see the module docstring.
    mcp_headers: str | None = None

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Name is required.")
        return cleaned

    @field_validator("description")
    @classmethod
    def _blank_to_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @field_validator("mcp_url")
    @classmethod
    def _clean_mcp_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @field_validator("mcp_headers")
    @classmethod
    def _validate_mcp_headers(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            return None
        try:
            parsed = json.loads(cleaned)
        except ValueError as exc:
            raise ValueError("Headers must be valid JSON.") from exc
        if not isinstance(parsed, dict):
            raise ValueError(
                'Headers must be a JSON object, e.g. {"Authorization": "Bearer …"}.'
            )
        if not all(isinstance(v, str) for v in parsed.values()):
            raise ValueError("Every header value must be a string.")
        return cleaned

    @model_validator(mode="after")
    def _mcp_url_required_for_mcp_kind(self) -> "ToolsetWriteRequest":
        if self.kind == "mcp" and (self.mcp_url is None or not _URL_RE.match(self.mcp_url)):
            raise ValueError(
                "An MCP toolset needs a server URL starting with http:// or https://"
            )
        return self


class ToolWriteRequest(BaseModel):
    name: str = Field(min_length=1)
    description: str | None = None
    parameters_json: str | None = None
    mock_response: str | None = None

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not _TOOL_NAME_RE.match(cleaned):
            raise ValueError(
                "Tool name may only contain letters, digits, underscores and hyphens (max 64)."
            )
        return cleaned

    @field_validator("description", "mock_response")
    @classmethod
    def _blank_to_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @field_validator("parameters_json")
    @classmethod
    def _validate_parameters(cls, value: str | None) -> str:
        cleaned = (value or "").strip()
        if not cleaned:
            return _EMPTY_SCHEMA
        try:
            parsed = json.loads(cleaned)
        except ValueError as exc:
            raise ValueError("Parameters must be valid JSON.") from exc
        if not isinstance(parsed, dict):
            raise ValueError("Parameters must be a JSON object (a JSON Schema).")
        return cleaned


class SetToolEnabledRequest(BaseModel):
    enabled: bool


class DiscoverResponse(BaseModel):
    ok: bool
    discovered: int = 0
    retired: int = 0
    tools: list[str] = []
    error: str | None = None


# --------------------------------------------------------------------------
# View builders / lookups
# --------------------------------------------------------------------------


def _tool_view(tool: Tool) -> ToolView:
    return ToolView(
        id=tool.id,
        toolset_id=tool.toolset_id,
        name=tool.name,
        description=tool.description,
        parameters_json=tool.parameters_json,
        mock_response=tool.mock_response,
        enabled=tool.enabled,
        source=tool.source,
        first_seen_at=tool.first_seen_at,
        last_seen_at=tool.last_seen_at,
    )


def _toolset_view(toolset: Toolset, tool_count: int, enabled_tool_count: int) -> ToolsetView:
    return ToolsetView(
        id=toolset.id,
        name=toolset.name,
        description=toolset.description,
        kind=toolset.kind,
        mcp_url=toolset.mcp_url,
        has_mcp_headers=bool(toolset.mcp_headers),
        tool_count=tool_count,
        enabled_tool_count=enabled_tool_count,
        created_at=toolset.created_at,
        updated_at=toolset.updated_at,
    )


async def _get_or_404(scope: Scope, session: AsyncSession, toolset_id: int) -> Toolset:
    toolset = await get_toolset(scope, session, toolset_id)
    if toolset is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such toolset.")
    return toolset


async def _detail_view(
    scope: Scope, session: AsyncSession, toolset: Toolset
) -> ToolsetDetailView:
    tools = await list_tools(scope, session, toolset_ids=[toolset.id])
    base = _toolset_view(toolset, len(tools), sum(1 for tool in tools if tool.enabled))
    return ToolsetDetailView(**base.model_dump(), tools=[_tool_view(tool) for tool in tools])


async def _get_tool_or_404(
    scope: Scope, session: AsyncSession, toolset_id: int, tool_id: int
) -> Tool:
    """Scoped through the toolset, like `list_tools` itself: a tool id that
    belongs to a foreign workspace or a different toolset is a 404, not a
    500 from a mismatched write later.
    """
    tools = await list_tools(scope, session, toolset_ids=[toolset_id])
    for tool in tools:
        if tool.id == tool_id:
            return tool
    raise HTTPException(status.HTTP_404_NOT_FOUND, "No such tool.")


async def _refuse_duplicate_name(
    scope: Scope, session: AsyncSession, toolset_id: int, name: str
) -> None:
    """Pre-checks the `(toolset_id, name)` unique constraint so the caller
    gets a sentence instead of a raw driver error — see
    `app.repos.toolsets.create_tool`'s docstring, which deliberately lets the
    constraint violation escape rather than catching it itself.
    """
    existing = await list_tools(scope, session, toolset_ids=[toolset_id])
    if any(tool.name == name for tool in existing):
        raise HTTPException(
            status.HTTP_409_CONFLICT, f'This toolset already has a tool called "{name}".'
        )


def _toolset_values(body: ToolsetWriteRequest, *, include_headers: bool) -> dict[str, object]:
    if body.kind == "manual":
        values: dict[str, object] = {
            "name": body.name,
            "description": body.description,
            "kind": "manual",
            "mcp_url": None,
        }
        if include_headers:
            values["mcp_headers"] = None
        return values

    values = {
        "name": body.name,
        "description": body.description,
        "kind": "mcp",
        "mcp_url": body.mcp_url,
    }
    if include_headers:
        values["mcp_headers"] = body.mcp_headers
    return values


# --------------------------------------------------------------------------
# Toolset CRUD
# --------------------------------------------------------------------------


@router.get("")
async def list_toolsets_endpoint(
    actor: CurrentUser, scope: CurrentScope, session: DbSession
) -> list[ToolsetView]:
    del actor
    toolsets = await list_toolsets(scope, session)
    tools = await list_tools(scope, session)
    counts = Counter(tool.toolset_id for tool in tools)
    enabled_counts = Counter(tool.toolset_id for tool in tools if tool.enabled)
    return [
        _toolset_view(toolset, counts.get(toolset.id, 0), enabled_counts.get(toolset.id, 0))
        for toolset in toolsets
    ]


@router.get("/{toolset_id}")
async def get_toolset_endpoint(
    toolset_id: int, actor: CurrentUser, scope: CurrentScope, session: DbSession
) -> ToolsetDetailView:
    del actor
    toolset = await _get_or_404(scope, session, toolset_id)
    return await _detail_view(scope, session, toolset)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_toolset_endpoint(
    body: ToolsetWriteRequest, actor: Admin, scope: CurrentScope, session: DbSession
) -> ToolsetDetailView:
    del actor
    values = _toolset_values(body, include_headers=True)
    toolset = await create_toolset(scope, session, **values)
    await session.commit()
    return await _detail_view(scope, session, toolset)


@router.put("/{toolset_id}")
async def update_toolset_endpoint(
    toolset_id: int,
    body: ToolsetWriteRequest,
    actor: Admin,
    scope: CurrentScope,
    session: DbSession,
) -> ToolsetDetailView:
    del actor
    await _get_or_404(scope, session, toolset_id)

    # A credential, not content: only touched when the request actually named
    # it (see the module docstring) — but switching to `manual` always clears
    # it, matching the create path.
    include_headers = body.kind == "manual" or "mcp_headers" in body.model_fields_set
    values = _toolset_values(body, include_headers=include_headers)

    await update_toolset(scope, session, toolset_id, values)
    await session.commit()
    refreshed = await _get_or_404(scope, session, toolset_id)
    return await _detail_view(scope, session, refreshed)


@router.delete("/{toolset_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_toolset_endpoint(
    toolset_id: int, actor: Admin, scope: CurrentScope, session: DbSession
) -> None:
    """Cascades to its tools at the database level; never touches
    `run_results` — a past run renders from its own frozen snapshot.
    """
    del actor
    await _get_or_404(scope, session, toolset_id)
    await delete_toolset(scope, session, toolset_id)
    await session.commit()


# --------------------------------------------------------------------------
# Tools inside a toolset
# --------------------------------------------------------------------------


@router.post("/{toolset_id}/tools", status_code=status.HTTP_201_CREATED)
async def create_tool_endpoint(
    toolset_id: int,
    body: ToolWriteRequest,
    actor: Writer,
    scope: CurrentScope,
    session: DbSession,
) -> ToolView:
    del actor
    await _get_or_404(scope, session, toolset_id)
    await _refuse_duplicate_name(scope, session, toolset_id, body.name)

    tool = await create_tool(
        scope,
        session,
        toolset_id,
        name=body.name,
        description=body.description,
        parameters_json=body.parameters_json or _EMPTY_SCHEMA,
        mock_response=body.mock_response,
    )
    await session.commit()
    return _tool_view(tool)


@router.put("/{toolset_id}/tools/{tool_id}")
async def update_tool_endpoint(
    toolset_id: int,
    tool_id: int,
    body: ToolWriteRequest,
    actor: Writer,
    scope: CurrentScope,
    session: DbSession,
) -> ToolView:
    del actor
    existing = await _get_tool_or_404(scope, session, toolset_id, tool_id)
    if body.name != existing.name:
        await _refuse_duplicate_name(scope, session, toolset_id, body.name)

    await update_tool(
        scope,
        session,
        tool_id,
        {
            "name": body.name,
            "description": body.description,
            "parameters_json": body.parameters_json or _EMPTY_SCHEMA,
            "mock_response": body.mock_response,
        },
    )
    await session.commit()
    refreshed = await _get_tool_or_404(scope, session, toolset_id, tool_id)
    return _tool_view(refreshed)


@router.delete("/{toolset_id}/tools/{tool_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tool_endpoint(
    toolset_id: int, tool_id: int, actor: Writer, scope: CurrentScope, session: DbSession
) -> None:
    del actor
    await _get_tool_or_404(scope, session, toolset_id, tool_id)
    await delete_tool(scope, session, tool_id)
    await session.commit()


@router.put("/{toolset_id}/tools/{tool_id}/enabled")
async def set_tool_enabled_endpoint(
    toolset_id: int,
    tool_id: int,
    body: SetToolEnabledRequest,
    actor: Writer,
    scope: CurrentScope,
    session: DbSession,
) -> ToolView:
    """A lighter action than the full editor — flips a stale MCP-discovered
    tool back on, or a manual one off, without touching anything else.
    """
    del actor
    await _get_tool_or_404(scope, session, toolset_id, tool_id)
    await set_tool_enabled(scope, session, tool_id, body.enabled)
    await session.commit()
    refreshed = await _get_tool_or_404(scope, session, toolset_id, tool_id)
    return _tool_view(refreshed)


# --------------------------------------------------------------------------
# MCP discovery
# --------------------------------------------------------------------------


@router.post("/{toolset_id}/discover")
async def discover_toolset_endpoint(
    toolset_id: int, actor: Writer, scope: CurrentScope, session: DbSession
) -> DiscoverResponse:
    """`tools/list` against the toolset's live MCP server.

    `Writer`, not `Admin`: this only ever reveals tool names/descriptions,
    never the `mcp_headers` it authenticates with, mirroring
    `app.api.machines`'s `POST /discover`.
    """
    del actor
    toolset = await _get_or_404(scope, session, toolset_id)
    if toolset.kind != "mcp" or not toolset.mcp_url:
        return DiscoverResponse(ok=False, error="Not an MCP toolset — nothing to discover.")

    try:
        discovered = await list_mcp_tools(toolset.mcp_url, toolset.mcp_headers)
    except McpClientError as exc:
        return DiscoverResponse(ok=False, error=str(exc))

    sync = await sync_discovered_tools(
        scope,
        session,
        toolset_id,
        [
            SyncedToolDescriptor(
                name=tool.name,
                description=tool.description,
                parameters_json=tool.parameters_json,
            )
            for tool in discovered
        ],
    )
    await session.commit()
    return DiscoverResponse(
        ok=True,
        discovered=sync.discovered,
        retired=sync.retired,
        tools=[tool.name for tool in discovered],
    )
