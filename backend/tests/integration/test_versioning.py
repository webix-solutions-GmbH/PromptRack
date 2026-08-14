"""Prompt-version integration behavior — the new half of Task 1.4, alongside
the ported workspace suite in `test_workspaces.py`: version history is scoped
like everything else, the deployed/baseline pointers refuse a cross-workspace
target, and the two FK actions only a real Postgres can show (`baseline_run_id`
`SET NULL` on run delete, `prompt_versions` `CASCADE` on prompt delete).

Two rules keep this file honest about `AsyncSession`'s async-refresh model
(`expire_on_commit=False`, and an *expired* attribute access needs an
`await`, unlike a sync `Session` where it happens transparently):

* every id this file needs again is captured into a plain `int` the moment
  it is created — never re-read off a possibly-expired ORM object later.
* after `update_prompt`'s raw Core `UPDATE` (`app.repos.prompts`), or after
  any other repository write that bypasses the ORM's own unit-of-work,
  `session.expire_all()` runs before the next read — relying on
  SQLAlchemy's "evaluate" auto-sync strategy for a raw bulk update would
  make a passing test depend on an implementation detail rather than on the
  schema.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.repos.endpoints import create_endpoint
from app.repos.prompt_versions import (
    NoChangesError,
    NotAttributedError,
    VersionError,
    commit_version,
    get_version,
    list_version_refs,
    set_baseline,
    set_deployed,
)
from app.repos.prompts import create_prompt, delete_prompt, get_prompt, update_prompt
from app.repos.runs import (
    create_run as create_run_row,
)
from app.repos.runs import delete_run, get_run_result, insert_run_results, list_run_results
from app.scope import CrossCustomerError, Scope
from app.services.attribution import match_version

CreateWorkspace = Callable[[str], Awaitable[tuple[int, Scope]]]


#: The two columns a version id can be attributed through — one per prompt
#: kind. `set_baseline` checks them with an `OR`, which is safe because a
#: prompt's kind decides which of the two its versions can ever land in; these
#: tests are what pin "either column counts as evidence".
SLOT_COLUMNS = ("system_prompt_version_id", "task_prompt_version_id")


async def _run_with_attributed_result(
    session: AsyncSession,
    scope: Scope,
    endpoint_id: int,
    *,
    prompt_version_id: int | None,
    slot: str = "system_prompt_version_id",
) -> tuple[int, int]:
    """A minimal run whose one result is attributed (or not) to a version —
    what `set_baseline` demands as evidence.

    `slot` names which of the two version columns the id lands in; the other
    one stays null, so a test that passes for both slots really did exercise
    each column on its own.
    """
    assert slot in SLOT_COLUMNS
    run = await create_run_row(
        scope,
        session,
        endpoint_id=endpoint_id,
        endpoint_snapshot="{}",
        model_id="qwen3-32b",
        group_names="[]",
    )
    run_id = run.id
    await insert_run_results(
        scope,
        session,
        run_id,
        [
            {
                **{column: None for column in SLOT_COLUMNS},
                slot: prompt_version_id,
                "group_name": "General",
                "test_case_title": "Hello",
                "test_case_text": "Say hi.",
                "status": "ok",
            }
        ],
    )
    [result] = await list_run_results(scope, session, run_id)
    return run_id, result.id


async def test_versions_are_scoped_and_never_leak_across_workspaces(
    session: AsyncSession, create_workspace: CreateWorkspace
):
    _, scope_a = await create_workspace("A")
    _, scope_b = await create_workspace("B")

    prompt_a = await create_prompt(scope_a, session, name="Greeting", content="Say hi.")
    prompt_b = await create_prompt(scope_b, session, name="Greeting", content="Say hi.")

    version_a = await commit_version(scope_a, session, prompt_a.id, message="v1")
    version_a_id = version_a.id
    version_b = await commit_version(scope_b, session, prompt_b.id, message="v1")
    version_b_id = version_b.id

    assert await get_version(scope_a, session, version_b_id) is None
    assert await get_version(scope_b, session, version_a_id) is None

    seen = await get_version(scope_a, session, version_a_id)
    assert seen is not None
    assert seen.id == version_a_id


async def test_commit_refuses_when_the_draft_equals_the_head(session: AsyncSession, scope: Scope):
    prompt = await create_prompt(scope, session, name="Greeting", content="Say hi.")
    prompt_id = prompt.id
    await commit_version(scope, session, prompt_id, message="v1")

    with pytest.raises(NoChangesError):
        await commit_version(scope, session, prompt_id, message="v2 (no-op)")


async def test_commit_survives_a_revert_and_matches_the_newest_identical_version(
    session: AsyncSession, scope: Scope
):
    prompt = await create_prompt(scope, session, name="Greeting", content="A")
    prompt_id = prompt.id
    v1 = await commit_version(scope, session, prompt_id, message="v1")
    v1_number = v1.version

    # `expire_all()` marks every loaded object (including `v1`) expired, so
    # its own attributes are captured above rather than re-read after this —
    # an expired attribute needs an `await` to refresh, and this file's
    # objects are otherwise plain (non-awaited) Python references.
    await update_prompt(scope, session, prompt_id, {"content": "B"})
    session.expire_all()
    v2 = await commit_version(scope, session, prompt_id, message="v2")
    v2_number, v2_id = v2.version, v2.id

    # Revert the draft back to v1's text, byte for byte.
    await update_prompt(scope, session, prompt_id, {"content": "A"})
    session.expire_all()
    v3 = await commit_version(scope, session, prompt_id, message="v3 (revert)")
    v3_number, v3_id = v3.version, v3.id

    assert [v1_number, v2_number, v3_number] == [1, 2, 3]

    refs = (await list_version_refs(scope, session, [prompt_id]))[prompt_id]
    # A draft equal to "A" matches v3 — the commit that is actually the head
    # of the history — not the older v1, even though both are byte-identical.
    assert match_version("A", refs) == v3_id
    assert v2_id != v3_id


async def test_set_deployed_refuses_a_version_from_another_workspace(
    session: AsyncSession, create_workspace: CreateWorkspace
):
    _, scope_a = await create_workspace("A")
    _, scope_b = await create_workspace("B")

    prompt_a = await create_prompt(scope_a, session, name="Greeting", content="Say hi.")
    prompt_a_id = prompt_a.id
    prompt_b = await create_prompt(scope_b, session, name="Greeting", content="Say hi.")
    version_b = await commit_version(scope_b, session, prompt_b.id, message="v1")

    with pytest.raises(CrossCustomerError):
        await set_deployed(scope_a, session, prompt_a_id, version_b.id)


async def test_set_deployed_refuses_a_version_belonging_to_a_different_prompt(
    session: AsyncSession, scope: Scope
):
    prompt_1 = await create_prompt(scope, session, name="One", content="A")
    prompt_1_id = prompt_1.id
    prompt_2 = await create_prompt(scope, session, name="Two", content="B")
    version_2 = await commit_version(scope, session, prompt_2.id, message="v1")

    with pytest.raises(VersionError):
        await set_deployed(scope, session, prompt_1_id, version_2.id)


async def test_set_deployed_succeeds_and_updates_the_prompt(session: AsyncSession, scope: Scope):
    prompt = await create_prompt(scope, session, name="Greeting", content="Say hi.")
    prompt_id = prompt.id
    version = await commit_version(scope, session, prompt_id, message="v1")
    version_id = version.id

    await set_deployed(scope, session, prompt_id, version_id)
    session.expire_all()

    deployed = await get_prompt(scope, session, prompt_id)
    assert deployed is not None
    assert deployed.deployed_version_id == version_id
    assert deployed.deployed_at is not None


async def test_set_baseline_refuses_a_run_from_another_workspace(
    session: AsyncSession, create_workspace: CreateWorkspace
):
    _, scope_a = await create_workspace("A")
    _, scope_b = await create_workspace("B")

    prompt_a = await create_prompt(scope_a, session, name="Greeting", content="Say hi.")
    version_a = await commit_version(scope_a, session, prompt_a.id, message="v1")
    version_a_id = version_a.id

    endpoint_b = await create_endpoint(scope_b, session, name="B box", base_url="http://x/v1")
    run_b_id, _ = await _run_with_attributed_result(
        session, scope_b, endpoint_b.id, prompt_version_id=None
    )

    with pytest.raises(CrossCustomerError):
        await set_baseline(scope_a, session, version_a_id, run_b_id)


async def test_set_baseline_refuses_a_run_that_never_tested_this_version(
    session: AsyncSession, scope: Scope
):
    prompt = await create_prompt(scope, session, name="Greeting", content="Say hi.")
    version = await commit_version(scope, session, prompt.id, message="v1")
    version_id = version.id

    endpoint = await create_endpoint(scope, session, name="box", base_url="http://x/v1")
    # A run in the same workspace, but its result is not attributed to `version`.
    run_id, _ = await _run_with_attributed_result(
        session, scope, endpoint.id, prompt_version_id=None
    )

    with pytest.raises(NotAttributedError):
        await set_baseline(scope, session, version_id, run_id)


@pytest.mark.parametrize("slot", SLOT_COLUMNS)
async def test_set_baseline_accepts_a_run_attributed_through_either_column(
    session: AsyncSession, scope: Scope, slot: str
):
    """Evidence is evidence whichever channel the prompt is sent on.

    A `system` prompt's versions can only ever appear in
    `system_prompt_version_id` and a `task` prompt's only in
    `task_prompt_version_id`, so `set_baseline` checks both with an `OR` — and
    each column has to work on its own, which is what parametrizing this pins.
    """
    kind = "system" if slot == "system_prompt_version_id" else "task"
    prompt = await create_prompt(
        scope, session, name="Greeting", content="Say hi.", kind=kind
    )
    version = await commit_version(scope, session, prompt.id, message="v1")
    version_id = version.id

    endpoint = await create_endpoint(scope, session, name="box", base_url="http://x/v1")
    run_id, _ = await _run_with_attributed_result(
        session, scope, endpoint.id, prompt_version_id=version_id, slot=slot
    )

    await set_baseline(scope, session, version_id, run_id)
    session.expire_all()

    baselined = await get_version(scope, session, version_id)
    assert baselined is not None
    assert baselined.baseline_run_id == run_id


async def test_one_run_can_be_the_baseline_of_a_version_in_each_slot(
    session: AsyncSession, scope: Scope
):
    """A run of a two-slot test case really did test both prompts, so it can
    justify a version of each — one row, two attributions, two baselines.
    """
    system_prompt = await create_prompt(
        scope, session, name="framing", content="SYSTEM", kind="system"
    )
    system_version_id = (
        await commit_version(scope, session, system_prompt.id, message="s1")
    ).id
    task_prompt = await create_prompt(
        scope, session, name="instruction", content="TASK", kind="task"
    )
    task_version_id = (await commit_version(scope, session, task_prompt.id, message="t1")).id

    endpoint = await create_endpoint(scope, session, name="box", base_url="http://x/v1")
    run = await create_run_row(
        scope,
        session,
        endpoint_id=endpoint.id,
        endpoint_snapshot="{}",
        model_id="qwen3-32b",
        group_names="[]",
    )
    run_id = run.id
    await insert_run_results(
        scope,
        session,
        run_id,
        [
            {
                "system_prompt_version_id": system_version_id,
                "task_prompt_version_id": task_version_id,
                "group_name": "General",
                "test_case_title": "Hello",
                "test_case_text": "Say hi.",
                "status": "ok",
            }
        ],
    )

    await set_baseline(scope, session, system_version_id, run_id)
    await set_baseline(scope, session, task_version_id, run_id)
    session.expire_all()

    for version_id in (system_version_id, task_version_id):
        baselined = await get_version(scope, session, version_id)
        assert baselined is not None
        assert baselined.baseline_run_id == run_id


async def test_set_baseline_still_refuses_a_run_attributed_to_a_different_version(
    session: AsyncSession, scope: Scope
):
    """The `OR` widens *which column* counts, never *which version*.

    A run attributed to some other prompt's version in the other slot is not
    evidence about this one.
    """
    wanted = await create_prompt(scope, session, name="wanted", content="A", kind="system")
    wanted_version_id = (await commit_version(scope, session, wanted.id, message="v1")).id
    other = await create_prompt(scope, session, name="other", content="B", kind="task")
    other_version_id = (await commit_version(scope, session, other.id, message="v1")).id

    endpoint = await create_endpoint(scope, session, name="box", base_url="http://x/v1")
    run_id, _ = await _run_with_attributed_result(
        session,
        scope,
        endpoint.id,
        prompt_version_id=other_version_id,
        slot="task_prompt_version_id",
    )

    with pytest.raises(NotAttributedError):
        await set_baseline(scope, session, wanted_version_id, run_id)


async def test_baseline_run_id_is_nulled_when_the_run_is_deleted(
    session: AsyncSession, scope: Scope
):
    prompt = await create_prompt(scope, session, name="Greeting", content="Say hi.")
    version = await commit_version(scope, session, prompt.id, message="v1")
    version_id = version.id

    endpoint = await create_endpoint(scope, session, name="box", base_url="http://x/v1")
    run_id, _ = await _run_with_attributed_result(
        session, scope, endpoint.id, prompt_version_id=version_id
    )
    await set_baseline(scope, session, version_id, run_id)

    await delete_run(scope, session, run_id)
    session.expire_all()

    survivor = await get_version(scope, session, version_id)
    assert survivor is not None
    assert survivor.baseline_run_id is None


async def test_deleting_a_prompt_cascades_its_versions_and_nulls_result_attribution(
    session: AsyncSession, scope: Scope
):
    prompt = await create_prompt(scope, session, name="Greeting", content="Say hi.")
    prompt_id = prompt.id
    version = await commit_version(scope, session, prompt_id, message="v1")
    version_id = version.id

    endpoint = await create_endpoint(scope, session, name="box", base_url="http://x/v1")
    _, result_id = await _run_with_attributed_result(
        session, scope, endpoint.id, prompt_version_id=version_id
    )

    await delete_prompt(scope, session, prompt_id)
    session.expire_all()

    assert await get_version(scope, session, version_id) is None

    result = await get_run_result(scope, session, result_id)
    assert result is not None
    assert result.system_prompt_version_id is None
    # The snapshot text survives regardless — that is the whole point of a
    # snapshot.
    assert result.test_case_text == "Say hi."
