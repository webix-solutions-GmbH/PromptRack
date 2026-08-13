"""`/api/prompts` — the versioned asset: draft CRUD, commits, the deployed
pointer, baseline attribution, restore, and diffs against any two texts.

Prompts hold no credentials (unlike machines/toolsets), so the whole surface
sits at `Writer` for mutation and `CurrentUser` for reads — the content vs.
credentials split the plan draws for the pivot. `mark_deployed`-equivalent
(`POST /{id}/deploy`) stays reachable from the UI only in the sense that
nothing here is MCP-specific; the MCP server (Phase 6) simply never mounts a
`deploy` tool, mirroring the old app's `mark_deployed` being UI-only.

`GET /versions/{version_id}` and `POST /versions/{version_id}/baseline` live
under this same router rather than nested under `/{prompt_id}/...`: a version
id already names its prompt unambiguously (see `app.repos.prompt_versions`),
and a baseline is set from a version, not from a prompt. Route resolution
never collides with `/{prompt_id}` — that pattern matches exactly one path
segment, while `/versions/...` is always two or more, so Starlette picks the
literal-segment route regardless of registration order.
"""

from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.guards import CurrentScope, CurrentUser, DbSession, Writer
from app.models import Prompt, PromptVersion
from app.repos.prompt_versions import (
    NoChangesError,
    NotAttributedError,
    VersionError,
    commit_version,
    get_version,
    list_version_refs,
    list_versions,
    set_baseline,
    set_deployed,
)
from app.repos.prompts import (
    create_prompt,
    delete_prompt,
    get_prompt,
    list_prompts,
    update_prompt,
)
from app.scope import CrossCustomerError, Scope
from app.services.attribution import VersionRef, head_version, is_dirty
from app.services.diff import unified_diff

router = APIRouter(prefix="/prompts", tags=["prompts"])


# --------------------------------------------------------------------------
# Wire shapes
# --------------------------------------------------------------------------


class VersionSummary(BaseModel):
    """Just enough of a version to answer "deployed v3, head is v5" —
    the full row is what `GET /versions/{id}` or `GET /{id}/versions` return.
    """

    id: int
    version: int


class PromptView(BaseModel):
    id: int
    name: str
    #: The mutable draft — what the editor writes and what a run tests.
    content: str
    #: `True` when the draft differs from the head version (or nothing has
    #: ever been committed). Mirrors `app.services.attribution.is_dirty`.
    dirty: bool
    head_version: VersionSummary | None
    deployed_version: VersionSummary | None
    deployed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class PromptWriteRequest(BaseModel):
    name: str = Field(min_length=1)
    content: str = Field(min_length=1)

    @field_validator("name", "content")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("This field is required.")
        return cleaned


class PromptPatchRequest(BaseModel):
    """Partial update of the draft — either field, or both, may be omitted."""

    name: str | None = None
    content: str | None = None

    @field_validator("name", "content")
    @classmethod
    def _not_blank_if_present(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("This field cannot be blank.")
        return cleaned


class PromptVersionView(BaseModel):
    id: int
    prompt_id: int
    version: int
    content: str
    message: str
    created_at: datetime
    created_by: int | None
    baseline_run_id: int | None


class CommitRequest(BaseModel):
    message: str = Field(min_length=1)

    @field_validator("message")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("A commit message is required.")
        return cleaned


class DeployRequest(BaseModel):
    version_id: int


class BaselineRequest(BaseModel):
    run_id: int


class RestoreRequest(BaseModel):
    version_id: int


class DiffView(BaseModel):
    from_label: str
    to_label: str
    #: One unified-diff line per entry, no trailing newlines. See
    #: `app.services.diff.unified_diff`.
    diff: list[str]


# --------------------------------------------------------------------------
# View builders / lookups
# --------------------------------------------------------------------------


def _version_summary(version_id: int | None, refs: list[VersionRef]) -> VersionSummary | None:
    if version_id is None:
        return None
    for ref in refs:
        if ref.id == version_id:
            return VersionSummary(id=ref.id, version=ref.version)
    # Referential integrity means this shouldn't happen, but a stale pointer
    # degrades to "unknown" rather than a 500.
    return None


def _prompt_view(prompt: Prompt, refs: list[VersionRef]) -> PromptView:
    head = head_version(refs)
    return PromptView(
        id=prompt.id,
        name=prompt.name,
        content=prompt.content,
        dirty=is_dirty(prompt.content, refs),
        head_version=_version_summary(head.id, refs) if head is not None else None,
        deployed_version=_version_summary(prompt.deployed_version_id, refs),
        deployed_at=prompt.deployed_at,
        created_at=prompt.created_at,
        updated_at=prompt.updated_at,
    )


def _version_view(version: PromptVersion) -> PromptVersionView:
    return PromptVersionView(
        id=version.id,
        prompt_id=version.prompt_id,
        version=version.version,
        content=version.content,
        message=version.message,
        created_at=version.created_at,
        created_by=version.created_by,
        baseline_run_id=version.baseline_run_id,
    )


async def _get_prompt_or_404(scope: Scope, session: AsyncSession, prompt_id: int) -> Prompt:
    prompt = await get_prompt(scope, session, prompt_id)
    if prompt is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such prompt.")
    return prompt


async def _refs_for(scope: Scope, session: AsyncSession, prompt_id: int) -> list[VersionRef]:
    return (await list_version_refs(scope, session, [prompt_id])).get(prompt_id, [])


async def _prompt_view_by_id(
    scope: Scope, session: AsyncSession, prompt_id: int
) -> PromptView:
    prompt = await _get_prompt_or_404(scope, session, prompt_id)
    return _prompt_view(prompt, await _refs_for(scope, session, prompt_id))


async def _get_version_or_404(
    scope: Scope, session: AsyncSession, prompt_id: int, version_id: int
) -> PromptVersion:
    """A version scoped through the given prompt: belonging to a foreign
    workspace or to a *different* prompt in this one are both a 404 — the
    caller asked for "this prompt's version N", not "some version somewhere".
    """
    version = await get_version(scope, session, version_id)
    if version is None or version.prompt_id != prompt_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such version.")
    return version


async def _resolve_diff_ref(
    scope: Scope, session: AsyncSession, prompt: Prompt, ref: str
) -> tuple[str, str]:
    """`"draft"` or a version id -> (label, content), for `GET /{id}/diff`."""
    if ref == "draft":
        return "draft", prompt.content
    if not ref.isdigit():
        # A literal `422` rather than Starlette's now-deprecated
        # `HTTP_422_UNPROCESSABLE_ENTITY` constant, matching `app.main`'s own
        # validation-error handler.
        raise HTTPException(
            422, f'Invalid diff reference "{ref}" — use "draft" or a version id.'
        )
    version = await _get_version_or_404(scope, session, prompt.id, int(ref))
    return f"v{version.version}", version.content


# --------------------------------------------------------------------------
# Prompt CRUD
# --------------------------------------------------------------------------


@router.get("")
async def list_prompts_endpoint(
    actor: CurrentUser, scope: CurrentScope, session: DbSession, order: str = "name"
) -> list[PromptView]:
    del actor
    prompts = await list_prompts(scope, session, order=order)
    refs_by_prompt = await list_version_refs(scope, session, [p.id for p in prompts])
    return [_prompt_view(p, refs_by_prompt.get(p.id, [])) for p in prompts]


@router.get("/{prompt_id}")
async def get_prompt_endpoint(
    prompt_id: int, actor: CurrentUser, scope: CurrentScope, session: DbSession
) -> PromptView:
    del actor
    return await _prompt_view_by_id(scope, session, prompt_id)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_prompt_endpoint(
    body: PromptWriteRequest, actor: Writer, scope: CurrentScope, session: DbSession
) -> PromptView:
    del actor
    prompt = await create_prompt(scope, session, name=body.name, content=body.content)
    await session.commit()
    return _prompt_view(prompt, [])


@router.patch("/{prompt_id}")
async def patch_prompt_endpoint(
    prompt_id: int,
    body: PromptPatchRequest,
    actor: Writer,
    scope: CurrentScope,
    session: DbSession,
) -> PromptView:
    """Edits the draft — the field(s) actually present in the body change,
    the rest are left alone. This is how the editor's autosave works: it can
    send `{"content": "..."}` on every keystroke without re-sending the name.
    """
    del actor
    await _get_prompt_or_404(scope, session, prompt_id)

    values: dict[str, object] = {}
    if "name" in body.model_fields_set:
        values["name"] = body.name
    if "content" in body.model_fields_set:
        values["content"] = body.content

    if values:
        await update_prompt(scope, session, prompt_id, values)
        await session.commit()
    return await _prompt_view_by_id(scope, session, prompt_id)


@router.delete("/{prompt_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_prompt_endpoint(
    prompt_id: int, actor: Writer, scope: CurrentScope, session: DbSession
) -> None:
    """Deletes the asset and, by cascade, its whole version history. Past
    runs are unaffected — they carry their own frozen effective system
    prompt, and `run_results.prompt_version_id` is `SET NULL`.
    """
    del actor
    await _get_prompt_or_404(scope, session, prompt_id)
    await delete_prompt(scope, session, prompt_id)
    await session.commit()


# --------------------------------------------------------------------------
# Version history: commit, list, read, deploy, restore
# --------------------------------------------------------------------------


@router.post("/{prompt_id}/commit", status_code=status.HTTP_201_CREATED)
async def commit_prompt_endpoint(
    prompt_id: int,
    body: CommitRequest,
    actor: Writer,
    scope: CurrentScope,
    session: DbSession,
) -> PromptVersionView:
    """Freezes the current draft as the next version. Refused when the draft
    equals the head version — see `app.repos.prompt_versions.NoChangesError`.
    """
    await _get_prompt_or_404(scope, session, prompt_id)
    try:
        version = await commit_version(
            scope, session, prompt_id, message=body.message, user_id=actor.user_id
        )
    except NoChangesError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    await session.commit()
    return _version_view(version)


@router.get("/{prompt_id}/versions")
async def list_prompt_versions_endpoint(
    prompt_id: int, actor: CurrentUser, scope: CurrentScope, session: DbSession
) -> list[PromptVersionView]:
    """One prompt's history, newest first — the history panel's data."""
    del actor
    await _get_prompt_or_404(scope, session, prompt_id)
    versions = await list_versions(scope, session, prompt_id)
    return [_version_view(v) for v in versions]


@router.get("/versions/{version_id}")
async def get_prompt_version_endpoint(
    version_id: int, actor: CurrentUser, scope: CurrentScope, session: DbSession
) -> PromptVersionView:
    del actor
    version = await get_version(scope, session, version_id)
    if version is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such version.")
    return _version_view(version)


@router.post("/{prompt_id}/deploy")
async def deploy_prompt_endpoint(
    prompt_id: int,
    body: DeployRequest,
    actor: Writer,
    scope: CurrentScope,
    session: DbSession,
) -> PromptView:
    """Moves the `deployed` pointer to one of this prompt's own versions —
    the human claim "this version is live at the customer".
    """
    await _get_prompt_or_404(scope, session, prompt_id)
    try:
        await set_deployed(scope, session, prompt_id, body.version_id, user_id=actor.user_id)
    except CrossCustomerError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such version.") from exc
    except VersionError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    await session.commit()
    return await _prompt_view_by_id(scope, session, prompt_id)


@router.post("/versions/{version_id}/baseline")
async def set_baseline_endpoint(
    version_id: int,
    body: BaselineRequest,
    actor: Writer,
    scope: CurrentScope,
    session: DbSession,
) -> PromptVersionView:
    """Attaches a run to a version as its baseline — the known-good run that
    justified deploying it. Refused unless the run's results are actually
    attributed to this version (`NotAttributedError`): a baseline has to be
    evidence, not a label.
    """
    del actor
    try:
        await set_baseline(scope, session, version_id, body.run_id)
    except CrossCustomerError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except NotAttributedError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    await session.commit()
    version = await get_version(scope, session, version_id)
    if version is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such version.")
    return _version_view(version)


@router.post("/{prompt_id}/restore")
async def restore_prompt_endpoint(
    prompt_id: int,
    body: RestoreRequest,
    actor: Writer,
    scope: CurrentScope,
    session: DbSession,
) -> PromptView:
    """Copies a past version's content back into the draft — the rollback
    half of "restore to draft". Deliberately does not also commit: the
    editor's dirty indicator then shows the restored draft against the
    current head, and committing it is a separate, explicit action like any
    other draft edit.
    """
    del actor
    await _get_prompt_or_404(scope, session, prompt_id)
    version = await _get_version_or_404(scope, session, prompt_id, body.version_id)
    await update_prompt(scope, session, prompt_id, {"content": version.content})
    await session.commit()
    return await _prompt_view_by_id(scope, session, prompt_id)


# --------------------------------------------------------------------------
# Diff
# --------------------------------------------------------------------------


@router.get("/{prompt_id}/diff")
async def diff_prompt_endpoint(
    prompt_id: int,
    actor: CurrentUser,
    scope: CurrentScope,
    session: DbSession,
    from_ref: str = Query(alias="from"),
    to_ref: str = Query(alias="to"),
) -> DiffView:
    """A unified diff between any two of a prompt's texts. `from`/`to` each
    accept a version id or the literal `"draft"`.
    """
    del actor
    prompt = await _get_prompt_or_404(scope, session, prompt_id)
    from_label, from_content = await _resolve_diff_ref(scope, session, prompt, from_ref)
    to_label, to_content = await _resolve_diff_ref(scope, session, prompt, to_ref)
    return DiffView(
        from_label=from_label,
        to_label=to_label,
        diff=unified_diff(from_content, to_content, from_label=from_label, to_label=to_label),
    )
