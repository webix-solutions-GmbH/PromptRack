"""`/api/endpoints` — endpoints (base URL + credentials) and model discovery.

Reading an endpoint needs only a signed-in actor (the new-run page needs every
role to see the list), writing one is `Admin` (it holds an API key), and
`POST /discover` sits in between at `Writer` — it only reads model ids back,
and every writer needs it on the new-run page.

The stored `api_key` is never round-tripped back to the client: an endpoint view
carries `has_api_key` instead. A JSON API has no way to guarantee a raw secret
only reaches a browser that could already see it, so `PUT` treats `api_key` as
write-only and patch-like: omit it to leave the stored key untouched, send
`""`/`null` to clear it, send a value to replace it.

The route handlers here are suffixed `_route` rather than the `_endpoint` every
other `app/api/*` module uses: this is the one module where "endpoint" is the
domain noun, and `list_endpoints_endpoint` says nothing twice.

A **global** endpoint (`is_global`, settable only in the Base workspace) is
returned by the reads here to every workspace and refused by the writes to all
but its own. The refusal is stated explicitly — `_refuse_if_borrowed` — even
though `scope_where` already makes the `UPDATE` match no row: answering a
no-op with 200 and the unchanged row would tell the caller their rename
succeeded. Base's own attempt to *un*-share one is a 409 while another
workspace still has a run to finish against it (`EndpointInUseError`), the same
shape `app.api.toolsets` gives its cascade guard.
"""

import json
import re
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.guards import Admin, CurrentScope, CurrentUser, DbSession, Writer
from app.models import Endpoint, EndpointModel, EndpointModelSource, EndpointPlatform
from app.repos.customers import NotBaseWorkspaceError
from app.repos.endpoints import (
    EndpointInUseError,
    EndpointModelCounts,
    create_endpoint,
    delete_endpoint,
    endpoint_model_counts,
    get_endpoint,
    list_endpoint_models,
    list_endpoints,
    sync_discovered_models,
    touch_endpoint_model,
    update_endpoint,
)
from app.scope import Scope
from app.services.discovery import DISCOVER_TIMEOUT_S, TEST_TIMEOUT_S, probe_models
from app.services.params import ParamsError, parse_params_json, validate_params

router = APIRouter(prefix="/endpoints", tags=["endpoints"])

_BASE_URL_RE = re.compile(r"^https?://", re.IGNORECASE)


# --------------------------------------------------------------------------
# Wire shapes
# --------------------------------------------------------------------------


class EndpointView(BaseModel):
    id: int
    name: str
    base_url: str
    has_api_key: bool
    #: A catalog key, not a credential — drives the frontend's parameter
    #: suggestions and round-trips freely, unlike `api_key`.
    platform: EndpointPlatform
    #: Request-body params merged under every run's own overrides. Content,
    #: not a credential, so it is parsed back to a dict rather than hidden the
    #: way `api_key` is.
    default_params: dict[str, Any] | None
    cpu: str | None
    ram: str | None
    gpu: str | None
    notes: str | None
    #: Shared with every workspace by the Base workspace that owns it.
    is_global: bool
    #: Whether *this* workspace owns the row. False only for a global endpoint
    #: seen from elsewhere, which is exactly when every write below refuses —
    #: the client hides the edit controls rather than rendering them disabled,
    #: so it needs the answer without having to know which workspace is Base.
    editable: bool
    created_at: datetime
    updated_at: datetime
    #: Every model ever seen on this endpoint, and how many are loaded now —
    #: what the endpoints list and detail pages both show.
    model_count: int
    loaded_model_count: int


class EndpointWriteRequest(BaseModel):
    name: str = Field(min_length=1)
    base_url: str = Field(min_length=1)
    api_key: str | None = None
    platform: EndpointPlatform = "generic"
    default_params: dict[str, Any] | None = None
    cpu: str | None = None
    ram: str | None = None
    gpu: str | None = None
    notes: str | None = None
    #: Refused outside Base by `assert_base_workspace`, from inside the
    #: repository — see `app.repos.endpoints`.
    is_global: bool = False

    @field_validator("default_params")
    @classmethod
    def _validate_default_params(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is None:
            return None
        try:
            return validate_params(value, allow_null_values=False)
        except ParamsError as exc:
            raise ValueError(str(exc)) from exc

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Name is required.")
        return cleaned

    @field_validator("base_url")
    @classmethod
    def _normalize_base_url(cls, value: str) -> str:
        cleaned = value.strip().rstrip("/")
        if not cleaned:
            raise ValueError("Base URL is required.")
        if not _BASE_URL_RE.match(cleaned):
            raise ValueError("Base URL must start with http:// or https://")
        return cleaned

    @field_validator("cpu", "ram", "gpu", "notes")
    @classmethod
    def _blank_to_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


class EndpointModelView(BaseModel):
    """One model ever seen on an endpoint.

    `source` says how it was *first* learned about, `currently_loaded` what the
    last discovery pass reported — the endpoint detail page's table and the
    new-run page's "previously seen" list both read exactly this.
    """

    id: int
    endpoint_id: int
    model_id: str
    currently_loaded: bool
    first_seen_at: datetime
    last_seen_at: datetime
    source: EndpointModelSource


class EndpointModelAddRequest(BaseModel):
    model_id: str = Field(min_length=1)

    @field_validator("model_id")
    @classmethod
    def _model_id_not_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Model id is required.")
        return cleaned


class DiscoverResponse(BaseModel):
    ok: bool
    discovered: int = 0
    retired: int = 0
    models: list[str] = []
    error: str | None = None


class TestConnectionResponse(BaseModel):
    ok: bool
    status: int | None = None
    latency_ms: int | None = None
    error: str | None = None


class TestConnectionRequest(BaseModel):
    base_url: str = Field(min_length=1)
    api_key: str | None = None

    # Same normalization `EndpointWriteRequest` applies on create, so a probe of
    # "http://box/v1/" tests the URL the saved row would actually carry rather
    # than reading `.../v1//models` and false-negativing in the dialog.
    @field_validator("base_url")
    @classmethod
    def _normalize_base_url(cls, value: str) -> str:
        cleaned = value.strip().rstrip("/")
        if not cleaned:
            raise ValueError("Base URL is required.")
        return cleaned


def _owns(scope: Scope, endpoint: Endpoint) -> bool:
    """Whether this scope owns the row, rather than merely seeing it.

    Ownership is the test, not `is_global`: a row that is visible without being
    owned can only have arrived through `visible_where`, so this needs no
    lookup of which workspace is Base. A system scope owns nothing and is not a
    request scope anyway.
    """
    return endpoint.customer_id == scope.customer_id


def _refuse_if_borrowed(scope: Scope, endpoint: Endpoint) -> None:
    """403 for a write against a shared endpoint from a workspace that only
    borrows it — see the module docstring for why this is stated rather than
    left to `scope_where`'s silent no-op.
    """
    if _owns(scope, endpoint):
        return
    raise HTTPException(
        status.HTTP_403_FORBIDDEN,
        f'"{endpoint.name}" is shared from the Base workspace. Switch to Base to change it.',
    )


def _to_view(
    endpoint: Endpoint, counts: EndpointModelCounts | None, *, editable: bool
) -> EndpointView:
    return EndpointView(
        id=endpoint.id,
        name=endpoint.name,
        base_url=endpoint.base_url,
        has_api_key=bool(endpoint.api_key),
        platform=endpoint.platform,
        default_params=parse_params_json(endpoint.default_params),
        cpu=endpoint.cpu,
        ram=endpoint.ram,
        gpu=endpoint.gpu,
        notes=endpoint.notes,
        is_global=endpoint.is_global,
        editable=editable,
        created_at=endpoint.created_at,
        updated_at=endpoint.updated_at,
        model_count=counts.total if counts is not None else 0,
        loaded_model_count=counts.loaded if counts is not None else 0,
    )


def _model_view(row: EndpointModel) -> EndpointModelView:
    return EndpointModelView(
        id=row.id,
        endpoint_id=row.endpoint_id,
        model_id=row.model_id,
        currently_loaded=row.currently_loaded,
        first_seen_at=row.first_seen_at,
        last_seen_at=row.last_seen_at,
        source=row.source,
    )


async def _get_or_404(scope: Scope, session: AsyncSession, endpoint_id: int) -> Endpoint:
    endpoint = await get_endpoint(scope, session, endpoint_id)
    if endpoint is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such endpoint.")
    return endpoint


async def _view(scope: Scope, session: AsyncSession, endpoint: Endpoint) -> EndpointView:
    counts = await endpoint_model_counts(scope, session)
    return _to_view(endpoint, counts.get(endpoint.id), editable=_owns(scope, endpoint))


# --------------------------------------------------------------------------
# CRUD
# --------------------------------------------------------------------------


@router.post("/test-connection")
async def test_connection_route(
    body: TestConnectionRequest, actor: Admin
) -> TestConnectionResponse:
    """Probes a base URL before any endpoint row exists for it — the "New
    endpoint" dialog's Test connection button. `Admin`, same reasoning as
    `POST /{endpoint_id}/test`: it exercises a raw API key. Registered ahead
    of `/{endpoint_id}` (a literal segment vs. that route's int path param) so
    "test-connection" is never swallowed by it and 422'd as an unparsable id.
    DB-free like `probe_models` itself — no `Scope`/session needed.
    """
    del actor
    probe = await probe_models(body.base_url, body.api_key or None, timeout=TEST_TIMEOUT_S)
    return TestConnectionResponse(
        ok=probe.ok, status=probe.status, latency_ms=probe.latency_ms, error=probe.error
    )


@router.get("")
async def list_endpoints_route(
    actor: CurrentUser, scope: CurrentScope, session: DbSession, order: str = "name"
) -> list[EndpointView]:
    del actor
    rows = await list_endpoints(scope, session, order=order)
    counts = await endpoint_model_counts(scope, session)
    return [
        _to_view(row, counts.get(row.id), editable=_owns(scope, row)) for row in rows
    ]


@router.get("/{endpoint_id}")
async def get_endpoint_route(
    endpoint_id: int, actor: CurrentUser, scope: CurrentScope, session: DbSession
) -> EndpointView:
    del actor
    endpoint = await _get_or_404(scope, session, endpoint_id)
    return await _view(scope, session, endpoint)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_endpoint_route(
    body: EndpointWriteRequest, actor: Admin, scope: CurrentScope, session: DbSession
) -> EndpointView:
    del actor
    try:
        endpoint = await create_endpoint(
            scope,
            session,
            name=body.name,
            base_url=body.base_url,
            api_key=body.api_key or None,
            platform=body.platform,
            default_params=json.dumps(body.default_params) if body.default_params else None,
            cpu=body.cpu,
            ram=body.ram,
            gpu=body.gpu,
            notes=body.notes,
            is_global=body.is_global,
        )
    except NotBaseWorkspaceError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    await session.commit()
    return await _view(scope, session, endpoint)


@router.put("/{endpoint_id}")
async def update_endpoint_route(
    endpoint_id: int,
    body: EndpointWriteRequest,
    actor: Admin,
    scope: CurrentScope,
    session: DbSession,
) -> EndpointView:
    del actor
    _refuse_if_borrowed(scope, await _get_or_404(scope, session, endpoint_id))

    values: dict[str, object] = {
        "name": body.name,
        "base_url": body.base_url,
        "cpu": body.cpu,
        "ram": body.ram,
        "gpu": body.gpu,
        "notes": body.notes,
    }
    # A credential, not content: only touched when the request actually named
    # the field, so a save that says nothing about it leaves it alone (see the
    # module docstring).
    if "api_key" in body.model_fields_set:
        values["api_key"] = body.api_key or None
    # Patch-like for a different reason: the field defaults to `false`, so a
    # client that simply does not know about sharing would un-share the row on
    # every save. Omitting it has to mean "leave it as it is".
    if "is_global" in body.model_fields_set:
        values["is_global"] = body.is_global
    # Content, not credentials, but patch-like all the same: both default to a
    # value ("generic", None) that a client unaware of them must not stamp
    # over a row that already has something set.
    if "platform" in body.model_fields_set:
        values["platform"] = body.platform
    if "default_params" in body.model_fields_set:
        values["default_params"] = (
            json.dumps(body.default_params) if body.default_params else None
        )

    try:
        await update_endpoint(scope, session, endpoint_id, values)
    except NotBaseWorkspaceError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except EndpointInUseError as exc:
        # Un-sharing a box another workspace is still running against. A 409
        # like the toolset's, and for the same reason: the request is
        # well-formed and refused by the state of other rows, not by a role.
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    await session.commit()
    refreshed = await _get_or_404(scope, session, endpoint_id)
    return await _view(scope, session, refreshed)


@router.delete("/{endpoint_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_endpoint_route(
    endpoint_id: int, actor: Admin, scope: CurrentScope, session: DbSession
) -> None:
    del actor
    _refuse_if_borrowed(scope, await _get_or_404(scope, session, endpoint_id))
    await delete_endpoint(scope, session, endpoint_id)
    await session.commit()


# --------------------------------------------------------------------------
# Models seen on an endpoint
# --------------------------------------------------------------------------


@router.get("/{endpoint_id}/models")
async def list_endpoint_models_route(
    endpoint_id: int, actor: CurrentUser, scope: CurrentScope, session: DbSession
) -> list[EndpointModelView]:
    """Every model ever seen on this endpoint, currently loaded ones first.

    `CurrentUser`, like the endpoint itself: the new-run page needs this list
    for every role, and it names models, never the key they authenticate with.
    """
    del actor
    await _get_or_404(scope, session, endpoint_id)
    rows = await list_endpoint_models(scope, session, endpoint_id=endpoint_id, order="loaded-first")
    return [_model_view(row) for row in rows]


@router.post("/{endpoint_id}/models")
async def add_endpoint_model_route(
    endpoint_id: int,
    body: EndpointModelAddRequest,
    actor: Writer,
    scope: CurrentScope,
    session: DbSession,
) -> EndpointModelView:
    """Records a model the endpoint never advertised — a model that has to be
    named by hand because `/v1/models` does not list it.

    An upsert, not a create: a model already on this endpoint only has its
    `last_seen_at` bumped and is answered with as it stands (`source` keeps
    saying how it was *first* learned about), so there is no 201 to report and
    no conflict to refuse. `Writer`, matching `POST /discover` — a model id is
    content, not a credential.
    """
    del actor
    await _get_or_404(scope, session, endpoint_id)
    await touch_endpoint_model(
        scope, session, endpoint_id=endpoint_id, model_id=body.model_id, source="manual"
    )
    await session.commit()

    rows = await list_endpoint_models(scope, session, endpoint_id=endpoint_id)
    row = next((candidate for candidate in rows if candidate.model_id == body.model_id), None)
    if row is None:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, "The model could not be recorded."
        )
    return _model_view(row)


# --------------------------------------------------------------------------
# Live probes
# --------------------------------------------------------------------------


@router.post("/{endpoint_id}/discover")
async def discover_endpoint_route(
    endpoint_id: int, actor: Writer, scope: CurrentScope, session: DbSession
) -> DiscoverResponse:
    """Reads `{base_url}/models` live and upserts what it finds.

    `Writer`, not `Admin`: this only ever reveals model ids, never the API key
    it authenticates with, and every writer needs it on the new-run page.
    """
    del actor
    endpoint = await _get_or_404(scope, session, endpoint_id)

    probe = await probe_models(endpoint.base_url, endpoint.api_key, timeout=DISCOVER_TIMEOUT_S)
    if not probe.ok or probe.model_ids is None:
        return DiscoverResponse(ok=False, error=probe.error)

    sync = await sync_discovered_models(scope, session, endpoint_id, probe.model_ids)
    await session.commit()
    return DiscoverResponse(
        ok=True, discovered=sync.discovered, retired=sync.retired, models=probe.model_ids
    )


@router.post("/{endpoint_id}/test")
async def test_endpoint_route(
    endpoint_id: int, actor: Admin, scope: CurrentScope, session: DbSession
) -> TestConnectionResponse:
    """Probes the endpoint with its stored credentials — `Admin` because
    unlike discovery, a failure here can reveal whether an API key is valid.
    """
    del actor
    endpoint = await _get_or_404(scope, session, endpoint_id)

    probe = await probe_models(endpoint.base_url, endpoint.api_key, timeout=TEST_TIMEOUT_S)
    return TestConnectionResponse(
        ok=probe.ok, status=probe.status, latency_ms=probe.latency_ms, error=probe.error
    )
