"""`/api/test-groups` — the suite's unit of selection.

Groups hold no credentials, so the content-vs-credentials split gives them
`Writer` for mutation and `CurrentUser` for reads, same as test cases and
prompts. Deleting a group cascades to its test cases at the database level
(`test_cases.group_id` is `ON DELETE CASCADE`) — unlike a customer workspace,
there is no "refuses while non-empty" guard here: a group is a label on a set
of test cases, not an engagement with billing behind it.
"""

from datetime import datetime

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.guards import CurrentScope, CurrentUser, DbSession, Writer
from app.models import TestGroup
from app.repos.test_cases import (
    create_test_group,
    delete_test_group,
    get_test_group,
    list_test_groups,
    test_case_counts_by_group,
    update_test_group,
)
from app.scope import Scope

router = APIRouter(prefix="/test-groups", tags=["test-groups"])


# --------------------------------------------------------------------------
# Wire shapes
# --------------------------------------------------------------------------


class TestGroupView(BaseModel):
    id: int
    name: str
    description: str | None
    sort_order: int
    test_case_count: int
    created_at: datetime


class TestGroupWriteRequest(BaseModel):
    name: str = Field(min_length=1)
    description: str | None = None
    sort_order: int = 0

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


# --------------------------------------------------------------------------
# View builders / lookups
# --------------------------------------------------------------------------


def _view(group: TestGroup, counts: dict[int, int]) -> TestGroupView:
    return TestGroupView(
        id=group.id,
        name=group.name,
        description=group.description,
        sort_order=group.sort_order,
        test_case_count=counts.get(group.id, 0),
        created_at=group.created_at,
    )


async def _get_or_404(scope: Scope, session: AsyncSession, group_id: int) -> TestGroup:
    group = await get_test_group(scope, session, group_id)
    if group is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such test group.")
    return group


# --------------------------------------------------------------------------
# CRUD
# --------------------------------------------------------------------------


@router.get("")
async def list_test_groups_endpoint(
    actor: CurrentUser, scope: CurrentScope, session: DbSession, order: str = "sort-name"
) -> list[TestGroupView]:
    del actor
    groups = await list_test_groups(scope, session, order=order)
    counts = await test_case_counts_by_group(scope, session)
    return [_view(group, counts) for group in groups]


@router.get("/{group_id}")
async def get_test_group_endpoint(
    group_id: int, actor: CurrentUser, scope: CurrentScope, session: DbSession
) -> TestGroupView:
    del actor
    group = await _get_or_404(scope, session, group_id)
    counts = await test_case_counts_by_group(scope, session)
    return _view(group, counts)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_test_group_endpoint(
    body: TestGroupWriteRequest, actor: Writer, scope: CurrentScope, session: DbSession
) -> TestGroupView:
    del actor
    group = await create_test_group(
        scope, session, name=body.name, description=body.description, sort_order=body.sort_order
    )
    await session.commit()
    return _view(group, {})


@router.put("/{group_id}")
async def update_test_group_endpoint(
    group_id: int,
    body: TestGroupWriteRequest,
    actor: Writer,
    scope: CurrentScope,
    session: DbSession,
) -> TestGroupView:
    del actor
    await _get_or_404(scope, session, group_id)
    await update_test_group(
        scope,
        session,
        group_id,
        {"name": body.name, "description": body.description, "sort_order": body.sort_order},
    )
    await session.commit()
    refreshed = await _get_or_404(scope, session, group_id)
    counts = await test_case_counts_by_group(scope, session)
    return _view(refreshed, counts)


@router.delete("/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_test_group_endpoint(
    group_id: int, actor: Writer, scope: CurrentScope, session: DbSession
) -> None:
    """Cascades to the group's test cases; past runs are unaffected — they
    carry their own frozen snapshot, not a live reference to the group.
    """
    del actor
    await _get_or_404(scope, session, group_id)
    await delete_test_group(scope, session, group_id)
    await session.commit()
