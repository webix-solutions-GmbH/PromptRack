"""`/api/machines` — endpoints (base URL + credentials) and model discovery.

Reading a machine needs only a signed-in actor (the new-run page needs every
role to see the list), writing one is `Admin` (it holds an API key), and
`POST /discover` sits in between at `Writer` — it only reads model ids back,
and every writer needs it on the new-run page, exactly the split
`git show master:src/app/api/machines/[id]/discover/route.ts` and
`.../test/route.ts` made.

The stored `api_key` is never round-tripped back to the client: a machine view
carries `has_api_key` instead. That is a deliberate deviation from the old
Next.js app, which could pre-fill the raw key into a server-rendered `<form>`
without it ever reaching a browser that could not already see it (the admin
who loaded the page). A JSON API has no such boundary, so `PUT` treats
`api_key` as write-only and patch-like: omit it to leave the stored key
untouched, send `""`/`null` to clear it, send a value to replace it.
"""

import re
from datetime import datetime

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.guards import Admin, CurrentScope, CurrentUser, DbSession, Writer
from app.models import Machine, MachineModel, MachineModelSource
from app.repos.machines import (
    MachineModelCounts,
    create_machine,
    delete_machine,
    get_machine,
    list_machine_models,
    list_machines,
    machine_model_counts,
    sync_discovered_models,
    touch_machine_model,
    update_machine,
)
from app.scope import Scope
from app.services.discovery import DISCOVER_TIMEOUT_S, TEST_TIMEOUT_S, probe_models

router = APIRouter(prefix="/machines", tags=["machines"])

_BASE_URL_RE = re.compile(r"^https?://", re.IGNORECASE)


# --------------------------------------------------------------------------
# Wire shapes
# --------------------------------------------------------------------------


class MachineView(BaseModel):
    id: int
    name: str
    base_url: str
    has_api_key: bool
    cpu: str | None
    ram: str | None
    gpu: str | None
    notes: str | None
    created_at: datetime
    updated_at: datetime
    #: Every model ever seen on this machine, and how many are loaded now —
    #: what the machines list and detail pages both show.
    model_count: int
    loaded_model_count: int


class MachineWriteRequest(BaseModel):
    name: str = Field(min_length=1)
    base_url: str = Field(min_length=1)
    api_key: str | None = None
    cpu: str | None = None
    ram: str | None = None
    gpu: str | None = None
    notes: str | None = None

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


class MachineModelView(BaseModel):
    """One model ever seen on a machine.

    `source` says how it was *first* learned about, `currently_loaded` what the
    last discovery pass reported — the machine detail page's table and the
    new-run page's "previously seen" list both read exactly this.
    """

    id: int
    machine_id: int
    model_id: str
    currently_loaded: bool
    first_seen_at: datetime
    last_seen_at: datetime
    source: MachineModelSource


class MachineModelAddRequest(BaseModel):
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


def _to_view(machine: Machine, counts: MachineModelCounts | None) -> MachineView:
    return MachineView(
        id=machine.id,
        name=machine.name,
        base_url=machine.base_url,
        has_api_key=bool(machine.api_key),
        cpu=machine.cpu,
        ram=machine.ram,
        gpu=machine.gpu,
        notes=machine.notes,
        created_at=machine.created_at,
        updated_at=machine.updated_at,
        model_count=counts.total if counts is not None else 0,
        loaded_model_count=counts.loaded if counts is not None else 0,
    )


def _model_view(row: MachineModel) -> MachineModelView:
    return MachineModelView(
        id=row.id,
        machine_id=row.machine_id,
        model_id=row.model_id,
        currently_loaded=row.currently_loaded,
        first_seen_at=row.first_seen_at,
        last_seen_at=row.last_seen_at,
        source=row.source,
    )


async def _get_or_404(scope: Scope, session: AsyncSession, machine_id: int) -> Machine:
    machine = await get_machine(scope, session, machine_id)
    if machine is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such machine.")
    return machine


async def _view(scope: Scope, session: AsyncSession, machine: Machine) -> MachineView:
    counts = await machine_model_counts(scope, session)
    return _to_view(machine, counts.get(machine.id))


# --------------------------------------------------------------------------
# CRUD
# --------------------------------------------------------------------------


@router.get("")
async def list_machines_endpoint(
    actor: CurrentUser, scope: CurrentScope, session: DbSession, order: str = "name"
) -> list[MachineView]:
    del actor
    rows = await list_machines(scope, session, order=order)
    counts = await machine_model_counts(scope, session)
    return [_to_view(row, counts.get(row.id)) for row in rows]


@router.get("/{machine_id}")
async def get_machine_endpoint(
    machine_id: int, actor: CurrentUser, scope: CurrentScope, session: DbSession
) -> MachineView:
    del actor
    machine = await _get_or_404(scope, session, machine_id)
    return await _view(scope, session, machine)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_machine_endpoint(
    body: MachineWriteRequest, actor: Admin, scope: CurrentScope, session: DbSession
) -> MachineView:
    del actor
    machine = await create_machine(
        scope,
        session,
        name=body.name,
        base_url=body.base_url,
        api_key=body.api_key or None,
        cpu=body.cpu,
        ram=body.ram,
        gpu=body.gpu,
        notes=body.notes,
    )
    await session.commit()
    return await _view(scope, session, machine)


@router.put("/{machine_id}")
async def update_machine_endpoint(
    machine_id: int,
    body: MachineWriteRequest,
    actor: Admin,
    scope: CurrentScope,
    session: DbSession,
) -> MachineView:
    del actor
    await _get_or_404(scope, session, machine_id)

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

    await update_machine(scope, session, machine_id, values)
    await session.commit()
    refreshed = await _get_or_404(scope, session, machine_id)
    return await _view(scope, session, refreshed)


@router.delete("/{machine_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_machine_endpoint(
    machine_id: int, actor: Admin, scope: CurrentScope, session: DbSession
) -> None:
    del actor
    await _get_or_404(scope, session, machine_id)
    await delete_machine(scope, session, machine_id)
    await session.commit()


# --------------------------------------------------------------------------
# Models seen on a machine
# --------------------------------------------------------------------------


@router.get("/{machine_id}/models")
async def list_machine_models_endpoint(
    machine_id: int, actor: CurrentUser, scope: CurrentScope, session: DbSession
) -> list[MachineModelView]:
    """Every model ever seen on this machine, currently loaded ones first.

    `CurrentUser`, like the machine itself: the new-run page needs this list
    for every role, and it names models, never the key they authenticate with.
    """
    del actor
    await _get_or_404(scope, session, machine_id)
    rows = await list_machine_models(scope, session, machine_id=machine_id, order="loaded-first")
    return [_model_view(row) for row in rows]


@router.post("/{machine_id}/models")
async def add_machine_model_endpoint(
    machine_id: int,
    body: MachineModelAddRequest,
    actor: Writer,
    scope: CurrentScope,
    session: DbSession,
) -> MachineModelView:
    """Records a model the endpoint never advertised — a model that has to be
    named by hand because `/v1/models` does not list it.

    An upsert, not a create: a model already on this machine only has its
    `last_seen_at` bumped and is answered with as it stands (`source` keeps
    saying how it was *first* learned about), so there is no 201 to report and
    no conflict to refuse. `Writer`, matching `POST /discover` — a model id is
    content, not a credential.
    """
    del actor
    await _get_or_404(scope, session, machine_id)
    await touch_machine_model(
        scope, session, machine_id=machine_id, model_id=body.model_id, source="manual"
    )
    await session.commit()

    rows = await list_machine_models(scope, session, machine_id=machine_id)
    row = next((candidate for candidate in rows if candidate.model_id == body.model_id), None)
    if row is None:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, "The model could not be recorded."
        )
    return _model_view(row)


# --------------------------------------------------------------------------
# Live probes
# --------------------------------------------------------------------------


@router.post("/{machine_id}/discover")
async def discover_machine_endpoint(
    machine_id: int, actor: Writer, scope: CurrentScope, session: DbSession
) -> DiscoverResponse:
    """Reads `{base_url}/models` live and upserts what it finds.

    `Writer`, not `Admin`: this only ever reveals model ids, never the API key
    it authenticates with, and every writer needs it on the new-run page.
    """
    del actor
    machine = await _get_or_404(scope, session, machine_id)

    probe = await probe_models(machine.base_url, machine.api_key, timeout=DISCOVER_TIMEOUT_S)
    if not probe.ok or probe.model_ids is None:
        return DiscoverResponse(ok=False, error=probe.error)

    sync = await sync_discovered_models(scope, session, machine_id, probe.model_ids)
    await session.commit()
    return DiscoverResponse(
        ok=True, discovered=sync.discovered, retired=sync.retired, models=probe.model_ids
    )


@router.post("/{machine_id}/test")
async def test_machine_endpoint(
    machine_id: int, actor: Admin, scope: CurrentScope, session: DbSession
) -> TestConnectionResponse:
    """Probes the endpoint with its stored credentials — `Admin` because
    unlike discovery, a failure here can reveal whether an API key is valid.
    """
    del actor
    machine = await _get_or_404(scope, session, machine_id)

    probe = await probe_models(machine.base_url, machine.api_key, timeout=TEST_TIMEOUT_S)
    return TestConnectionResponse(
        ok=probe.ok, status=probe.status, latency_ms=probe.latency_ms, error=probe.error
    )
