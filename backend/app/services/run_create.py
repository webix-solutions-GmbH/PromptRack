"""Creating a run, independent of how it was asked for.

The **snapshot invariant** lives here — freeze the **three texts** (the system
prompt's draft, the task prompt's draft, the test case's own content), the
**two version ids** attributing them, and the tool definitions into
`run_results` at creation time — so the new-run endpoint and the MCP
`create_run` tool cannot drift apart: one parses a request body, the other
JSON-RPC arguments, and both end up in this one function. Editing or deleting a
test case, a prompt or a toolset afterwards can therefore never rewrite history.

The three texts are frozen *separately* and assembly is deliberately deferred
to execution (`app.services.message_assembly`, called from the executor). Two
things fall out of that. Drift reporting can say "the task prompt changed"
rather than "the user message changed"; and each text frozen here is byte-for-
byte what goes on the wire, so `match_version` compares against the sent text
instead of a derived one — which is what makes the attribution below exact
rather than a plausible guess.

The line between frozen and live is **content vs. credentials**: text, tool
definitions and a manual tool's canned response travel with the run; a
endpoint's `base_url`/`api_key` and a toolset's `mcp_url`/headers are read live
at execution time, so a moved endpoint does not break Resume.

Three things deliberately stay *outside* the transaction, in this order:
validation (which throws before anything is written), then the endpoint probe
(a network call, which must never hold a transaction open). Only the three
writes — the run row, all of its result rows, the model sighting — are one
unit, because a crash between them used to leave a run with no test cases in
it, which Resume would have reported as finished.

Like every repository function, this does not commit: the caller's request
boundary does. The `transaction()` block below is a SAVEPOINT inside that unit
of work, which is what makes the three writes atomic without this function
having to know where the request ends.
"""

import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Endpoint, Prompt, TestCase, TestGroup
from app.repos.endpoints import get_endpoint, touch_endpoint_model
from app.repos.prompt_versions import list_version_refs
from app.repos.prompts import list_prompts_by_ids
from app.repos.runs import create_run, insert_run_results
from app.repos.scoped import transaction, utc_now
from app.repos.test_cases import (
    SnapshotToolRow,
    list_snapshot_tool_rows,
    list_test_cases,
    list_test_groups_by_ids,
    list_toolset_links,
)
from app.scope import Scope
from app.services.attribution import VersionRef, match_version
from app.services.llm_info import LlmInfo, probe_llm_info, serialize_llm_info
from app.services.message_assembly import assert_user_message
from app.services.params import merge_params, parse_params_json
from app.services.tool_config import assert_tool_config
from app.services.tool_loop import SnapshotTool, ToolDefinition, serialize_tools_snapshot

#: What run creation calls to learn about the endpoint. A parameter rather than
#: a hard-wired import so tests (and any caller that already knows) can skip the
#: network; the default is the real probe.
LlmInfoProbe = Callable[[str, str | None, str], Awaitable[LlmInfo | None]]


class RunCreateError(Exception):
    """A run that cannot be created, with the sentence a caller can show.

    Tool-configuration problems raise `app.services.tool_config.ToolConfigError`,
    a case with no user message raises
    `app.services.message_assembly.NoUserMessageError`, and a reference into
    another workspace raises `app.scope.CrossCustomerError` — each is a refusal
    in its own right, and the distinction is worth keeping at the API boundary.
    """


@dataclass(frozen=True)
class CreatedRun:
    """What the caller needs to redirect to (or report about) the new run."""

    run_id: int
    endpoint_id: int
    endpoint_name: str
    model_id: str
    group_names: list[str]
    result_count: int


async def create_run_record(
    scope: Scope,
    session: AsyncSession,
    *,
    endpoint_id: int,
    model_id: str,
    group_ids: Sequence[int],
    params: Mapping[str, Any] | None = None,
    comment: str | None = None,
    probe: LlmInfoProbe | None = None,
) -> CreatedRun:
    """Creates a run and materializes one `run_results` row per test case.

    `params` is this run's **overrides** over the endpoint's `default_params`,
    not the whole of what it sends: the two are merged below, and a `None` value
    here unsets a default rather than sending a null (`app.services.params`).
    """
    unique_group_ids = list(dict.fromkeys(group_ids))
    if not unique_group_ids:
        raise RunCreateError("Select at least one test group.")

    endpoint = await get_endpoint(scope, session, endpoint_id)
    if endpoint is None:
        raise RunCreateError("Endpoint not found.")

    # The snapshot point for parameters. The endpoint's defaults are read once,
    # here, and only the merged result is frozen below — so editing those
    # defaults afterwards can no more change what this run sends (or resends on
    # Resume) than editing a prompt can change what it asked.
    merged_params = merge_params(parse_params_json(endpoint.default_params), params)

    groups = await list_test_groups_by_ids(scope, session, unique_group_ids)
    if not groups:
        raise RunCreateError("The selected test groups no longer exist.")

    cases = await list_test_cases(scope, session, group_ids=[group.id for group in groups])
    if not cases:
        raise RunCreateError("The selected test groups contain no test cases.")

    prompts_by_id, version_refs = await _resolve_prompts(scope, session, cases)
    tool_snapshots = await _resolve_tool_snapshots(scope, session, cases)
    _assert_user_messages(cases, prompts_by_id)
    await _assert_tool_config(scope, session, cases)

    # Ask the endpoint about itself (server software, model metadata) and
    # freeze the answer with the run. Best-effort: an unreachable or
    # tight-lipped server just leaves the snapshot empty.
    info = await (probe or probe_llm_info)(endpoint.base_url, endpoint.api_key, model_id)

    now = utc_now()
    cleaned_comment = comment.strip() if comment else None

    async with transaction(session):
        run = await create_run(
            scope,
            session,
            endpoint_id=endpoint.id,
            endpoint_snapshot=_endpoint_snapshot(endpoint),
            model_id=model_id,
            params=json.dumps(merged_params) if merged_params else None,
            llm_info=serialize_llm_info(info),
            comment=cleaned_comment or None,
            group_names=json.dumps([group.name for group in groups]),
            status="pending",
        )

        rows = _result_rows(
            groups=groups,
            cases=cases,
            prompts_by_id=prompts_by_id,
            version_refs=version_refs,
            tool_snapshots=tool_snapshots,
        )
        await insert_run_results(scope, session, run.id, rows)

        # Remember the model against the endpoint so the next run can offer it
        # even when it was typed by hand and never showed up in /models.
        await touch_endpoint_model(
            scope, session, endpoint_id=endpoint.id, model_id=model_id, source="run", at=now
        )

        run_id = run.id

    return CreatedRun(
        run_id=run_id,
        endpoint_id=endpoint.id,
        endpoint_name=endpoint.name,
        model_id=model_id,
        group_names=[group.name for group in groups],
        result_count=len(rows),
    )


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


async def _resolve_prompts(
    scope: Scope, session: AsyncSession, cases: Sequence[TestCase]
) -> tuple[dict[int, Prompt], dict[int, list[VersionRef]]]:
    """The prompt assets these test cases reference, plus their committed text.

    Both slots at once: the ids are collected from ``system_prompt_id`` and
    ``task_prompt_id`` into one deduplicated list, so this stays one
    ``list_prompts_by_ids`` and one ``list_version_refs`` however many slots a
    suite fills. Keying the results by prompt id serves both slots without any
    per-slot bookkeeping — and a prompt can never occupy both slots of one case,
    because a prompt has exactly one kind and
    :func:`~app.repos.prompts.assert_prompt_slot` enforces it, so no
    disambiguation is needed here.

    Only the prompts actually referenced are read, and only the three columns
    the attribution comparison needs — a suite's whole version history would
    otherwise be pulled through for one equality check per row.
    """
    prompt_ids = list(
        dict.fromkeys(
            prompt_id
            for case in cases
            for prompt_id in (case.system_prompt_id, case.task_prompt_id)
            if prompt_id is not None
        )
    )
    prompts = await list_prompts_by_ids(scope, session, prompt_ids)
    refs = await list_version_refs(scope, session, prompt_ids)
    return {prompt.id: prompt for prompt in prompts}, refs


def _assert_user_messages(
    cases: Sequence[TestCase], prompts_by_id: Mapping[int, Prompt]
) -> None:
    """Refuses, before anything is written, a case that would send no user
    message at all.

    The authoring-time guard already ran when the case was saved, but a prompt
    deleted since then ``SET NULL``s the slot on every case that referenced it,
    which can leave a case with neither a task prompt nor content. This is the
    only thing standing between that deletion and an empty user message reaching
    the provider, so it is the same shared function checked again here — exactly
    the way ``assert_tool_config`` is — and the refusal names the case.
    """
    for case in cases:
        task_prompt = (
            prompts_by_id.get(case.task_prompt_id)
            if case.task_prompt_id is not None
            else None
        )
        assert_user_message(
            None if task_prompt is None else task_prompt.content,
            case.content,
            subject=f'Test case "{case.title}"',
        )


async def _resolve_tool_snapshots(
    scope: Scope, session: AsyncSession, cases: Sequence[TestCase]
) -> dict[int, list[SnapshotTool]]:
    """Freezes each test case's tool configuration.

    Definitions and canned responses are content and travel with the run; an
    MCP tool only records which toolset it came from, because its endpoint and
    auth are credentials that must be read live at execution time.

    `list_snapshot_tool_rows` is scoped on `toolsets`, which is what closes the
    cross-workspace path: a case linked to a foreign toolset contributes no
    tools at all.
    """
    tool_case_ids = [case.id for case in cases if case.tool_mode != "none"]
    if not tool_case_ids:
        return {}

    by_case: dict[int, list[SnapshotTool]] = {}
    for row in await list_snapshot_tool_rows(scope, session, tool_case_ids):
        if not row.enabled:
            continue
        by_case.setdefault(row.test_case_id, []).append(
            SnapshotTool(
                definition=_tool_definition(row),
                source=row.source,
                toolset_id=row.toolset_id,
                toolset_name=row.toolset_name,
                mock_response=row.mock_response,
            )
        )
    return by_case


def _tool_definition(row: SnapshotToolRow) -> ToolDefinition:
    """One entry of the OpenAI-compatible `tools` array.

    A stored schema that is not a JSON object — empty, malformed, an array —
    degrades to the no-argument schema rather than raising, so one bad tool row
    cannot break a whole run. Several servers reject a bare `{}`, so the
    fallback spells the shape out.
    """
    function: dict[str, Any] = {"name": row.tool_name}
    if row.description and row.description.strip():
        function["description"] = row.description
    function["parameters"] = _parameter_schema(row.parameters_json)
    return {"type": "function", "function": function}


def _parameter_schema(raw: str | None) -> dict[str, Any]:
    if raw and raw.strip():
        try:
            parsed = json.loads(raw)
        except ValueError:
            parsed = None
        if isinstance(parsed, dict):
            return parsed
    return {"type": "object", "properties": {}}


async def _assert_tool_config(
    scope: Scope, session: AsyncSession, cases: Sequence[TestCase]
) -> None:
    """Refuses, before anything is written, a tool test that would measure
    nothing.

    A test case with a tool mode but no usable tools would quietly become an
    ordinary prompt and produce a result that looks meaningful; two toolsets
    defining the same tool name would only ever show the model one of the two.
    Both are the test-case editor's rules, checked here through the very same
    function, so a case authored through the API can never be one run creation
    would later reject — and the refusal names the case that needs fixing.
    """
    tool_cases = [case for case in cases if case.tool_mode != "none"]
    if not tool_cases:
        return

    links: dict[int, list[int]] = {}
    for link in await list_toolset_links(scope, session, [case.id for case in tool_cases]):
        links.setdefault(link.test_case_id, []).append(link.toolset_id)

    for case in tool_cases:
        await assert_tool_config(
            scope,
            session,
            tool_mode=case.tool_mode,
            toolset_ids=links.get(case.id, []),
            subject=f'Test case "{case.title}"',
        )


# ---------------------------------------------------------------------------
# The frozen rows
# ---------------------------------------------------------------------------


def _endpoint_snapshot(endpoint: Endpoint) -> str:
    """The endpoint as the run will forever display it: name, URL, hardware.

    `api_key` is deliberately absent — a run snapshot is display data and has no
    business holding a secret — while `base_url` is kept because "which endpoint
    produced these numbers" is part of the answer. Execution still reads the
    live endpoint row, so a moved endpoint does not break Resume.

    `platform` comes along because it is what makes the frozen `runs.params`
    readable later: `reasoning_effort` and `chat_template_kwargs` are only
    recognisable as knobs once you know which server was being asked.
    """
    return json.dumps(
        {
            "name": endpoint.name,
            "base_url": endpoint.base_url,
            "platform": endpoint.platform,
            "cpu": endpoint.cpu,
            "ram": endpoint.ram,
            "gpu": endpoint.gpu,
        }
    )


def _result_rows(
    *,
    groups: Sequence[TestGroup],
    cases: Sequence[TestCase],
    prompts_by_id: Mapping[int, Prompt],
    version_refs: Mapping[int, list[VersionRef]],
    tool_snapshots: Mapping[int, list[SnapshotTool]],
) -> list[dict[str, Any]]:
    """One frozen row per test case, in execution order (group by group).

    Each slot freezes its prompt's draft **verbatim** and attributes it against
    that prompt's own version list. Nothing is derived, so a version id here can
    only ever name text that was really sent — the misattribution the old
    `mode`/`custom_text` splice produced is gone by construction, not by a
    correction.
    """
    rows: list[dict[str, Any]] = []
    sort_order = 0

    def slot(prompt_id: int | None) -> tuple[str | None, int | None]:
        """One slot's frozen text and its attribution, or `(None, None)`."""
        prompt = prompts_by_id.get(prompt_id) if prompt_id is not None else None
        if prompt is None:
            return None, None
        # Attribution, not selection: a run always tests the current draft, and
        # this records which commit that draft happened to be. Null = a dirty
        # draft. The two slots are independent — one being dirty must not cost
        # the other its attribution.
        return prompt.content, match_version(
            prompt.content, version_refs.get(prompt.id, [])
        )

    for group in groups:
        for case in (case for case in cases if case.group_id == group.id):
            system_prompt_text, system_prompt_version_id = slot(case.system_prompt_id)
            task_prompt_text, task_prompt_version_id = slot(case.task_prompt_id)
            snapshot = [] if case.tool_mode == "none" else tool_snapshots.get(case.id, [])

            rows.append(
                {
                    "test_case_id": case.id,
                    "system_prompt_version_id": system_prompt_version_id,
                    "task_prompt_version_id": task_prompt_version_id,
                    "sort_order": sort_order,
                    "group_name": group.name,
                    "test_case_title": case.title,
                    # The data half of the user message only; the task prompt's
                    # half is the column beside it, and the executor joins them.
                    "test_case_text": case.content,
                    "expected_output": case.expected_output,
                    "system_prompt_text": system_prompt_text,
                    "task_prompt_text": task_prompt_text,
                    "tools_snapshot": (
                        serialize_tools_snapshot(snapshot) if snapshot else None
                    ),
                    "tool_mode": case.tool_mode,
                    "tool_choice": case.tool_choice,
                    "max_turns": case.max_turns,
                    "status": "pending",
                }
            )
            sort_order += 1

    return rows
