"""`create_run_record` against a real Postgres — the snapshot invariant.

Ports `git show master:tests/integration/run-create.test.ts` onto the pivot's
names and adds what the two pivots introduced: version **attribution**, and the
prompt-kinds split that makes it exact.

**The central assertion of the prompt-kinds spec lives here**: a run created
from a test case with both slots filled freezes **three texts** — the system
prompt's draft, the task prompt's draft, and the case's own content — and **two
version ids**, one per slot, each null exactly when *that* prompt's draft is
dirty. The two slots are independent, and every assertion below reads the
**stored column** rather than any flag derived from it, per `CLAUDE.md`.

Only the wired-up function can show any of this. The rules underneath it are
already covered without a database (`tests/test_attribution.py` for the version
matcher, `tests/test_message_assembly.py` for assembly and the user-message
guard, `tests/test_tool_config.py` for the tool refusals); what needs Postgres
is that they end up *frozen* in the row, that they stay frozen when everything
they came from is edited away, and that a failure mid-way leaves no run behind.

The endpoint probe is stubbed throughout: `create_run_record` calls it before
it writes anything, there is no endpoint here, and it is not what these tests
are about.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.repos.machines import create_machine, list_machine_models
from app.repos.prompt_versions import commit_version
from app.repos.prompts import create_prompt, delete_prompt, update_prompt
from app.repos.runs import list_run_results, list_runs
from app.repos.test_cases import (
    create_test_case,
    create_test_group,
    replace_toolset_links,
    update_test_case,
)
from app.repos.toolsets import create_tool, create_toolset, delete_toolset, update_tool
from app.scope import CrossCustomerError, Scope
from app.services.llm_info import LlmInfo
from app.services.message_assembly import NoUserMessageError
from app.services.run_create import RunCreateError, create_run_record
from app.services.tool_config import ToolConfigError

CreateWorkspace = Callable[[str], Awaitable[tuple[int, Scope]]]


async def _no_probe(base_url: str, api_key: str | None, model_id: str) -> LlmInfo | None:
    """A server that revealed nothing — the common case, and the default here."""
    del base_url, api_key, model_id
    return None


async def _vllm_probe(base_url: str, api_key: str | None, model_id: str) -> LlmInfo:
    del base_url, api_key, model_id
    return LlmInfo(server="vLLM", version="0.8.5", details={"max_model_len": "32768"})


@dataclass
class Fixture:
    scope: Scope
    machine_id: int
    system_prompt_id: int
    system_version_id: int
    task_prompt_id: int
    task_version_id: int
    toolset_id: int
    tool_id: int
    group_id: int
    test_case_id: int


SYSTEM_TEXT = "SYSTEM PROMPT TEXT"
TASK_TEXT = "TASK PROMPT TEXT"
CASE_TEXT = "ORIGINAL TEST CASE TEXT"


async def _seed(session: AsyncSession, scope: Scope) -> Fixture:
    """One machine, **two** committed prompts — one per kind — a manual tool,
    and one tool test case whose two slots are both filled.

    Both slots on the same case is what makes this fixture worth its size: it
    is the only shape that can show the two texts and the two version ids
    staying independent of each other.
    """
    machine = await create_machine(
        scope,
        session,
        name="test-box",
        base_url="http://127.0.0.1:9/v1",
        cpu="EPYC 7443P",
        gpu="RTX 6000 Ada",
    )
    system_prompt = await create_prompt(
        scope, session, name="framing", content=SYSTEM_TEXT, kind="system"
    )
    system_version = await commit_version(
        scope, session, system_prompt.id, message="first system"
    )
    task_prompt = await create_prompt(
        scope, session, name="instruction", content=TASK_TEXT, kind="task"
    )
    task_version = await commit_version(scope, session, task_prompt.id, message="first task")

    toolset = await create_toolset(scope, session, name="Support Desk", kind="manual")
    tool = await create_tool(
        scope,
        session,
        toolset.id,
        name="lookup_order",
        description="Look up an order.",
        parameters_json='{"type": "object", "properties": {}}',
        mock_response="ORIGINAL MOCK RESPONSE",
    )

    group = await create_test_group(scope, session, name="General")
    test_case = await create_test_case(
        scope,
        session,
        group_id=group.id,
        title="Order status",
        content=CASE_TEXT,
        expected_output="Names the delivery date.",
        system_prompt_id=system_prompt.id,
        task_prompt_id=task_prompt.id,
        tool_mode="execute",
        max_turns=4,
        sort_order=10,
    )
    await replace_toolset_links(scope, session, test_case.id, [toolset.id])

    return Fixture(
        scope=scope,
        machine_id=machine.id,
        system_prompt_id=system_prompt.id,
        system_version_id=system_version.id,
        task_prompt_id=task_prompt.id,
        task_version_id=task_version.id,
        toolset_id=toolset.id,
        tool_id=tool.id,
        group_id=group.id,
        test_case_id=test_case.id,
    )


async def test_freezes_text_prompt_and_tools_against_later_edits(
    session: AsyncSession, scope: Scope
):
    fixture = await _seed(session, scope)

    created = await create_run_record(
        scope,
        session,
        machine_id=fixture.machine_id,
        model_id="qwen3-32b",
        group_ids=[fixture.group_id],
        params={"temperature": 0.2},
        comment="  first pass  ",
        probe=_no_probe,
    )
    assert created.result_count == 1
    assert created.machine_name == "test-box"
    assert created.group_names == ["General"]

    [before] = await list_run_results(scope, session, created.run_id)
    assert before.test_case_text == CASE_TEXT
    assert before.system_prompt_text == SYSTEM_TEXT
    assert before.task_prompt_text == TASK_TEXT
    assert before.expected_output == "Names the delivery date."
    assert before.group_name == "General"
    assert before.test_case_title == "Order status"
    assert before.tool_mode == "execute"
    assert before.max_turns == 4
    assert before.status == "pending"
    assert before.tools_snapshot is not None
    assert "ORIGINAL MOCK RESPONSE" in before.tools_snapshot

    snapshot = json.loads(before.tools_snapshot)
    assert snapshot[0]["definition"] == {
        "type": "function",
        "function": {
            "name": "lookup_order",
            "description": "Look up an order.",
            "parameters": {"type": "object", "properties": {}},
        },
    }
    assert snapshot[0]["source"] == "manual"
    assert snapshot[0]["toolset_name"] == "Support Desk"

    # Now change everything the snapshot came from — including both prompts.
    await update_test_case(
        scope, session, fixture.test_case_id, {"content": "EDITED TEST CASE TEXT"}
    )
    await update_prompt(
        scope, session, fixture.system_prompt_id, {"content": "EDITED SYSTEM TEXT"}
    )
    await update_prompt(scope, session, fixture.task_prompt_id, {"content": "EDITED TASK TEXT"})
    await update_tool(scope, session, fixture.tool_id, {"mock_response": "EDITED MOCK RESPONSE"})
    await delete_toolset(scope, session, fixture.toolset_id)
    session.expire_all()

    [after] = await list_run_results(scope, session, created.run_id)
    assert after.test_case_text == CASE_TEXT
    assert after.system_prompt_text == SYSTEM_TEXT
    assert after.task_prompt_text == TASK_TEXT
    assert after.tools_snapshot == before.tools_snapshot
    # The FK survives for cross-run comparison; the snapshot is what renders.
    assert after.test_case_id == fixture.test_case_id


async def test_records_the_run_row_and_the_model_sighting(
    session: AsyncSession, scope: Scope
):
    fixture = await _seed(session, scope)

    created = await create_run_record(
        scope,
        session,
        machine_id=fixture.machine_id,
        model_id="qwen3-32b",
        group_ids=[fixture.group_id],
        params={"temperature": 0.2},
        comment="  first pass  ",
        probe=_vllm_probe,
    )

    [run] = await list_runs(scope, session)
    assert run.id == created.run_id
    assert run.status == "pending"
    assert run.model_id == "qwen3-32b"
    assert json.loads(run.params) == {"temperature": 0.2}
    assert json.loads(run.group_names) == ["General"]
    assert run.comment == "first pass"
    assert json.loads(run.machine_snapshot) == {
        "name": "test-box",
        "base_url": "http://127.0.0.1:9/v1",
        "cpu": "EPYC 7443P",
        "ram": None,
        "gpu": "RTX 6000 Ada",
    }
    assert json.loads(run.llm_info) == {
        "server": "vLLM",
        "version": "0.8.5",
        "details": {"max_model_len": "32768"},
    }

    # The model is remembered against the machine even though it never showed
    # up in /models — the next run can offer it.
    sightings = await list_machine_models(scope, session, machine_id=fixture.machine_id)
    assert [(row.model_id, row.source, row.currently_loaded) for row in sightings] == [
        ("qwen3-32b", "run", False)
    ]


async def test_no_params_and_no_comment_stay_null(session: AsyncSession, scope: Scope):
    fixture = await _seed(session, scope)

    created = await create_run_record(
        scope,
        session,
        machine_id=fixture.machine_id,
        model_id="qwen3-32b",
        group_ids=[fixture.group_id],
        params={},
        comment="   ",
        probe=_no_probe,
    )

    [run] = await list_runs(scope, session)
    assert run.id == created.run_id
    assert run.params is None
    assert run.comment is None
    assert run.llm_info is None


async def _run_once(session: AsyncSession, scope: Scope, fixture: Fixture):
    """Creates a run over the whole seeded group and returns its result rows."""
    created = await create_run_record(
        scope,
        session,
        machine_id=fixture.machine_id,
        model_id="qwen3-32b",
        group_ids=[fixture.group_id],
        probe=_no_probe,
    )
    return await list_run_results(scope, session, created.run_id)


# ---------------------------------------------------------------------------
# The central assertion: three texts, two version ids, one per slot
# ---------------------------------------------------------------------------


async def test_freezes_three_texts_and_two_version_ids_from_a_two_slot_case(
    session: AsyncSession, scope: Scope
):
    """Both slots clean: every one of the five columns says what was sent."""
    fixture = await _seed(session, scope)

    [result] = await _run_once(session, scope, fixture)

    assert result.system_prompt_text == SYSTEM_TEXT
    assert result.task_prompt_text == TASK_TEXT
    assert result.test_case_text == CASE_TEXT
    assert result.system_prompt_version_id == fixture.system_version_id
    assert result.task_prompt_version_id == fixture.task_version_id
    # The two slots really did resolve to two *different* versions — an
    # implementation that filled both columns from one slot would pass every
    # assertion above except this one.
    assert fixture.system_version_id != fixture.task_version_id


async def test_a_dirty_system_draft_costs_only_the_system_attribution(
    session: AsyncSession, scope: Scope
):
    """Per-slot independence, half one.

    The stored columns are read directly: `system_prompt_version_id` goes null
    while `task_prompt_version_id` keeps its version, and both texts are still
    frozen verbatim — a dirty draft is unattributed, never unsent.
    """
    fixture = await _seed(session, scope)
    await update_prompt(
        scope, session, fixture.system_prompt_id, {"content": "EDITED, UNCOMMITTED"}
    )
    session.expire_all()

    [result] = await _run_once(session, scope, fixture)

    assert result.system_prompt_version_id is None
    assert result.task_prompt_version_id == fixture.task_version_id
    assert result.system_prompt_text == "EDITED, UNCOMMITTED"
    assert result.task_prompt_text == TASK_TEXT


async def test_a_dirty_task_draft_costs_only_the_task_attribution(
    session: AsyncSession, scope: Scope
):
    """Per-slot independence, half two — the mirror image of the test above."""
    fixture = await _seed(session, scope)
    await update_prompt(
        scope, session, fixture.task_prompt_id, {"content": "EDITED, UNCOMMITTED"}
    )
    session.expire_all()

    [result] = await _run_once(session, scope, fixture)

    assert result.task_prompt_version_id is None
    assert result.system_prompt_version_id == fixture.system_version_id
    assert result.task_prompt_text == "EDITED, UNCOMMITTED"
    assert result.system_prompt_text == SYSTEM_TEXT


async def test_both_drafts_dirty_leaves_both_columns_null(
    session: AsyncSession, scope: Scope
):
    fixture = await _seed(session, scope)
    await update_prompt(scope, session, fixture.system_prompt_id, {"content": "S EDITED"})
    await update_prompt(scope, session, fixture.task_prompt_id, {"content": "T EDITED"})
    session.expire_all()

    [result] = await _run_once(session, scope, fixture)

    assert result.system_prompt_version_id is None
    assert result.task_prompt_version_id is None
    assert (result.system_prompt_text, result.task_prompt_text) == ("S EDITED", "T EDITED")


async def test_recommitting_a_dirty_draft_attributes_the_next_run_again(
    session: AsyncSession, scope: Scope
):
    """The version id is a fact about the text, not about the run's age.

    Editing then committing gives the task slot a *new* version, and the next
    run's stored column names that one rather than the original.
    """
    fixture = await _seed(session, scope)
    await update_prompt(scope, session, fixture.task_prompt_id, {"content": "SECOND TASK TEXT"})
    session.expire_all()
    second = await commit_version(scope, session, fixture.task_prompt_id, message="second")

    [result] = await _run_once(session, scope, fixture)

    assert result.task_prompt_version_id == second.id
    assert result.task_prompt_version_id != fixture.task_version_id
    assert result.task_prompt_text == "SECOND TASK TEXT"


async def test_an_empty_slot_freezes_null_text_and_null_attribution(
    session: AsyncSession, scope: Scope
):
    """A case with no prompts at all, and one with only a task prompt.

    The columns deliberately do not distinguish "empty slot" from "dirty
    draft" — both are "no committed version was sent here" — but the *text*
    column does, and that is what is asserted.
    """
    fixture = await _seed(session, scope)

    await create_test_case(
        scope,
        session,
        group_id=fixture.group_id,
        title="Standalone",
        content="No prompt behind this one.",
        sort_order=20,
    )
    await create_test_case(
        scope,
        session,
        group_id=fixture.group_id,
        title="Task only",
        task_prompt_id=fixture.task_prompt_id,
        sort_order=30,
    )

    both, promptless, task_only = await _run_once(session, scope, fixture)

    assert both.sort_order == 0
    assert (promptless.sort_order, task_only.sort_order) == (1, 2)

    assert promptless.system_prompt_text is None
    assert promptless.task_prompt_text is None
    assert promptless.system_prompt_version_id is None
    assert promptless.task_prompt_version_id is None
    assert promptless.tools_snapshot is None

    # `content` is nullable now: the task prompt is the whole user message.
    assert task_only.test_case_text is None
    assert task_only.task_prompt_text == TASK_TEXT
    assert task_only.task_prompt_version_id == fixture.task_version_id
    assert task_only.system_prompt_text is None
    assert task_only.system_prompt_version_id is None


async def test_refuses_a_case_a_deleted_prompt_left_with_no_user_message(
    session: AsyncSession, scope: Scope
):
    """The hole both authoring guards leave open.

    A case whose task prompt was its entire user message passes authoring; a
    later `delete_prompt` `SET NULL`s the slot and leaves it with nothing to
    send. Run creation is the third place the one shared guard runs, and it
    refuses *before* anything is written.
    """
    fixture = await _seed(session, scope)
    await create_test_case(
        scope,
        session,
        group_id=fixture.group_id,
        title="Task only",
        task_prompt_id=fixture.task_prompt_id,
        sort_order=20,
    )
    await delete_prompt(scope, session, fixture.task_prompt_id)
    session.expire_all()

    with pytest.raises(NoUserMessageError, match='Test case "Task only"'):
        await _run_once(session, scope, fixture)

    assert await list_runs(scope, session) == []


async def test_rolls_the_run_back_when_a_result_row_cannot_be_written(
    session: AsyncSession, scope: Scope, monkeypatch: pytest.MonkeyPatch
):
    fixture = await _seed(session, scope)

    async def explode(*args, **kwargs):
        raise RuntimeError("simulated failure mid-transaction")

    # The model sighting is the last write inside the transaction, so failing
    # it proves the run row and its result rows go back with it.
    monkeypatch.setattr("app.services.run_create.touch_machine_model", explode)

    with pytest.raises(RuntimeError, match="simulated failure mid-transaction"):
        await create_run_record(
            scope,
            session,
            machine_id=fixture.machine_id,
            model_id="qwen3-32b",
            group_ids=[fixture.group_id],
            probe=_no_probe,
        )

    # Without the transaction the `runs` row would be left behind, and Resume
    # would report an empty run as finished.
    assert await list_runs(scope, session) == []


async def test_refuses_a_selection_that_would_measure_nothing(
    session: AsyncSession, scope: Scope
):
    fixture = await _seed(session, scope)

    with pytest.raises(RunCreateError, match="at least one test group"):
        await create_run_record(
            scope,
            session,
            machine_id=fixture.machine_id,
            model_id="qwen3-32b",
            group_ids=[],
            probe=_no_probe,
        )

    with pytest.raises(RunCreateError, match="no longer exist"):
        await create_run_record(
            scope,
            session,
            machine_id=fixture.machine_id,
            model_id="qwen3-32b",
            group_ids=[fixture.group_id + 999],
            probe=_no_probe,
        )

    empty = await create_test_group(scope, session, name="Empty")
    with pytest.raises(RunCreateError, match="contain no test cases"):
        await create_run_record(
            scope,
            session,
            machine_id=fixture.machine_id,
            model_id="qwen3-32b",
            group_ids=[empty.id],
            probe=_no_probe,
        )

    assert await list_runs(scope, session) == []


async def test_refuses_a_machine_this_workspace_cannot_see(
    session: AsyncSession, create_workspace: CreateWorkspace
):
    _, scope_a = await create_workspace("A")
    _, scope_b = await create_workspace("B")
    fixture = await _seed(session, scope_a)
    other = await _seed(session, scope_b)

    with pytest.raises(RunCreateError, match="Machine not found"):
        await create_run_record(
            scope_a,
            session,
            machine_id=other.machine_id,
            model_id="qwen3-32b",
            group_ids=[fixture.group_id],
            probe=_no_probe,
        )


async def test_refuses_a_tool_test_with_no_enabled_tools(
    session: AsyncSession, scope: Scope
):
    fixture = await _seed(session, scope)
    await update_tool(scope, session, fixture.tool_id, {"enabled": False})
    session.expire_all()

    with pytest.raises(ToolConfigError, match="no enabled tools"):
        await create_run_record(
            scope,
            session,
            machine_id=fixture.machine_id,
            model_id="qwen3-32b",
            group_ids=[fixture.group_id],
            probe=_no_probe,
        )

    assert await list_runs(scope, session) == []


async def test_refuses_two_toolsets_that_define_the_same_tool_name(
    session: AsyncSession, scope: Scope
):
    fixture = await _seed(session, scope)
    second = await create_toolset(scope, session, name="Second desk", kind="manual")
    await create_tool(scope, session, second.id, name="lookup_order", mock_response="OTHER")
    await replace_toolset_links(
        scope, session, fixture.test_case_id, [fixture.toolset_id, second.id]
    )

    with pytest.raises(ToolConfigError, match="lookup_order"):
        await create_run_record(
            scope,
            session,
            machine_id=fixture.machine_id,
            model_id="qwen3-32b",
            group_ids=[fixture.group_id],
            probe=_no_probe,
        )

    assert await list_runs(scope, session) == []


async def test_refuses_a_test_case_linked_to_a_foreign_toolset(
    session: AsyncSession, create_workspace: CreateWorkspace
):
    """The link itself is refused at authoring time, so this is the belt to
    that suspenders: even a row that got there another way must not put another
    workspace's tool definitions in front of a model.
    """
    _, scope_a = await create_workspace("A")
    _, scope_b = await create_workspace("B")
    fixture = await _seed(session, scope_a)
    other = await _seed(session, scope_b)

    await session.execute(
        text("UPDATE test_case_toolsets SET toolset_id = :other WHERE test_case_id = :case_id"),
        {"other": other.toolset_id, "case_id": fixture.test_case_id},
    )

    with pytest.raises(CrossCustomerError):
        await create_run_record(
            scope_a,
            session,
            machine_id=fixture.machine_id,
            model_id="qwen3-32b",
            group_ids=[fixture.group_id],
            probe=_no_probe,
        )
