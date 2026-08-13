"""`create_run_record` against a real Postgres — the snapshot invariant.

Ports `git show master:tests/integration/run-create.test.ts` onto the pivot's
names and adds what the pivot introduced: `run_results.prompt_version_id`
attribution, which is decided here and nowhere else.

Only the wired-up function can show any of this. The rules underneath it are
already covered without a database (`tests/test_attribution.py` for the version
matcher, `tests/test_effective_prompt.py` for the resolution, and
`tests/test_tool_config.py` for the refusals); what needs Postgres is that they
end up *frozen* in the row, that they stay frozen when everything they came
from is edited away, and that a failure mid-way leaves no run behind.

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
from app.repos.prompts import create_prompt, update_prompt
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
    prompt_id: int
    version_id: int
    toolset_id: int
    tool_id: int
    group_id: int
    test_case_id: int


async def _seed(session: AsyncSession, scope: Scope) -> Fixture:
    """One machine, one committed prompt, one manual tool, one tool test case."""
    machine = await create_machine(
        scope,
        session,
        name="test-box",
        base_url="http://127.0.0.1:9/v1",
        cpu="EPYC 7443P",
        gpu="RTX 6000 Ada",
    )
    prompt = await create_prompt(scope, session, name="base", content="BASE PROMPT TEXT")
    version = await commit_version(scope, session, prompt.id, message="first")

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
        content="ORIGINAL TEST CASE TEXT",
        expected_output="Names the delivery date.",
        prompt_id=prompt.id,
        mode="append",
        custom_text="CUSTOM TAIL",
        tool_mode="execute",
        max_turns=4,
        sort_order=10,
    )
    await replace_toolset_links(scope, session, test_case.id, [toolset.id])

    return Fixture(
        scope=scope,
        machine_id=machine.id,
        prompt_id=prompt.id,
        version_id=version.id,
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
    assert before.test_case_text == "ORIGINAL TEST CASE TEXT"
    assert before.effective_prompt_text == "BASE PROMPT TEXT\n\nCUSTOM TAIL"
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

    # Now change everything the snapshot came from.
    await update_test_case(
        scope, session, fixture.test_case_id, {"content": "EDITED TEST CASE TEXT"}
    )
    await update_prompt(scope, session, fixture.prompt_id, {"content": "EDITED PROMPT TEXT"})
    await update_tool(scope, session, fixture.tool_id, {"mock_response": "EDITED MOCK RESPONSE"})
    await delete_toolset(scope, session, fixture.toolset_id)
    session.expire_all()

    [after] = await list_run_results(scope, session, created.run_id)
    assert after.test_case_text == "ORIGINAL TEST CASE TEXT"
    assert after.effective_prompt_text == "BASE PROMPT TEXT\n\nCUSTOM TAIL"
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


async def test_attributes_a_clean_draft_to_its_committed_version(
    session: AsyncSession, scope: Scope
):
    fixture = await _seed(session, scope)

    created = await create_run_record(
        scope,
        session,
        machine_id=fixture.machine_id,
        model_id="qwen3-32b",
        group_ids=[fixture.group_id],
        probe=_no_probe,
    )

    [result] = await list_run_results(scope, session, created.run_id)
    assert result.prompt_version_id == fixture.version_id


async def test_a_dirty_draft_and_a_promptless_case_are_not_attributed(
    session: AsyncSession, scope: Scope
):
    fixture = await _seed(session, scope)
    await update_prompt(scope, session, fixture.prompt_id, {"content": "EDITED, UNCOMMITTED"})
    session.expire_all()

    # A second case in the same group referencing no prompt at all.
    await create_test_case(
        scope,
        session,
        group_id=fixture.group_id,
        title="Standalone",
        content="No prompt behind this one.",
        sort_order=20,
    )

    created = await create_run_record(
        scope,
        session,
        machine_id=fixture.machine_id,
        model_id="qwen3-32b",
        group_ids=[fixture.group_id],
        probe=_no_probe,
    )

    dirty, promptless = await list_run_results(scope, session, created.run_id)
    assert dirty.prompt_version_id is None
    assert dirty.effective_prompt_text == "EDITED, UNCOMMITTED\n\nCUSTOM TAIL"
    assert promptless.prompt_version_id is None
    assert promptless.effective_prompt_text is None
    assert promptless.tools_snapshot is None
    assert [dirty.sort_order, promptless.sort_order] == [0, 1]


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
