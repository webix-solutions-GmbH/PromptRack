"""The executor against a real database, with a scripted model.

`app.services.executor.execute_run` takes its chat streamer as a parameter for
exactly this — the seam `tool_loop` already established — so everything below
exercises the real persistence, the real advisory lock and the real scoping
while never opening a socket.

What is worth testing here is not "did it call the model" but the four
recovery invariants the module docstring names: every row persisted the moment
it finishes, a row error never stopping the run, `failed` meaning "the machine
was never reachable", and an interrupted row going back to `pending`.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.repos.machines import create_machine
from app.repos.prompts import create_prompt
from app.repos.runs import get_run, list_run_results
from app.repos.test_cases import create_test_case, create_test_group
from app.repos.toolsets import create_tool, create_toolset
from app.scope import Scope
from app.services.executor import RunAlreadyExecutingError, execute_run
from app.services.llm import LlmError, LlmResult, ToolCall
from app.services.run_create import create_run_record
from app.services.run_events import RunEvent
from app.services.run_lock import acquire_run_lock

CreateWorkspace = Callable[[str], Awaitable[tuple[int, Scope]]]


async def _no_probe(base_url: str, api_key: str | None, model_id: str) -> None:
    """Run creation probes the endpoint; these tests have none."""
    del base_url, api_key, model_id
    return None


def _result(text: str = "Hello.", **overrides: Any) -> LlmResult:
    defaults: dict[str, Any] = {
        "text": text,
        "ttft_ms": 10,
        "duration_ms": 110,
        "prompt_tokens": 7,
        "completion_tokens": 5,
        "tokens_estimated": False,
        "finish_reason": "stop",
    }
    return LlmResult(**{**defaults, **overrides})


def scripted(*answers: LlmResult | Exception) -> tuple[Any, list[Sequence[Any]]]:
    """A `ChatStreamer` that replays `answers` in order, recording its calls."""
    calls: list[Sequence[Any]] = []
    pending = list(answers)

    async def stream(
        base_url: str,
        api_key: str | None,
        model: str,
        messages: Sequence[Any],
        *,
        tools: Sequence[Mapping[str, Any]] | None = None,
        tool_choice: str | None = None,
        params: Mapping[str, Any] | None = None,
        on_delta: Callable[[str, str], None] | None = None,
    ) -> LlmResult:
        del base_url, api_key, model, tools, tool_choice, params
        calls.append(list(messages))
        answer = pending.pop(0) if pending else _result()
        if isinstance(answer, Exception):
            raise answer
        if on_delta is not None and answer.text:
            on_delta(answer.text, answer.text)
        return answer

    return stream, calls


async def make_run(
    scope: Scope,
    session: AsyncSession,
    *,
    titles: Sequence[str] = ("First",),
    tool_mode: str = "none",
    toolset_id: int | None = None,
    base_url: str = "http://machine.invalid/v1",
) -> int:
    """A machine, a group, one test case per title, and a run over them."""
    machine = await create_machine(scope, session, name="Box", base_url=base_url)
    group = await create_test_group(scope, session, name="Group")
    for index, title in enumerate(titles):
        case = await create_test_case(
            scope,
            session,
            group_id=group.id,
            title=title,
            content=f"Say {title}.",
            tool_mode=tool_mode,
            sort_order=index,
        )
        if toolset_id is not None:
            from app.repos.test_cases import replace_toolset_links

            await replace_toolset_links(scope, session, case.id, [toolset_id])
    await session.commit()

    created = await create_run_record(
        scope,
        session,
        machine_id=machine.id,
        model_id="test-model",
        group_ids=[group.id],
        probe=_no_probe,
    )
    await session.commit()
    return created.run_id


async def reload_results(scope: Scope, session: AsyncSession, run_id: int) -> list[Any]:
    """The executor writes through its own session, so ours has to re-read."""
    session.expire_all()
    return await list_run_results(scope, session, run_id)


class TestHappyPath:
    async def test_every_row_is_persisted_and_the_run_completes(
        self, session: AsyncSession, scope: Scope
    ) -> None:
        run_id = await make_run(scope, session, titles=("First", "Second"))
        stream, calls = scripted(_result("One."), _result("Two."))
        events: list[RunEvent] = []

        await execute_run(run_id, events.append, stream=stream)

        results = await reload_results(scope, session, run_id)
        assert [r.status for r in results] == ["ok", "ok"]
        assert [r.response_text for r in results] == ["One.", "Two."]
        assert [r.ttft_ms for r in results] == [10, 10]
        assert [r.completion_tokens for r in results] == [5, 5]
        # A plain prompt carries no transcript detail at all.
        assert [r.transcript_json for r in results] == [None, None]
        assert [r.turn_count for r in results] == [None, None]

        session.expire_all()
        run = await get_run(scope, session, run_id)
        assert run is not None
        assert run.status == "completed"
        assert run.started_at is not None and run.finished_at is not None

        # The system message is absent (no prompt was referenced) and the user
        # message is the frozen test-case text.
        assert [message["role"] for message in calls[0]] == ["user"]
        assert calls[0][0]["content"] == "Say First."

        types = [type(event).__name__ for event in events]
        assert types[0] == "RunStart"
        assert types[-1] == "RunDone"
        assert types.count("ResultDone") == 2

    async def test_a_finished_run_reports_nothing_pending(
        self, session: AsyncSession, scope: Scope
    ) -> None:
        run_id = await make_run(scope, session)
        stream, _ = scripted(_result())
        await execute_run(run_id, lambda _event: None, stream=stream)

        events: list[RunEvent] = []
        await execute_run(run_id, events.append, stream=stream)

        done = events[-1]
        assert type(done).__name__ == "RunDone"
        assert done.to_json() == {
            "type": "runDone",
            "run_id": run_id,
            "status": "completed",
            "nothing_pending": True,
        }


class TestMessageAssembly:
    """Assembly happens **here**, at execution, from the three frozen columns.

    Run creation freezes the parts separately so drift reporting can name them
    separately; this is the other end of that decision — what the provider
    actually receives. The pure rule is `tests/test_message_assembly.py`'s job;
    what only the wired-up executor can show is that the right *column* feeds
    each part of the request.
    """

    async def _run_with_slots(
        self,
        scope: Scope,
        session: AsyncSession,
        *,
        system_text: str | None,
        task_text: str | None,
        content: str | None,
    ) -> int:
        machine = await create_machine(scope, session, name="Box", base_url="http://x/v1")
        group = await create_test_group(scope, session, name="Group")
        system_prompt = (
            None
            if system_text is None
            else await create_prompt(
                scope, session, name="framing", content=system_text, kind="system"
            )
        )
        task_prompt = (
            None
            if task_text is None
            else await create_prompt(
                scope, session, name="instruction", content=task_text, kind="task"
            )
        )
        await create_test_case(
            scope,
            session,
            group_id=group.id,
            title="Case",
            content=content,
            system_prompt_id=None if system_prompt is None else system_prompt.id,
            task_prompt_id=None if task_prompt is None else task_prompt.id,
        )
        await session.commit()

        created = await create_run_record(
            scope,
            session,
            machine_id=machine.id,
            model_id="test-model",
            group_ids=[group.id],
            probe=_no_probe,
        )
        await session.commit()
        return created.run_id

    async def test_the_two_prompts_land_on_their_own_channels(
        self, session: AsyncSession, scope: Scope
    ) -> None:
        run_id = await self._run_with_slots(
            scope,
            session,
            system_text="You are terse.",
            task_text="Extract the PO number.",
            content="Invoice 4711, PO P00018",
        )
        stream, calls = scripted(_result())

        await execute_run(run_id, lambda _event: None, stream=stream)

        assert [message["role"] for message in calls[0]] == ["system", "user"]
        assert calls[0][0]["content"] == "You are terse."
        # Concatenation, not templating: instruction first, data last, one
        # blank line between them. This is wire format, asserted byte for byte.
        assert calls[0][1]["content"] == "Extract the PO number.\n\nInvoice 4711, PO P00018"

    async def test_a_task_prompt_can_be_the_whole_user_message(
        self, session: AsyncSession, scope: Scope
    ) -> None:
        run_id = await self._run_with_slots(
            scope,
            session,
            system_text=None,
            task_text="Summarise yesterday's tickets.",
            content=None,
        )
        stream, calls = scripted(_result())

        await execute_run(run_id, lambda _event: None, stream=stream)

        assert [message["role"] for message in calls[0]] == ["user"]
        # No trailing separator: a blank `content` is absent, not empty.
        assert calls[0][0]["content"] == "Summarise yesterday's tickets."

    async def test_a_blank_system_prompt_sends_no_system_message_at_all(
        self, session: AsyncSession, scope: Scope
    ) -> None:
        # Several providers treat an empty system role as a real turn that
        # behaves differently from having none, so it is omitted entirely.
        run_id = await self._run_with_slots(
            scope, session, system_text="   \n ", task_text=None, content="What is 2+2?"
        )
        stream, calls = scripted(_result())

        await execute_run(run_id, lambda _event: None, stream=stream)

        assert [message["role"] for message in calls[0]] == ["user"]

    async def test_a_row_left_with_no_user_message_errors_instead_of_dispatching(
        self, session: AsyncSession, scope: Scope
    ) -> None:
        """The third and last place the shared guard runs.

        A prompt deleted *after* the run was created `SET NULL`s nothing on the
        frozen row — but a row can still reach here empty when the case had no
        content and its task prompt was deleted between creation and execution
        of a *resumed* run. Blanking the frozen column directly is the smallest
        way to reach that state, and what matters is the outcome: the row is
        marked `error` with a readable message, and the model is never called.
        """
        run_id = await self._run_with_slots(
            scope, session, system_text=None, task_text="Summarise.", content=None
        )
        await session.execute(
            text("UPDATE run_results SET task_prompt_text = NULL WHERE run_id = :run_id"),
            {"run_id": run_id},
        )
        await session.commit()

        stream, calls = scripted(_result())
        await execute_run(run_id, lambda _event: None, stream=stream)

        assert calls == []
        results = await reload_results(scope, session, run_id)
        assert [r.status for r in results] == ["error"]
        assert "no user message" in (results[0].error or "")


class TestFailures:
    async def test_a_row_error_does_not_stop_the_run(
        self, session: AsyncSession, scope: Scope
    ) -> None:
        run_id = await make_run(scope, session, titles=("First", "Second"))
        stream, _ = scripted(LlmError("HTTP 400 — bad request", "http", 400), _result("Two."))

        events: list[RunEvent] = []
        await execute_run(run_id, events.append, stream=stream)

        results = await reload_results(scope, session, run_id)
        assert [r.status for r in results] == ["error", "ok"]
        assert results[0].error == "HTTP 400 — bad request"
        assert results[0].duration_ms is not None

        session.expire_all()
        run = await get_run(scope, session, run_id)
        assert run is not None
        # A model that merely errored on one row is still a completed run.
        assert run.status == "completed"
        assert [type(e).__name__ for e in events].count("ResultError") == 1

    async def test_a_run_whose_every_attempt_was_unreachable_fails(
        self, session: AsyncSession, scope: Scope
    ) -> None:
        run_id = await make_run(scope, session, titles=("First", "Second"))
        stream, _ = scripted(
            LlmError("Connection refused.", "connection"),
            LlmError("Request timed out.", "timeout"),
        )

        await execute_run(run_id, lambda _event: None, stream=stream)

        session.expire_all()
        run = await get_run(scope, session, run_id)
        assert run is not None
        assert run.status == "failed"

    async def test_a_mixed_run_is_not_failed(
        self, session: AsyncSession, scope: Scope
    ) -> None:
        run_id = await make_run(scope, session, titles=("First", "Second"))
        stream, _ = scripted(LlmError("Connection refused.", "connection"), _result("Two."))

        await execute_run(run_id, lambda _event: None, stream=stream)

        session.expire_all()
        run = await get_run(scope, session, run_id)
        assert run is not None
        assert run.status == "completed"


class TestInterruption:
    async def test_an_aborted_row_goes_back_to_pending_and_resume_finishes_it(
        self, session: AsyncSession, scope: Scope
    ) -> None:
        run_id = await make_run(scope, session, titles=("First", "Second"))
        cancelled = asyncio.Event()
        entered = asyncio.Event()

        async def blocking_stream(*args: Any, **kwargs: Any) -> LlmResult:
            del args, kwargs
            entered.set()
            await asyncio.sleep(30)
            raise AssertionError("should have been cancelled")

        events: list[RunEvent] = []
        task = asyncio.ensure_future(
            execute_run(run_id, events.append, cancelled, stream=blocking_stream)
        )
        await asyncio.wait_for(entered.wait(), timeout=5)
        cancelled.set()
        await asyncio.wait_for(task, timeout=5)

        results = await reload_results(scope, session, run_id)
        # Nothing half-written survives, and both rows remain runnable.
        assert [r.status for r in results] == ["pending", "pending"]
        assert results[0].started_at is None

        session.expire_all()
        run = await get_run(scope, session, run_id)
        assert run is not None
        assert run.status == "pending"
        assert [type(e).__name__ for e in events].count("Aborted") == 1

        # Resume: the same call finishes what is left.
        stream, calls = scripted(_result("One."), _result("Two."))
        await execute_run(run_id, lambda _event: None, stream=stream)
        results = await reload_results(scope, session, run_id)
        assert [r.status for r in results] == ["ok", "ok"]
        assert len(calls) == 2

    async def test_rows_left_running_by_a_crash_are_reclaimed(
        self, session: AsyncSession, scope: Scope
    ) -> None:
        run_id = await make_run(scope, session)
        results = await list_run_results(scope, session, run_id)
        results[0].status = "running"
        results[0].response_text = "half written"
        await session.commit()

        stream, _ = scripted(_result("Done."))
        await execute_run(run_id, lambda _event: None, stream=stream)

        reloaded = await reload_results(scope, session, run_id)
        assert reloaded[0].status == "ok"
        assert reloaded[0].response_text == "Done."


class TestLocking:
    async def test_a_second_execution_is_refused(
        self, session: AsyncSession, scope: Scope
    ) -> None:
        run_id = await make_run(scope, session)
        lock = await acquire_run_lock(run_id)
        assert lock is not None
        try:
            stream, _ = scripted(_result())
            with pytest.raises(RunAlreadyExecutingError):
                await execute_run(run_id, lambda _event: None, stream=stream)
        finally:
            await lock.release()


class TestToolRuns:
    async def test_a_manual_tool_run_persists_its_transcript(
        self, session: AsyncSession, scope: Scope
    ) -> None:
        toolset = await create_toolset(scope, session, name="Desk", kind="manual")
        await create_tool(
            scope,
            session,
            toolset.id,
            name="lookup_order",
            description="Looks an order up.",
            parameters_json='{"type":"object","properties":{}}',
            mock_response='{"status":"shipped"}',
        )
        await session.commit()

        run_id = await make_run(
            scope, session, tool_mode="execute", toolset_id=toolset.id
        )
        stream, calls = scripted(
            _result("", tool_calls=[ToolCall(id="c1", name="lookup_order", arguments="{}")]),
            _result("It shipped."),
        )

        events: list[RunEvent] = []
        await execute_run(run_id, events.append, stream=stream)

        results = await reload_results(scope, session, run_id)
        assert results[0].status == "ok"
        assert results[0].response_text == "It shipped."
        assert results[0].turn_count == 2
        assert results[0].tool_call_count == 1
        assert results[0].stopped_reason == "stop"
        assert results[0].transcript_json is not None
        # The canned response really was fed back to the model.
        assert calls[1][-1]["content"] == '{"status":"shipped"}'

        emitted = [type(event).__name__ for event in events]
        assert "TurnStart" in emitted
        assert "ToolCallEvent" in emitted
        assert "ToolResultEvent" in emitted
