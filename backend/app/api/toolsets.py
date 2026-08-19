"""`/api/toolsets` — tool bundles and what they offer: tools, and a markdown corpus.

Toolset CRUD (it holds `mcp_url` + `mcp_headers`, i.e. credentials) is
`Admin`; the tools *inside* one are `Writer`, and `POST /{id}/discover` sits
at `Writer` too — it only ever reveals tool names/descriptions, never the
headers it authenticates with — the same split `app.api.endpoints` makes
between endpoint CRUD and `POST /discover`. Reading is `CurrentUser`: every
role needs the list to build a test case.

A `documents` toolset's corpus sits at `Writer` for exactly that reason: the
container may hold credentials, the markdown inside it never does, so it is
content and follows the same line the tools already draw. The three retrieval
tools a documents toolset offers are synthesized `tools` rows
(`app.repos.toolsets.sync_document_tools`), re-assertable through
`POST /{id}/documents/sync` the way MCP tools are re-read through
`POST /{id}/discover` — which is also why hand-authoring a *fourth* tool on a
documents toolset is refused here (`_refuse_hand_authored_tool`): it would be
offered to the model as a canned-response tool with no corpus behind it, and the
executor routes a call on `tools.source`, so a `manual` row inside a documents
toolset is a shape nothing downstream accounts for.

`mcp_headers` is treated exactly like an endpoint's `api_key`: never
round-tripped back to the client (a `ToolsetView` carries `has_mcp_headers`
instead), and write-only/patch-like on `PUT` — omit to leave the stored value
untouched, send `""`/`null` to clear it, send a value to replace it. `mcp_url`
is not a credential and is returned and replaced like any other field.
Switching `kind` to anything but `mcp` always clears both — neither a manual
toolset nor a documents one has a server to reach, and a stale URL left on one
is configuration nothing reads.

A **global** toolset (`is_global`, settable only in the Base workspace) reads
from every workspace and writes from none but its own, explicitly refused by
`_refuse_if_borrowed` rather than left as `scope_where`'s silent no-op — the
same split `app.api.endpoints` makes. Its tools follow it: readable wherever
the toolset is, editable only in Base. Deleting one that other workspaces'
test cases still select is a 409 naming them (`ToolsetInUseError`) — and so is
clearing `is_global` on one, since un-sharing strands exactly the same links
behind a row those workspaces can no longer see.
"""

import json
import re
from collections import Counter
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, File, HTTPException, UploadFile, status
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.guards import Admin, CurrentScope, CurrentUser, DbSession, Writer
from app.models import Document, Tool, Toolset, ToolsetKind, ToolSource
from app.repos.customers import NotBaseWorkspaceError
from app.repos.documents import (
    DocumentMeta,
    DocumentPathConflictError,
    create_document,
    delete_document,
    get_document,
    list_corpus_stats,
    list_documents,
    update_document,
    upsert_document,
)
from app.repos.toolsets import McpToolDescriptor as SyncedToolDescriptor
from app.repos.toolsets import (
    ToolsetInUseError,
    create_tool,
    create_toolset,
    delete_tool,
    delete_toolset,
    get_toolset,
    list_tools,
    list_toolsets,
    set_tool_enabled,
    sync_discovered_tools,
    sync_document_tools,
    update_tool,
    update_toolset,
)
from app.scope import Scope
from app.services.documents import (
    DOCUMENT_TOOL_NAMES,
    MARKDOWN_SUFFIXES,
    MAX_TITLE_LENGTH,
    clean_document_path,
    derive_document_title,
    normalize_markdown,
)
from app.services.mcp_client import McpClientError, list_mcp_tools

router = APIRouter(prefix="/toolsets", tags=["toolsets"])

_URL_RE = re.compile(r"^https?://", re.IGNORECASE)
_TOOL_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
_EMPTY_SCHEMA = '{"type": "object", "properties": {}}'

#: Per *document*, not per request. A megabyte of markdown is far more than
#: `read_document`'s largest window, so a file above this is a sign something
#: other than documentation was dropped on the corpus. Bytes rather than
#: characters because it is checked *before* decoding, which is what keeps a
#: dropped video out of memory rather than merely out of the column.
_MAX_DOCUMENT_BYTES = 1024 * 1024


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


class DocumentView(BaseModel):
    """One document without its markdown — every list, and every write's echo.

    `chars` and not "bytes": it is the unit `read_document` windows in, so the
    corpus browser and the model measure a document in the same currency.
    """

    id: int
    toolset_id: int
    title: str
    path: str
    chars: int
    created_at: datetime
    updated_at: datetime


class DocumentDetailView(DocumentView):
    """One document *with* its markdown — the editor's read, and nothing else's."""

    content: str


class ToolsetView(BaseModel):
    id: int
    name: str
    description: str | None
    kind: ToolsetKind
    mcp_url: str | None
    has_mcp_headers: bool
    #: Shared with every workspace by the Base workspace that owns it.
    is_global: bool
    #: Whether *this* workspace owns the row — false only for a global toolset
    #: seen from elsewhere, which is exactly when every write here refuses. See
    #: `app.api.endpoints.EndpointView.editable`.
    editable: bool
    #: Both counts, because discovery disables a vanished tool rather than
    #: deleting it: "3/5 enabled" is the only honest summary of an MCP toolset
    #: whose server has moved on.
    tool_count: int
    enabled_tool_count: int
    #: How many documents the corpus holds — always present, and 0 for the kinds
    #: that have no corpus, because a `documents` toolset with an empty corpus is
    #: the one state the list has to be able to show: its three tools are there
    #: and answer, and every answer is "this corpus contains no documents".
    document_count: int
    created_at: datetime
    updated_at: datetime


class ToolsetDetailView(ToolsetView):
    tools: list[ToolView]
    #: Metadata only, never `content`: a corpus is megabytes and the detail page
    #: shows a table of paths. One document's markdown is its own route.
    documents: list[DocumentView]


class ToolsetWriteRequest(BaseModel):
    name: str = Field(min_length=1)
    description: str | None = None
    kind: ToolsetKind = "manual"
    mcp_url: str | None = None
    #: Write-only credential — see the module docstring.
    mcp_headers: str | None = None
    #: Refused outside Base by `assert_base_workspace`, from inside the
    #: repository — see `app.repos.toolsets`.
    is_global: bool = False

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


class DocumentWriteRequest(BaseModel):
    """One hand-authored document. A full replace, like `ToolWriteRequest`."""

    path: str = Field(min_length=1)
    #: Optional: omitted or blank derives it from the markdown's first heading,
    #: then from the path's file stem — the same rule the upload route applies to
    #: a file, so a document reads the same whichever door it came in through.
    title: str | None = Field(default=None, max_length=MAX_TITLE_LENGTH)
    content: str

    @field_validator("path")
    @classmethod
    def _validate_path(cls, value: str) -> str:
        return clean_document_path(value)

    @field_validator("title")
    @classmethod
    def _blank_to_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @field_validator("content")
    @classmethod
    def _validate_content(cls, value: str) -> str:
        cleaned = normalize_markdown(value)
        if not cleaned.strip():
            raise ValueError("A document needs markdown content.")
        return cleaned

    def resolved_title(self) -> str:
        return self.title or derive_document_title(self.content, self.path)


class DocumentUploadResult(BaseModel):
    """What became of one uploaded file.

    Per file rather than per request, because a folder drop is the ordinary way a
    corpus arrives and one `.pdf` among thirty `.md`s must not lose the other
    twenty-nine. `created` distinguishes a new document from one that replaced
    what was already at that path — uploading a corrected file is the normal
    maintenance action, so a re-upload is a replace and never a conflict.
    """

    filename: str
    ok: bool
    path: str | None = None
    created: bool | None = None
    error: str | None = None


class DocumentUploadResponse(BaseModel):
    created: int
    replaced: int
    failed: int
    results: list[DocumentUploadResult]
    #: The corpus after the upload, so one call refreshes the table.
    documents: list[DocumentView]


class DocumentToolSyncResponse(BaseModel):
    """What re-asserting the three synthesized tools changed.

    Deliberately *not* shaped like `DiscoverResponse`: that carries an `ok`
    discriminator because reaching a live MCP server can fail, which is an
    expected outcome of the probe rather than a failed request. Re-asserting rows
    from a constant cannot fail, and a union that never takes its second branch
    would be a lie about the shape.
    """

    created: int
    refreshed: int
    tools: list[str]


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


def _owns(scope: Scope, toolset: Toolset) -> bool:
    """Whether this scope owns the row rather than merely seeing it — see
    `app.api.endpoints._owns`, which this mirrors.
    """
    return toolset.customer_id == scope.customer_id


def _refuse_if_borrowed(scope: Scope, toolset: Toolset) -> None:
    if _owns(scope, toolset):
        return
    raise HTTPException(
        status.HTTP_403_FORBIDDEN,
        f'"{toolset.name}" is shared from the Base workspace. Switch to Base to change it.',
    )


def _toolset_view(
    toolset: Toolset,
    tool_count: int,
    enabled_tool_count: int,
    document_count: int,
    *,
    editable: bool,
) -> ToolsetView:
    return ToolsetView(
        id=toolset.id,
        name=toolset.name,
        description=toolset.description,
        kind=toolset.kind,
        mcp_url=toolset.mcp_url,
        has_mcp_headers=bool(toolset.mcp_headers),
        is_global=toolset.is_global,
        editable=editable,
        tool_count=tool_count,
        enabled_tool_count=enabled_tool_count,
        document_count=document_count,
        created_at=toolset.created_at,
        updated_at=toolset.updated_at,
    )


def _document_view(meta: DocumentMeta) -> DocumentView:
    return DocumentView(
        id=meta.id,
        toolset_id=meta.toolset_id,
        title=meta.title,
        path=meta.path,
        chars=meta.chars,
        created_at=meta.created_at,
        updated_at=meta.updated_at,
    )


def _document_detail_view(document: Document) -> DocumentDetailView:
    """The full row. `chars` is measured here rather than in the database,
    because unlike a list this route was always going to carry the text.
    """
    return DocumentDetailView(
        id=document.id,
        toolset_id=document.toolset_id,
        title=document.title,
        path=document.path,
        chars=len(document.content),
        created_at=document.created_at,
        updated_at=document.updated_at,
        content=document.content,
    )


async def _get_or_404(scope: Scope, session: AsyncSession, toolset_id: int) -> Toolset:
    toolset = await get_toolset(scope, session, toolset_id)
    if toolset is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such toolset.")
    return toolset


async def _get_owned_or_404(scope: Scope, session: AsyncSession, toolset_id: int) -> Toolset:
    """The toolset, refusing a shared one this workspace only borrows."""
    toolset = await _get_or_404(scope, session, toolset_id)
    _refuse_if_borrowed(scope, toolset)
    return toolset


async def _detail_view(
    scope: Scope, session: AsyncSession, toolset: Toolset
) -> ToolsetDetailView:
    tools = await list_tools(scope, session, toolset_ids=[toolset.id])
    documents = await list_documents(scope, session, toolset_ids=[toolset.id])
    base = _toolset_view(
        toolset,
        len(tools),
        sum(1 for tool in tools if tool.enabled),
        len(documents),
        editable=_owns(scope, toolset),
    )
    return ToolsetDetailView(
        **base.model_dump(),
        tools=[_tool_view(tool) for tool in tools],
        documents=[_document_view(meta) for meta in documents],
    )


async def _get_tool_or_404(
    scope: Scope, session: AsyncSession, toolset_id: int, tool_id: int
) -> Tool:
    """Scoped through the toolset, like `list_tools` itself: a tool id that
    belongs to a foreign workspace or a different toolset is a 404, not a
    500 from a mismatched write later.

    Only the write routes use this, so it asks for the toolset to be *owned*:
    `list_tools` reads through visibility, and without this a shared toolset's
    tools would resolve here and then be silently not-written by `_tool_where`'s
    strict predicate.
    """
    await _get_owned_or_404(scope, session, toolset_id)
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


def _refuse_hand_authored_tool(toolset: Toolset) -> None:
    """Refuses authoring or rewriting a tool on a `documents` toolset.

    Nothing in the repository layer stops it — `create_tool` only asks that the
    toolset be this workspace's, and before this feature that was the whole rule,
    which is why a hand-written tool on an `mcp` toolset is still allowed (a
    canned `mock_response` beside discovered tools is a useful way to exercise one
    without its server). A documents toolset is the case where it stops being
    harmless: its three tools are synthesized from one constant so that every
    corpus offers the same functions with the same descriptions, and the executor
    routes a call on `tools.source`, so a fourth `manual` row here would be
    offered to the model as a canned-response tool with no corpus behind it and
    quietly turn a retrieval measurement into something else. Rewriting one of
    the three is the same problem from the other side: a re-worded description is
    the one variable this feature holds constant across engagements.

    Deleting a tool and flipping `enabled` stay allowed on purpose. Disabling
    `search_documents` to see whether a model can navigate by `list_documents` and
    `read_document` alone is one of the more interesting things this can measure,
    and `POST /{id}/documents/sync` puts a deleted row back.
    """
    if toolset.kind != "documents":
        return
    raise HTTPException(
        status.HTTP_400_BAD_REQUEST,
        f'"{toolset.name}" is a documents toolset: its tools are synthesized '
        f"({', '.join(DOCUMENT_TOOL_NAMES)}) and cannot be written by hand. Add "
        "documents to the corpus instead.",
    )


def _toolset_values(
    body: ToolsetWriteRequest, *, include_headers: bool, include_global: bool
) -> dict[str, object]:
    # Keyed on "is this the one kind that *has* a server", not on "is this
    # manual": `documents` is the third kind and has no server either, and a
    # stale URL left behind on it would show up in the editor as an MCP toolset's
    # worth of configuration that nothing reads.
    has_server = body.kind == "mcp"
    values: dict[str, object] = {
        "name": body.name,
        "description": body.description,
        "kind": body.kind,
        # A serverless toolset has no URL, so switching to one always clears it
        # rather than leaving a stale one behind.
        "mcp_url": body.mcp_url if has_server else None,
    }
    if include_headers:
        values["mcp_headers"] = body.mcp_headers if has_server else None
    if include_global:
        values["is_global"] = body.is_global
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
    # `list_corpus_stats` and not `list_documents`: the list only needs the
    # count, and counting in Postgres is what keeps a page of corpora from
    # reading every document's length out of TOAST storage to get it.
    corpora = await list_corpus_stats(scope, session, [toolset.id for toolset in toolsets])
    return [
        _toolset_view(
            toolset,
            counts.get(toolset.id, 0),
            enabled_counts.get(toolset.id, 0),
            corpora[toolset.id].document_count,
            editable=_owns(scope, toolset),
        )
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
    values = _toolset_values(body, include_headers=True, include_global=True)
    try:
        toolset = await create_toolset(scope, session, **values)
    except NotBaseWorkspaceError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
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
    await _get_owned_or_404(scope, session, toolset_id)

    # A credential, not content: only touched when the request actually named
    # it (see the module docstring) — but switching to a kind that has no server
    # always clears it, matching the create path.
    include_headers = body.kind != "mcp" or "mcp_headers" in body.model_fields_set
    # `is_global` is patch-like for a different reason than the credential: it
    # defaults to `false`, so a client that knows nothing about sharing would
    # un-share the toolset on every save. See `app.api.endpoints`.
    values = _toolset_values(
        body,
        include_headers=include_headers,
        include_global="is_global" in body.model_fields_set,
    )

    try:
        await update_toolset(scope, session, toolset_id, values)
    except NotBaseWorkspaceError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except ToolsetInUseError as exc:
        # Un-sharing a toolset other workspaces still select — the same 409 the
        # delete gives, because it is the same loss one step earlier.
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    await session.commit()
    refreshed = await _get_or_404(scope, session, toolset_id)
    return await _detail_view(scope, session, refreshed)


@router.delete("/{toolset_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_toolset_endpoint(
    toolset_id: int, actor: Admin, scope: CurrentScope, session: DbSession
) -> None:
    """Cascades to its tools at the database level; never touches
    `run_results` — a past run renders from its own frozen snapshot.

    A toolset that *other workspaces'* test cases still select is refused with a
    409 naming them, because `test_case_toolsets` cascades and the loss would
    otherwise be silent and invisible from here — see
    `app.repos.toolsets.ToolsetInUseError`.
    """
    del actor
    await _get_owned_or_404(scope, session, toolset_id)
    try:
        await delete_toolset(scope, session, toolset_id)
    except ToolsetInUseError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
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
    toolset = await _get_owned_or_404(scope, session, toolset_id)
    _refuse_hand_authored_tool(toolset)
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
    _refuse_hand_authored_tool(await _get_owned_or_404(scope, session, toolset_id))
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
    `app.api.endpoints`'s `POST /discover`.
    """
    del actor
    toolset = await _get_owned_or_404(scope, session, toolset_id)
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


# --------------------------------------------------------------------------
# The corpus behind a documents toolset
#
# `Writer`, not `Admin`, for every write here: the toolset is the container that
# may hold credentials, the markdown inside it is content, which is exactly the
# line the tools already draw. Reads stay `CurrentUser` and go through
# visibility, so a corpus the Base workspace shares is readable from every
# engagement that borrows it and writable in none of them — `_get_owned_or_404`
# turns that into a named 403 rather than the silent no-op the strict predicate
# would give on its own.
# --------------------------------------------------------------------------


def _require_documents_kind(toolset: Toolset) -> None:
    """Refuses *filling* a corpus on a toolset that is not a documents toolset.

    A manual toolset with documents in it would be a corpus no model can reach:
    nothing offers `list_documents`/`search_documents`/`read_document` unless
    `kind` is `documents`, and the three rows are synthesized from the kind alone.
    Saying so is much better than accepting the upload and leaving the user to
    discover that a run answers "I have no tools for that".

    Deliberately **not** asked by `PUT`/`DELETE` on a document that already
    exists, or by either read. Switching a toolset's kind away from `documents`
    leaves its corpus in place (the same reason `sync_document_tools` leaves the
    three tool rows alone), and gating the cleanup routes on the kind would strand
    that corpus behind an `Admin`-only kind change — a `Writer` who can see a
    document has to be able to correct or remove it.
    """
    if toolset.kind == "documents":
        return
    raise HTTPException(
        status.HTTP_400_BAD_REQUEST,
        f'"{toolset.name}" is not a documents toolset, so a corpus on it would be '
        'unreachable. Change its kind to "documents" first.',
    )


async def _get_document_or_404(
    scope: Scope, session: AsyncSession, toolset_id: int, document_id: int
) -> Document:
    """One document, addressed through its toolset — see `_get_tool_or_404`.

    The toolset has to be visible and the document has to be *in it*: a document
    id from another toolset (or another workspace) is a 404 here rather than a
    write that lands somewhere the caller did not name.
    """
    await _get_or_404(scope, session, toolset_id)
    document = await get_document(scope, session, document_id)
    if document is None or document.toolset_id != toolset_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such document.")
    return document


async def _get_owned_document_or_404(
    scope: Scope, session: AsyncSession, toolset_id: int, document_id: int
) -> Document:
    """The same lookup, refusing a corpus this workspace only borrows.

    Every write asks this: `get_document` reads through visibility, so without it
    a shared corpus's document would resolve and then be silently not-written by
    `_document_where`'s strict predicate.
    """
    await _get_owned_or_404(scope, session, toolset_id)
    return await _get_document_or_404(scope, session, toolset_id, document_id)


def _upload_path(filename: str) -> str:
    """The corpus path a dropped file lands at.

    The browser's `filename` is the key, which is what lets a client that has a
    folder in hand send `guides/refunds.md` and get that path in the corpus
    rather than a flattened `refunds.md`. Markdown-only is enforced on the
    extension and nothing else — sniffing the content would only make a rejected
    file's reason harder to explain.
    """
    name = (filename or "").strip().replace("\\", "/")
    if not name:
        raise ValueError("The file has no name.")
    if not name.lower().endswith(MARKDOWN_SUFFIXES):
        raise ValueError(
            f"Only markdown files can be uploaded ({', '.join(MARKDOWN_SUFFIXES)})."
        )
    return clean_document_path(name)


def _decode_markdown(raw: bytes) -> str:
    """An uploaded file's bytes as corpus text, or a sentence saying why not.

    UTF-8 is required rather than guessed: a corpus is searched with Postgres FTS
    and read back to a model, and a latin-1 fallback would put mojibake into both
    — where it reads as the *model* misquoting the documentation. A file that is
    not UTF-8 is one the uploader has to convert, and saying so is the only
    honest answer.
    """
    if len(raw) > _MAX_DOCUMENT_BYTES:
        raise ValueError(
            f"Larger than the {_MAX_DOCUMENT_BYTES // 1024} kB per-document limit."
        )
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(
            "Not valid UTF-8 text — re-save the file as UTF-8 markdown."
        ) from exc
    content = normalize_markdown(text)
    if not content.strip():
        raise ValueError("The file is empty.")
    return content


@router.get("/{toolset_id}/documents")
async def list_documents_endpoint(
    toolset_id: int, actor: CurrentUser, scope: CurrentScope, session: DbSession
) -> list[DocumentView]:
    """The corpus, metadata only.

    A toolset's documents also travel inside its own detail response, the way its
    tools do; this route exists because an upload or a delete changes only the
    corpus, and re-reading a megabyte-free list is cheaper than re-reading the
    whole toolset.
    """
    del actor
    await _get_or_404(scope, session, toolset_id)
    metas = await list_documents(scope, session, toolset_ids=[toolset_id])
    return [_document_view(meta) for meta in metas]


@router.post("/{toolset_id}/documents/upload")
async def upload_documents_endpoint(
    toolset_id: int,
    actor: Writer,
    scope: CurrentScope,
    session: DbSession,
    files: Annotated[list[UploadFile], File()],
) -> DocumentUploadResponse:
    """Uploads markdown files into the corpus, one document per file.

    Path-idempotent, because that is what maintaining a corpus looks like: the
    same folder uploaded twice, or one corrected file re-sent, replaces what was
    at that path instead of refusing (`app.repos.documents.upsert_document`).
    Editing a document by id is where a path collision is still a mistake and
    stays a 409.

    Rejections are **per file** and the request still succeeds: one `.pdf` in a
    folder of thirty guides must not cost the other twenty-nine, so every file
    gets a row in `results` and the accepted ones commit together. Nothing is
    read into memory beyond the per-document cap.
    """
    del actor
    toolset = await _get_owned_or_404(scope, session, toolset_id)
    _require_documents_kind(toolset)
    if not files:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No files uploaded.")

    results: list[DocumentUploadResult] = []
    created = 0
    replaced = 0
    for upload in files:
        filename = (upload.filename or "").strip() or "(unnamed file)"
        try:
            path = _upload_path(upload.filename or "")
            content = _decode_markdown(await upload.read(_MAX_DOCUMENT_BYTES + 1))
        except ValueError as exc:
            results.append(DocumentUploadResult(filename=filename, ok=False, error=str(exc)))
            continue

        write = await upsert_document(
            scope,
            session,
            toolset_id,
            title=derive_document_title(content, path),
            path=path,
            content=content,
        )
        if write.created:
            created += 1
        else:
            replaced += 1
        results.append(
            DocumentUploadResult(filename=filename, ok=True, path=path, created=write.created)
        )

    await session.commit()
    metas = await list_documents(scope, session, toolset_ids=[toolset_id])
    return DocumentUploadResponse(
        created=created,
        replaced=replaced,
        failed=sum(1 for result in results if not result.ok),
        results=results,
        documents=[_document_view(meta) for meta in metas],
    )


@router.post("/{toolset_id}/documents/sync")
async def sync_document_tools_endpoint(
    toolset_id: int, actor: Writer, scope: CurrentScope, session: DbSession
) -> DocumentToolSyncResponse:
    """Re-asserts the three synthesized retrieval tools.

    The documents counterpart of `POST /{id}/discover`, and `Writer` for the same
    reason: it reveals nothing and writes only tool rows. A documents toolset gets
    them at creation, so this is the door for a corpus that predates an improved
    tool description, or one whose rows were deleted by hand.

    It never re-enables a tool that is already there — disabling
    `search_documents` and leaving the model to navigate by `list_documents` and
    `read_document` alone is a deliberate test case, and a sync that helpfully
    switched it back on would destroy it silently. See
    `app.repos.toolsets.sync_document_tools`.
    """
    del actor
    toolset = await _get_owned_or_404(scope, session, toolset_id)
    _require_documents_kind(toolset)
    sync = await sync_document_tools(scope, session, toolset_id)
    await session.commit()
    return DocumentToolSyncResponse(
        created=sync.created, refreshed=sync.refreshed, tools=list(DOCUMENT_TOOL_NAMES)
    )


@router.get("/{toolset_id}/documents/{document_id}")
async def get_document_endpoint(
    toolset_id: int,
    document_id: int,
    actor: CurrentUser,
    scope: CurrentScope,
    session: DbSession,
) -> DocumentDetailView:
    """One document with its markdown — the only route that carries `content`."""
    del actor
    document = await _get_document_or_404(scope, session, toolset_id, document_id)
    return _document_detail_view(document)


@router.post("/{toolset_id}/documents", status_code=status.HTTP_201_CREATED)
async def create_document_endpoint(
    toolset_id: int,
    body: DocumentWriteRequest,
    actor: Writer,
    scope: CurrentScope,
    session: DbSession,
) -> DocumentDetailView:
    del actor
    toolset = await _get_owned_or_404(scope, session, toolset_id)
    _require_documents_kind(toolset)
    try:
        document = await create_document(
            scope,
            session,
            toolset_id,
            title=body.resolved_title(),
            path=body.path,
            content=body.content,
        )
    except DocumentPathConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    await session.commit()
    return _document_detail_view(document)


@router.put("/{toolset_id}/documents/{document_id}")
async def update_document_endpoint(
    toolset_id: int,
    document_id: int,
    body: DocumentWriteRequest,
    actor: Writer,
    scope: CurrentScope,
    session: DbSession,
) -> DocumentDetailView:
    """A full replace of one document, like `PUT /tools/{id}`.

    Moving it onto a path its corpus already uses is a 409 raised before the
    `UPDATE`, so a refused edit writes nothing at all — unlike the upload route,
    where replacing by path is the point.
    """
    del actor
    document = await _get_owned_document_or_404(scope, session, toolset_id, document_id)
    try:
        await update_document(
            scope,
            session,
            document.id,
            {"title": body.resolved_title(), "path": body.path, "content": body.content},
        )
    except DocumentPathConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    await session.commit()
    refreshed = await _get_document_or_404(scope, session, toolset_id, document_id)
    return _document_detail_view(refreshed)


@router.delete(
    "/{toolset_id}/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_document_endpoint(
    toolset_id: int,
    document_id: int,
    actor: Writer,
    scope: CurrentScope,
    session: DbSession,
) -> None:
    """Never touches the toolset's tools: the three retrieval tools stay, and an
    emptied corpus answers every one of them truthfully — `list_documents` says
    it is empty rather than the tool vanishing mid-suite.
    """
    del actor
    document = await _get_owned_document_or_404(scope, session, toolset_id, document_id)
    await delete_document(scope, session, document.id)
    await session.commit()
