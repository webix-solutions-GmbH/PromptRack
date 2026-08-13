"""`/api/customers` — workspaces.

Listing and switching are already covered by `/api/auth/me` and
`/api/auth/switch-customer`; this router is the workspace *management*
surface — create, rename, archive and the guarded delete — which is why every
mutation but delete is `Writer` and delete alone is `Admin`, exactly the split
`git show master:src/actions/customers.ts` used.
"""

from datetime import datetime

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.guards import Admin, CurrentUser, DbSession, Writer
from app.models import Customer
from app.repos.customers import (
    CustomerContentCounts,
    count_customer_content,
    create_customer,
    delete_customer,
    find_customer_by_name,
    get_customer,
    list_customers,
    set_customer_archived,
    update_customer,
)
from app.repos.scoped import utc_now

router = APIRouter(prefix="/customers", tags=["customers"])


# --------------------------------------------------------------------------
# Wire shapes
# --------------------------------------------------------------------------


class ContentCountsView(BaseModel):
    machines: int
    prompts: int
    toolsets: int
    test_groups: int
    runs: int
    total: int


class CustomerView(BaseModel):
    id: int
    name: str
    description: str | None
    archived: bool
    created_at: datetime
    updated_at: datetime
    content: ContentCountsView


class CustomerWriteRequest(BaseModel):
    name: str = Field(min_length=1)
    description: str | None = None

    @field_validator("name")
    @classmethod
    def _not_blank(cls, value: str) -> str:
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


class ArchiveRequest(BaseModel):
    archived: bool


def _content_view(counts: CustomerContentCounts) -> ContentCountsView:
    return ContentCountsView(
        machines=counts.machines,
        prompts=counts.prompts,
        toolsets=counts.toolsets,
        test_groups=counts.test_groups,
        runs=counts.runs,
        total=counts.total,
    )


async def _get_or_404(session: AsyncSession, customer_id: int) -> Customer:
    customer = await get_customer(session, customer_id)
    if customer is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That workspace no longer exists.")
    return customer


async def _view(session: AsyncSession, customer_id: int) -> CustomerView:
    """Builds the response by id, re-reading the row rather than trusting
    whatever instance the caller has in hand.

    `update_customer`/`set_customer_archived` are Core-style ``UPDATE``
    statements (see `app.repos.customers`); SQLAlchemy cannot evaluate
    `updated_at`'s ``onupdate=func.now()`` against the identity map and
    expires that attribute instead, so touching it on the pre-commit instance
    afterwards would try to lazy-load outside of an ``await`` and raise
    ``MissingGreenlet``. Re-fetching after `session.commit()` sidesteps that
    entirely — the same pattern `app.api.machines` uses.
    """
    customer = await _get_or_404(session, customer_id)
    counts = await count_customer_content(session, customer.id)
    return CustomerView(
        id=customer.id,
        name=customer.name,
        description=customer.description,
        archived=customer.archived_at is not None,
        created_at=customer.created_at,
        updated_at=customer.updated_at,
        content=_content_view(counts),
    )


def _held_contents(counts: CustomerContentCounts) -> list[str]:
    """The delete guard's "still holds ..." clause, one entry per non-empty
    root table — exactly what `ON DELETE RESTRICT` is standing in front of.
    """
    labels = (
        (counts.machines, "machine"),
        (counts.prompts, "prompt"),
        (counts.toolsets, "toolset"),
        (counts.test_groups, "test group"),
        (counts.runs, "run"),
    )
    return [f"{count} {label}{'' if count == 1 else 's'}" for count, label in labels if count]


# --------------------------------------------------------------------------
# Endpoints
# --------------------------------------------------------------------------


@router.get("")
async def list_customers_endpoint(actor: CurrentUser, session: DbSession) -> list[CustomerView]:
    del actor
    rows = await list_customers(session)
    return [await _view(session, row.id) for row in rows]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_customer_endpoint(
    body: CustomerWriteRequest, actor: Writer, session: DbSession
) -> CustomerView:
    del actor
    clash = await find_customer_by_name(session, body.name)
    if clash is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f'A workspace named "{clash.name}" already exists (id {clash.id}).',
        )
    customer = await create_customer(session, name=body.name, description=body.description)
    await session.commit()
    return await _view(session, customer.id)


@router.put("/{customer_id}")
async def update_customer_endpoint(
    customer_id: int, body: CustomerWriteRequest, actor: Writer, session: DbSession
) -> CustomerView:
    del actor
    await _get_or_404(session, customer_id)
    clash = await find_customer_by_name(session, body.name, except_id=customer_id)
    if clash is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f'A workspace named "{clash.name}" already exists (id {clash.id}).',
        )
    await update_customer(session, customer_id, name=body.name, description=body.description)
    await session.commit()
    return await _view(session, customer_id)


@router.post("/{customer_id}/archive")
async def set_customer_archived_endpoint(
    customer_id: int, body: ArchiveRequest, actor: Writer, session: DbSession
) -> CustomerView:
    """Hides a workspace from the switcher without touching anything it owns.

    Archiving the caller's own active workspace is allowed: the next request's
    `current_scope()` falls back to the oldest live workspace and heals the
    stored pointer — that fallback is exactly why this can be this
    unceremonious.
    """
    del actor
    await _get_or_404(session, customer_id)
    await set_customer_archived(session, customer_id, utc_now() if body.archived else None)
    await session.commit()
    return await _view(session, customer_id)


@router.delete("/{customer_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_customer_endpoint(customer_id: int, actor: Admin, session: DbSession) -> None:
    """Deletes an empty workspace.

    Admin-only, unlike the rest: a workspace holds machines, i.e. base URLs
    and API keys, and the deletion is irreversible. The FK `RESTRICT` on all
    five root tables is the backstop; the two checks below exist purely so the
    caller gets a sentence instead of a constraint violation.
    """
    del actor
    customer = await _get_or_404(session, customer_id)

    all_customers = await list_customers(session)
    if len(all_customers) <= 1:
        # Every scope resolves to a workspace, so there has to be one left to
        # resolve to; archiving is the way to retire the last engagement.
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This is the only workspace. Archive it instead of deleting it.",
        )

    counts = await count_customer_content(session, customer_id)
    held = _held_contents(counts)
    if held:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f'Workspace "{customer.name}" still holds {", ".join(held)}. '
            "Archive it instead, or delete its contents first.",
        )

    await delete_customer(session, customer_id)
    await session.commit()
