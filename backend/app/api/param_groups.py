"""`/api/param-groups` — named, reusable request-param presets.

Params are content, not credentials, so the content-vs-credentials split gives
groups `Writer` for mutation and `CurrentUser` for reads, same as prompts and
test groups. Deleting one never touches a past run — the run froze the merged
params and the selected names at creation, and holds no FK here.
"""

import json
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.guards import CurrentScope, CurrentUser, DbSession, Writer
from app.models import ParamGroup
from app.repos.param_groups import (
    create_param_group,
    delete_param_group,
    get_param_group,
    list_param_groups,
    update_param_group,
)
from app.scope import Scope
from app.services.params import ParamsError, parse_params_json, validate_params

router = APIRouter(prefix="/param-groups", tags=["param-groups"])


# --------------------------------------------------------------------------
# Wire shapes
# --------------------------------------------------------------------------


class ParamGroupView(BaseModel):
    id: int
    name: str
    description: str | None
    params: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class ParamGroupWriteRequest(BaseModel):
    name: str = Field(min_length=1)
    description: str | None = None
    #: A group is a *patch* over the endpoint defaults it lands on, so a null
    #: value is legal here — it unsets a default, exactly like a run override.
    params: dict[str, Any]

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

    @field_validator("params")
    @classmethod
    def _validate_params(cls, value: dict[str, Any]) -> dict[str, Any]:
        try:
            return validate_params(value, allow_null_values=True)
        except ParamsError as exc:
            raise ValueError(str(exc)) from exc


# --------------------------------------------------------------------------
# View builders / lookups
# --------------------------------------------------------------------------


def _view(group: ParamGroup) -> ParamGroupView:
    return ParamGroupView(
        id=group.id,
        name=group.name,
        description=group.description,
        params=parse_params_json(group.params) or {},
        created_at=group.created_at,
        updated_at=group.updated_at,
    )


async def _get_or_404(scope: Scope, session: AsyncSession, param_group_id: int) -> ParamGroup:
    group = await get_param_group(scope, session, param_group_id)
    if group is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such parameter group.")
    return group


# --------------------------------------------------------------------------
# CRUD
# --------------------------------------------------------------------------


@router.get("")
async def list_param_groups_endpoint(
    actor: CurrentUser, scope: CurrentScope, session: DbSession
) -> list[ParamGroupView]:
    del actor
    groups = await list_param_groups(scope, session)
    return [_view(group) for group in groups]


@router.get("/{param_group_id}")
async def get_param_group_endpoint(
    param_group_id: int, actor: CurrentUser, scope: CurrentScope, session: DbSession
) -> ParamGroupView:
    del actor
    group = await _get_or_404(scope, session, param_group_id)
    return _view(group)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_param_group_endpoint(
    body: ParamGroupWriteRequest, actor: Writer, scope: CurrentScope, session: DbSession
) -> ParamGroupView:
    del actor
    group = await create_param_group(
        scope,
        session,
        name=body.name,
        description=body.description,
        params=json.dumps(body.params),
    )
    await session.commit()
    return _view(group)


@router.put("/{param_group_id}")
async def update_param_group_endpoint(
    param_group_id: int,
    body: ParamGroupWriteRequest,
    actor: Writer,
    scope: CurrentScope,
    session: DbSession,
) -> ParamGroupView:
    del actor
    await _get_or_404(scope, session, param_group_id)
    await update_param_group(
        scope,
        session,
        param_group_id,
        {
            "name": body.name,
            "description": body.description,
            "params": json.dumps(body.params),
        },
    )
    await session.commit()
    return _view(await _get_or_404(scope, session, param_group_id))


@router.delete("/{param_group_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_param_group_endpoint(
    param_group_id: int, actor: Writer, scope: CurrentScope, session: DbSession
) -> None:
    """Past runs are unaffected — they froze the merged params and the names."""
    del actor
    await _get_or_404(scope, session, param_group_id)
    await delete_param_group(scope, session, param_group_id)
    await session.commit()
