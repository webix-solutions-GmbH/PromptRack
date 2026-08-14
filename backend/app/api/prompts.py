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
from app.auth.users import list_display_names
from app.models import Prompt, PromptKind, PromptVersion
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
    PromptKindChangeError,
    create_prompt,
    delete_prompt,
    get_prompt,
    list_prompts,
    test_case_reference_counts,
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
    #: Which channel this prompt is sent on: a `system` prompt becomes the
    #: system message, a `task` prompt the head of the user message. A
    #: property of the asset, not of a test case's reference to it.
    kind: PromptKind
    #: The mutable draft — what the editor writes and what a run tests.
    content: str
    #: `True` when the draft differs from the head version (or nothing has
    #: ever been committed). Mirrors `app.services.attribution.is_dirty`.
    dirty: bool
    #: How many live test cases reference this prompt, across **both** slots.
    #: Server-computed (`app.repos.prompts.test_case_reference_counts`) so the
    #: list and the editor can never disagree about whether a kind change is
    #: still allowed — it is refused while this is non-zero.
    used_by_test_case_count: int
    head_version: VersionSummary | None
    deployed_version: VersionSummary | None
    deployed_at: datetime | None
    #: Who moved the `deployed` pointer, resolved from `prompts.deployed_by`
    #: (`users.name`, falling back to `users.email`; `None` when nothing is
    #: deployed yet, or that user's account is gone — `SET NULL` on delete).
    deployed_by_name: str | None
    created_at: datetime
    updated_at: datetime


class PromptWriteRequest(BaseModel):
    name: str = Field(min_length=1)
    #: An empty draft is legitimate: the asset can be created before its text
    #: is written (`is_dirty` calls a prompt with no versions dirty either way,
    #: so the first commit is still allowed), and
    #: `app.services.message_assembly` already reads a whitespace-only prompt
    #: as absent. Only the *name* identifies the asset, so only the name is
    #: required.
    content: str = ""
    #: Defaults to `system` — the channel everything authored before the
    #: prompt-kinds pivot was sent on. An unrecognised value is **refused**
    #: (Pydantic rejects the `Literal`, a 422), deliberately unlike
    #: `app.auth.policy.parse_role`, which degrades an unknown role to the
    #: least privileged one: there is no safe fallback channel, and silently
    #: picking one would move text between the system and user messages.
    kind: PromptKind = "system"

    @field_validator("name")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("This field is required.")
        return cleaned

    @field_validator("content")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()


class PromptPatchRequest(BaseModel):
    """Partial update of the draft — either field, or both, may be omitted.

    A present-but-empty `content` clears the draft, the same state a prompt is
    created in: an editor that can reach empty has to be able to get back
    there. A blank `name` is still refused — that is the asset's identity.

    `kind` rides on the same PATCH, but it is the one field the server can
    refuse for a reason unrelated to the draft (409 while test cases reference
    the prompt), so a client that sends both must not read that refusal as
    "the draft was not saved" — nothing is written when it fires.
    """

    name: str | None = None
    content: str | None = None
    kind: PromptKind | None = None

    @field_validator("name")
    @classmethod
    def _not_blank_if_present(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("This field cannot be blank.")
        return cleaned

    @field_validator("content")
    @classmethod
    def _strip_if_present(cls, value: str | None) -> str | None:
        return None if value is None else value.strip()


class PromptVersionView(BaseModel):
    id: int
    prompt_id: int
    version: int
    content: str
    message: str
    created_at: datetime
    created_by: int | None
    #: The author's name (`users.name`, falling back to `users.email`),
    #: resolved server-side so the history panel never renders a bare id.
    #: `None` alongside `created_by is None`, or when that user's account has
    #: since been deleted (`SET NULL`).
    created_by_name: str | None
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


def _prompt_view(
    prompt: Prompt,
    refs: list[VersionRef],
    names: dict[int, str],
    used_by: int = 0,
) -> PromptView:
    head = head_version(refs)
    return PromptView(
        id=prompt.id,
        name=prompt.name,
        kind=prompt.kind,
        content=prompt.content,
        dirty=is_dirty(prompt.content, refs),
        used_by_test_case_count=used_by,
        head_version=_version_summary(head.id, refs) if head is not None else None,
        deployed_version=_version_summary(prompt.deployed_version_id, refs),
        deployed_at=prompt.deployed_at,
        deployed_by_name=names.get(prompt.deployed_by) if prompt.deployed_by is not None else None,
        created_at=prompt.created_at,
        updated_at=prompt.updated_at,
    )


def _version_view(version: PromptVersion, names: dict[int, str]) -> PromptVersionView:
    return PromptVersionView(
        id=version.id,
        prompt_id=version.prompt_id,
        version=version.version,
        content=version.content,
        message=version.message,
        created_at=version.created_at,
        created_by=version.created_by,
        created_by_name=names.get(version.created_by) if version.created_by is not None else None,
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
    refs = await _refs_for(scope, session, prompt_id)
    names = await list_display_names(session, [prompt.deployed_by])
    counts = await test_case_reference_counts(scope, session, [prompt_id])
    return _prompt_view(prompt, refs, names, counts.get(prompt_id, 0))


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
    prompt_ids = [p.id for p in prompts]
    refs_by_prompt = await list_version_refs(scope, session, prompt_ids)
    names = await list_display_names(session, [p.deployed_by for p in prompts])
    counts = await test_case_reference_counts(scope, session, prompt_ids)
    return [
        _prompt_view(p, refs_by_prompt.get(p.id, []), names, counts.get(p.id, 0))
        for p in prompts
    ]


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
    prompt = await create_prompt(
        scope, session, name=body.name, content=body.content, kind=body.kind
    )
    await session.commit()
    # Brand new: no versions, nobody deployed it, and nothing can reference it yet.
    return _prompt_view(prompt, [], {}, 0)


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

    `kind` is refused with a 409 while any test case references the prompt.
    The check lives inside `app.repos.prompts.update_prompt`, so it holds for
    every caller and nothing is written when it fires.
    """
    del actor
    await _get_prompt_or_404(scope, session, prompt_id)

    values: dict[str, object] = {}
    if "name" in body.model_fields_set:
        values["name"] = body.name
    if "content" in body.model_fields_set:
        # A JSON `null` says the same thing as `""` — no draft text — and the
        # column is NOT NULL. "Absent" is already carried by `model_fields_set`.
        values["content"] = body.content or ""
    if "kind" in body.model_fields_set and body.kind is not None:
        values["kind"] = body.kind

    if values:
        try:
            await update_prompt(scope, session, prompt_id, values)
        except PromptKindChangeError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        await session.commit()
    return await _prompt_view_by_id(scope, session, prompt_id)


@router.delete("/{prompt_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_prompt_endpoint(
    prompt_id: int, actor: Writer, scope: CurrentScope, session: DbSession
) -> None:
    """Deletes the asset and, by cascade, its whole version history. Past
    runs are unaffected — they carry their own frozen copies of both prompt
    texts, and `run_results.system_prompt_version_id` /
    `task_prompt_version_id` are `SET NULL`.

    Live test cases *are* affected: the slot pointing here is `SET NULL` too,
    which can leave a case with neither a task prompt nor content. Run
    creation refuses that case (`assert_user_message`) rather than sending an
    empty user message.
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
    names = await list_display_names(session, [version.created_by])
    return _version_view(version, names)


@router.get("/{prompt_id}/versions")
async def list_prompt_versions_endpoint(
    prompt_id: int, actor: CurrentUser, scope: CurrentScope, session: DbSession
) -> list[PromptVersionView]:
    """One prompt's history, newest first — the history panel's data."""
    del actor
    await _get_prompt_or_404(scope, session, prompt_id)
    versions = await list_versions(scope, session, prompt_id)
    names = await list_display_names(session, [v.created_by for v in versions])
    return [_version_view(v, names) for v in versions]


@router.get("/versions/{version_id}")
async def get_prompt_version_endpoint(
    version_id: int, actor: CurrentUser, scope: CurrentScope, session: DbSession
) -> PromptVersionView:
    del actor
    version = await get_version(scope, session, version_id)
    if version is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such version.")
    names = await list_display_names(session, [version.created_by])
    return _version_view(version, names)


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
    names = await list_display_names(session, [version.created_by])
    return _version_view(version, names)


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
