"""Prompt history: commits, the deployed pointer, the baseline run.

This is the "git" half of the pivot. ``prompts.content`` is the mutable draft
(:mod:`app.repos.prompts` owns it); everything here is about freezing that
draft and about the two pointers that hang off the frozen versions:

* a **commit** writes an immutable ``prompt_versions`` row. Versions are never
  edited and never deleted individually — the history dies with the asset
  (``CASCADE``) and past runs keep their own snapshots regardless.
* the **deployed** pointer is a human's bookkeeping claim that a version is
  live at the customer. It is set from the UI only, never over MCP, for the
  same reason customer workspaces are not writable there.
* the **baseline** pointer names the known-good run that justified deploying a
  version — the reference point a regression check after a model swap compares
  against.

``prompt_versions`` carries no ``customer_id``: it inherits its workspace
through ``prompt_id``, so reads join ``prompts`` and writes express the same
thing as a :func:`~app.repos.scoped.scope_through_parent` subquery. The two
cross-references the database cannot check — a deployed version belonging to
*this* prompt, a baseline run belonging to this workspace *and* actually having
tested this version — are checked inside these functions, so no call site can
forget them.
"""

from collections.abc import Sequence

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Prompt, PromptVersion, Run, RunResult
from app.repos.customers import assert_same_customer
from app.repos.prompts import get_prompt, update_prompt
from app.repos.scoped import apply_where, scope_through_parent, transaction, utc_now
from app.scope import CrossCustomerError, Scope, combine, where_scoped
from app.services.attribution import VersionRef, is_dirty


class VersionError(Exception):
    """A write against a prompt's history was refused."""


class NoChangesError(VersionError):
    """The draft is byte-identical to the head version, so a commit is empty.

    Not an error condition so much as the editor's dirty indicator arriving as
    a refusal: history that records a commit which changed nothing is history
    nobody can read.
    """


class NotAttributedError(VersionError):
    """The run offered as a baseline never tested this version.

    A baseline is evidence, so it has to be a run whose results are attributed
    to the version it justifies — otherwise "verified" would rest on a run of
    some other text. Attribution may have arrived through either slot: a
    version of a ``system`` prompt can only ever appear in
    ``run_results.system_prompt_version_id`` and a ``task`` prompt's only in
    ``task_prompt_version_id``, so asking about both is the same question as
    asking about the right one, and simpler.
    """


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


async def list_versions(
    scope: Scope, session: AsyncSession, prompt_id: int
) -> list[PromptVersion]:
    """One prompt's history, newest first — the order the panel shows it in."""
    statement = apply_where(
        select(PromptVersion).join(Prompt, PromptVersion.prompt_id == Prompt.id),
        where_scoped(scope, Prompt, PromptVersion.prompt_id == prompt_id),
    ).order_by(PromptVersion.version.desc())
    return list((await session.scalars(statement)).all())


async def get_version(
    scope: Scope, session: AsyncSession, version_id: int
) -> PromptVersion | None:
    """One version by its own id, scoped through the prompt it belongs to."""
    statement = apply_where(
        select(PromptVersion).join(Prompt, PromptVersion.prompt_id == Prompt.id),
        where_scoped(scope, Prompt, PromptVersion.id == version_id),
    )
    return (await session.scalars(statement)).first()


async def get_head_version(
    scope: Scope, session: AsyncSession, prompt_id: int
) -> PromptVersion | None:
    """The newest commit of one prompt, or ``None`` while it has never been
    committed — an uncommitted prompt is a dirty working tree.
    """
    statement = (
        apply_where(
            select(PromptVersion).join(Prompt, PromptVersion.prompt_id == Prompt.id),
            where_scoped(scope, Prompt, PromptVersion.prompt_id == prompt_id),
        )
        .order_by(PromptVersion.version.desc())
        .limit(1)
    )
    return (await session.scalars(statement)).first()


async def list_version_refs(
    scope: Scope, session: AsyncSession, prompt_ids: Sequence[int]
) -> dict[int, list[VersionRef]]:
    """The committed text of several prompts at once, keyed by prompt id.

    What run creation attributes against: one query for every prompt a run
    touches, handed to :func:`app.services.attribution.match_version` per row.
    Only the three columns the comparison needs are read — a suite's whole
    history would otherwise be pulled through for one equality check.
    """
    if not prompt_ids:
        return {}
    statement = apply_where(
        select(
            PromptVersion.prompt_id,
            PromptVersion.id,
            PromptVersion.version,
            PromptVersion.content,
        ).join(Prompt, PromptVersion.prompt_id == Prompt.id),
        where_scoped(scope, Prompt, PromptVersion.prompt_id.in_(list(prompt_ids))),
    ).order_by(PromptVersion.prompt_id.asc(), PromptVersion.version.desc())

    refs: dict[int, list[VersionRef]] = {}
    for prompt_id, version_id, version, content in (await session.execute(statement)).all():
        refs.setdefault(prompt_id, []).append(
            VersionRef(id=version_id, version=version, content=content)
        )
    return refs


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


async def commit_version(
    scope: Scope,
    session: AsyncSession,
    prompt_id: int,
    *,
    message: str,
    user_id: int | None = None,
) -> PromptVersion:
    """Freezes the current draft as the next version.

    Reading the head and writing the new row happen in one unit of work, so the
    ``max + 1`` a commit computes cannot be invalidated between the two
    statements by anything this process does. Two processes committing the same
    prompt at the same instant are caught by the ``(prompt_id, version)``
    unique constraint instead — the backstop the schema exists to provide, and
    cheaper than serializing every commit behind a lock.

    Refuses a commit whose content equals the head's: see :class:`NoChangesError`.
    """
    async with transaction(session):
        prompt = await get_prompt(scope, session, prompt_id)
        if prompt is None:
            raise CrossCustomerError(
                f"The selected prompt (id {prompt_id}) no longer exists in this workspace."
            )

        # The rule itself is `is_dirty`, so the editor's indicator and this
        # refusal can never drift apart. A prompt with no head is dirty by that
        # rule too — the first commit is always allowed.
        head = await get_head_version(scope, session, prompt_id)
        if head is not None and not is_dirty(prompt.content, [_as_ref(head)]):
            raise NoChangesError(
                f"The draft is identical to v{head.version} — there is nothing to commit."
            )

        version = PromptVersion(
            prompt_id=prompt_id,
            version=1 if head is None else head.version + 1,
            content=prompt.content,
            message=message,
            created_by=user_id,
        )
        session.add(version)
        await session.flush()
        return version


async def set_deployed(
    scope: Scope,
    session: AsyncSession,
    prompt_id: int,
    version_id: int,
    *,
    user_id: int | None = None,
) -> PromptVersion:
    """Points a prompt's ``deployed`` pointer at one of its own versions.

    Two checks the database cannot make, both here rather than at the call
    site: the version has to be visible in this workspace (the scoped read),
    and it has to belong to *this* prompt — the foreign key only says
    "some version".

    Returns the version now marked deployed, which is what the caller renders.
    """
    version = await get_version(scope, session, version_id)
    if version is None:
        raise CrossCustomerError(
            f"The selected version (id {version_id}) no longer exists in this workspace."
        )
    if version.prompt_id != prompt_id:
        raise VersionError(
            f"Version id {version_id} belongs to prompt {version.prompt_id}, "
            f"not to prompt {prompt_id}."
        )

    await update_prompt(
        scope,
        session,
        prompt_id,
        {
            "deployed_version_id": version_id,
            "deployed_at": utc_now(),
            "deployed_by": user_id,
        },
    )
    return version


async def set_baseline(
    scope: Scope, session: AsyncSession, version_id: int, run_id: int
) -> None:
    """Records the run that justifies a version — its regression reference.

    Three things have to hold, and none of them is expressible as a foreign
    key: the version is in this workspace, the run is too, and the run really
    tested *this* version — through **either** prompt slot; see
    :class:`NotAttributedError`. The last one is what makes a baseline evidence
    rather than a label; a run of a dirty draft carries no attribution at all
    and therefore can never be one.

    One run can be the baseline for versions of two different prompts at once
    (its rows attribute a system prompt's version *and* a task prompt's), which
    is correct: it really did test both.
    """
    if await get_version(scope, session, version_id) is None:
        raise CrossCustomerError(
            f"The selected version (id {version_id}) no longer exists in this workspace."
        )
    await assert_same_customer(session, scope, Run, run_id)

    if not await _has_attributed_result(scope, session, run_id, version_id):
        raise NotAttributedError(
            f"Run {run_id} has no results attributed to this version, so it cannot be "
            "its baseline."
        )

    statement = apply_where(
        update(PromptVersion),
        combine(
            [
                PromptVersion.id == version_id,
                scope_through_parent(scope, PromptVersion.prompt_id, Prompt, Prompt.id),
            ]
        ),
    )
    await session.execute(statement.values(baseline_run_id=run_id))


async def _has_attributed_result(
    scope: Scope, session: AsyncSession, run_id: int, version_id: int
) -> bool:
    """Whether any of a run's results tested the given version, in either slot.

    Scoped through the run, the parent ``run_results`` inherits its workspace
    from, so a foreign run cannot answer "yes" here even though the caller has
    already been told it does not exist.

    The ``OR`` is safe because a version id is unique across both columns: the
    prompt's kind decides which column its versions can ever land in.
    """
    statement = apply_where(
        select(RunResult.id).join(Run, RunResult.run_id == Run.id),
        where_scoped(
            scope,
            Run,
            RunResult.run_id == run_id,
            or_(
                RunResult.system_prompt_version_id == version_id,
                RunResult.task_prompt_version_id == version_id,
            ),
        ),
    ).limit(1)
    return (await session.scalars(statement)).first() is not None


def _as_ref(version: PromptVersion) -> VersionRef:
    return VersionRef(id=version.id, version=version.version, content=version.content)
