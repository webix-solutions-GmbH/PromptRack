"""Prompts — the versioned asset.

Only the prompt row itself lives here: ``prompts.content`` is the mutable
draft, and every read or write of it is a plain scoped root-table query. The
history (``prompt_versions``), the commit rule and the deployed/baseline
pointers live in :mod:`app.repos.prompt_versions`, which is where the
invariants that make a version immutable are worth reading in one place.

Two rules about a prompt's ``kind`` live here rather than at any call site,
because both are properties of the asset:
:func:`assert_prompt_slot` (a test case's slot only accepts a prompt of that
kind, in this workspace) and the refusal inside :func:`update_prompt` (a kind
cannot change while test cases reference it).
"""

from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Prompt, PromptKind, TestCase, TestGroup
from app.repos.scoped import apply_where
from app.scope import CrossCustomerError, Scope, scope_values, where_scoped


class PromptSlotError(Exception):
    """A prompt was named for a slot its `kind` does not fit.

    Deliberately *not* a :class:`~app.scope.CrossCustomerError`: that one means
    "no such row here", which would be a lie about a prompt sitting in this very
    workspace with the other kind. The two refusals also differ at the API
    boundary — a missing row is a 404, a wrong-kind reference is a 400.
    """


class PromptKindChangeError(Exception):
    """A prompt's `kind` cannot change while test cases reference it.

    The alternative — silently relocating the text from the system channel to
    the head of the user message for every case that uses it — is exactly the
    invisible wire-format change the prompt-kinds pivot exists to eliminate.
    """


async def list_prompts(
    scope: Scope, session: AsyncSession, order: str = "name"
) -> list[Prompt]:
    statement = apply_where(select(Prompt), where_scoped(scope, Prompt))
    statement = statement.order_by(
        Prompt.updated_at.desc() if order == "updated" else Prompt.name.asc()
    )
    return list((await session.scalars(statement)).all())


async def get_prompt(scope: Scope, session: AsyncSession, prompt_id: int) -> Prompt | None:
    statement = apply_where(select(Prompt), where_scoped(scope, Prompt, Prompt.id == prompt_id))
    return (await session.scalars(statement)).first()


async def list_prompts_by_ids(
    scope: Scope, session: AsyncSession, prompt_ids: Sequence[int]
) -> list[Prompt]:
    """The named prompts, for building a lookup map.

    An empty id list answers without querying — ``IN ()`` is a pointless round
    trip.
    """
    if not prompt_ids:
        return []
    statement = apply_where(
        select(Prompt), where_scoped(scope, Prompt, Prompt.id.in_(list(prompt_ids)))
    )
    return list((await session.scalars(statement)).all())


async def find_prompt_by_name(
    scope: Scope, session: AsyncSession, name: str
) -> list[Prompt]:
    """Prompts whose name matches case-insensitively, inside the scope.

    Returns every match rather than one: a caller that relates a prompt by name
    (MCP does) must refuse an ambiguous name instead of guessing, and name
    resolution being scoped is what keeps it from ever reaching another
    workspace's row.
    """
    statement = apply_where(
        select(Prompt),
        where_scoped(scope, Prompt, func.lower(Prompt.name) == func.lower(name)),
    ).order_by(Prompt.id.asc())
    return list((await session.scalars(statement)).all())


async def assert_prompt_slot(
    scope: Scope,
    session: AsyncSession,
    prompt_id: int | None,
    kind: PromptKind,
) -> Prompt | None:
    """Refuses a prompt that cannot fill the slot it was named for.

    Two things in **one** read, because both are true of the same row and this
    runs once per slot on every test-case write: the prompt has to be visible in
    this workspace (:class:`~app.scope.CrossCustomerError` otherwise, matching
    :func:`~app.repos.customers.assert_same_customer`'s wording), and its
    ``kind`` has to be the slot's (:class:`PromptSlotError` otherwise — a
    *different* refusal, since the row does exist here).

    Called from inside the repository functions in :mod:`app.repos.test_cases`,
    the same reasoning that puts ``assert_same_customer`` there: no call site
    can forget half of it.

    ``prompt_id`` may be ``None`` — an empty slot is always valid, and letting
    it through here keeps the four call sites free of the same ``if``. The
    validated row is returned, which is what lets the caller check "does this
    case have a user message at all" without a second query.
    """
    if prompt_id is None:
        return None

    statement = apply_where(
        select(Prompt), where_scoped(scope, Prompt, Prompt.id == prompt_id)
    )
    prompt = (await session.scalars(statement)).first()
    if prompt is None:
        raise CrossCustomerError(
            f"The selected prompt (id {prompt_id}) no longer exists in this workspace."
        )
    if prompt.kind != kind:
        raise PromptSlotError(
            f'Prompt "{prompt.name}" is a {prompt.kind} prompt and cannot be used as '
            f"the {kind} prompt. Pick a {kind} prompt, or change that prompt's kind."
        )
    return prompt


async def test_case_reference_counts(
    scope: Scope, session: AsyncSession, prompt_ids: Sequence[int]
) -> dict[int, int]:
    """How many test cases reference each prompt, across **both** slots.

    Two consumers, one query, so they can never disagree: the kind-change
    refusal below, and the "used by N test cases" column ``/prompts`` shows.

    Test cases inherit their workspace through their group, so the count is
    taken over that join rather than off the child table. A prompt nothing
    references is simply absent from the result.
    """
    if not prompt_ids:
        return {}

    wanted = list(prompt_ids)
    counts: dict[int, int] = {}
    for column in (TestCase.system_prompt_id, TestCase.task_prompt_id):
        statement = apply_where(
            select(column, func.count())
            .select_from(TestCase)
            .join(TestGroup, TestCase.group_id == TestGroup.id),
            where_scoped(scope, TestGroup, column.in_(wanted)),
        ).group_by(column)
        for prompt_id, count in (await session.execute(statement)).all():
            counts[prompt_id] = counts.get(prompt_id, 0) + count
    return counts


async def create_prompt(
    scope: Scope,
    session: AsyncSession,
    *,
    name: str,
    content: str,
    kind: PromptKind = "system",
) -> Prompt:
    """Creates a prompt with its draft content. It has no versions until the
    first explicit commit — an uncommitted prompt is a dirty working tree.

    ``kind`` defaults to ``system``: it is the channel everything authored
    before the pivot was sent on, so defaulting anywhere else would silently
    move text between channels.
    """
    prompt = Prompt(name=name, content=content, kind=kind, **scope_values(scope))
    session.add(prompt)
    await session.flush()
    return prompt


async def update_prompt(
    scope: Scope, session: AsyncSession, prompt_id: int, values: Mapping[str, Any]
) -> None:
    """Patches the named columns only.

    This is how the draft is edited and, from :mod:`app.repos.prompt_versions`,
    how the deployed pointer is moved — the cross-reference checks that pointer
    needs belong there, not to every caller of this function.

    One rule does live here, because it is a property of the column rather than
    of any one caller: a **kind change is refused while any test case
    references the prompt** (:class:`PromptKindChangeError`). It costs the two
    extra reads only when ``kind`` is actually in the patch, so moving the
    deployed pointer and restoring a version — the other users of this
    function — pay nothing.
    """
    if not values:
        return

    if "kind" in values:
        await _assert_kind_change_allowed(scope, session, prompt_id, values["kind"])

    statement = apply_where(update(Prompt), where_scoped(scope, Prompt, Prompt.id == prompt_id))
    await session.execute(statement.values(**values))


async def _assert_kind_change_allowed(
    scope: Scope, session: AsyncSession, prompt_id: int, kind: Any
) -> None:
    """A no-op unless the kind really changes; a refusal when it does and the
    prompt is referenced. A prompt that is not visible here is left to the
    ``UPDATE`` itself, which will match no rows.
    """
    current = await get_prompt(scope, session, prompt_id)
    if current is None or current.kind == kind:
        return

    referenced = (await test_case_reference_counts(scope, session, [prompt_id])).get(
        prompt_id, 0
    )
    if referenced:
        cases = "test case" if referenced == 1 else "test cases"
        raise PromptKindChangeError(
            f'Prompt "{current.name}" is used by {referenced} {cases}, so its kind '
            f"cannot change from {current.kind} to {kind} — that would move its text "
            "to another channel behind their backs. Remove the references first."
        )


async def delete_prompt(scope: Scope, session: AsyncSession, prompt_id: int) -> None:
    """Deletes the asset and, by cascade, its whole history.

    Past runs are unaffected: they carry their own frozen copies of both prompt
    texts, and ``run_results.system_prompt_version_id`` /
    ``task_prompt_version_id`` are ``SET NULL``.

    Live test cases are affected, though: their ``system_prompt_id`` /
    ``task_prompt_id`` are ``SET NULL`` too, which can leave a case with neither
    a task prompt nor content. That case is the one run creation's
    :func:`~app.services.message_assembly.assert_user_message` exists to catch,
    since the authoring-time guard ran before this deletion.
    """
    statement = apply_where(delete(Prompt), where_scoped(scope, Prompt, Prompt.id == prompt_id))
    await session.execute(statement)
