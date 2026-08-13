"""`/api/tokens` — API tokens for the MCP endpoint (mounted for real in Phase 6).

Any signed-in role may hold a token: a viewer's token simply cannot call a
write tool, once `app.mcp` checks its role the same way every other guard
does (see `app.auth.tokens`). Ownership is baked into every query
(`user_id = actor.user_id`), so one user's tokens are never visible to, or
revocable by, another — there is no admin override.
"""

from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.auth import tokens as token_store
from app.auth.guards import CurrentUser, DbSession
from app.models import ApiToken
from app.repos.scoped import utc_now

router = APIRouter(prefix="/tokens", tags=["tokens"])

#: A ceiling on the *finite* expiry option only — a token minted with no
#: `expires_in_days` has no expiry at all, matching the old app.
MAX_EXPIRY_DAYS = 3650


class TokenView(BaseModel):
    id: int
    name: str
    display_prefix: str
    created_at: datetime
    last_used_at: datetime | None
    expires_at: datetime | None
    revoked_at: datetime | None


class CreatedTokenView(TokenView):
    #: The raw value — present exactly once, only on the response to `POST`.
    #: Never stored, never returned again by `GET`.
    token: str


class CreateTokenRequest(BaseModel):
    name: str = Field(min_length=1)
    expires_in_days: int | None = Field(default=None, ge=1, le=MAX_EXPIRY_DAYS)


def _view(token: ApiToken) -> TokenView:
    return TokenView(
        id=token.id,
        name=token.name,
        display_prefix=token.display_prefix,
        created_at=token.created_at,
        last_used_at=token.last_used_at,
        expires_at=token.expires_at,
        revoked_at=token.revoked_at,
    )


@router.get("")
async def list_tokens(actor: CurrentUser, session: DbSession) -> list[TokenView]:
    rows = await token_store.list_tokens(session, actor.user_id)
    return [_view(row) for row in rows]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_token(
    body: CreateTokenRequest, actor: CurrentUser, session: DbSession
) -> CreatedTokenView:
    expires_at = (
        utc_now() + timedelta(days=body.expires_in_days)
        if body.expires_in_days is not None
        else None
    )
    token, raw = await token_store.create_token(
        session, user_id=actor.user_id, name=body.name.strip(), expires_at=expires_at
    )
    await session.commit()
    return CreatedTokenView(**_view(token).model_dump(), token=raw)


@router.delete("/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_token(token_id: int, actor: CurrentUser, session: DbSession) -> None:
    revoked = await token_store.revoke_token(session, token_id=token_id, user_id=actor.user_id)
    if not revoked:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such token.")
    await session.commit()
