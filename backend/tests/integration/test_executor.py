"""The executor against a real database, with a scripted model.

`app.services.executor.execute_run` takes its chat streamer as a parameter for
exactly this — the seam `tool_loop` already established — so everything below
exercises the real persistence, the real advisory lock and the real scoping
while never opening a socket.

What is worth testing here is not "did it call the model" but the four
recovery invariants the module docstring names: every row persisted the moment
it finishes, a row error never stopping the run, `failed` meaning "the endpoint
was never reachable", and an interrupted row going back to `pending`.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.repos.documents import create_document
from app.repos.endpoints import create_endpoint
from app.repos.prompts import create_prompt
from app.repos.runs import get_run, list_run_results, update_run_result
from app.repos.test_cases import create_test_case, create_test_group
from app.repos.toolsets import create_tool, create_toolset
from app.scope import Scope
from app.services.executor import RESET_TO_PENDING, RunAlreadyExecutingError, execute_run
from app.services.llm import LlmError, LlmResult, ToolCall
from app.services.run_create import create_run_record
from app.services.run_events import RunEvent
from app.services.run_lock import acquire_run_lock
from app.services.tool_loop import parse_tools_snapshot, parse_transcript

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
    merged = {**defaults, **overrides}
    # A model that does not think sees its first visible token the moment it
    # produces anything at all, so the two TTFTs coincide unless a test says so.
    merged.setdefault("ttft_content_ms", merged["ttft_ms"])
    return LlmResult(**merged)


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
    base_url: str = "http://endpoint.invalid/v1",
) -> int:
    """An endpoint, a group, one test case per title, and a run over them."""
    endpoint = await create_endpoint(scope, session, name="Box", base_url=base_url)
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
        endpoint_id=endpoint.id,
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


class TestReasoningModels:
    """A thinking model's own channel, stored rather than dropped."""

    async def test_the_thinking_and_its_metrics_land_in_their_own_columns(
        self, session: AsyncSession, scope: Scope
    ) -> None:
        run_id = await make_run(scope, session)
        stream, _ = scripted(
            _result(
                "42",
                reasoning_text="The question is about arithmetic.",
                reasoning_tokens=470,
                ttft_ms=130,
                ttft_content_ms=7296,
                duration_ms=7417,
                completion_tokens=479,
            )
        )

        await execute_run(run_id, lambda _event: None, stream=stream)

        [result] = await reload_results(scope, session, run_id)
        assert result.reasoning_text == "The question is about arithmetic."
        assert result.reasoning_tokens == 470
        assert result.response_text == "42"
        # `ttft_ms` is the prefill; the visible-token reading is its own column.
        assert result.ttft_ms == 130
        assert result.ttft_content_ms == 7296
        # 479 tokens over the 7.287s of generation, not the 121ms it was visible.
        assert result.tokens_per_sec == pytest.approx(479 / 7.287, abs=0.1)

    async def test_a_model_that_does_not_think_leaves_the_columns_null(
        self, session: AsyncSession, scope: Scope
    ) -> None:
        run_id = await make_run(scope, session)
        stream, _ = scripted(_result("Straight to it."))

        await execute_run(run_id, lambda _event: None, stream=stream)

        [result] = await reload_results(scope, session, run_id)
        assert result.reasoning_text is None
        assert result.reasoning_tokens is None
        assert result.ttft_content_ms == result.ttft_ms

    async def test_an_aborted_row_gives_up_its_thinking_along_with_its_answer(
        self, session: AsyncSession, scope: Scope
    ) -> None:
        """`RESET_TO_PENDING` has to clear every output column, not most of them."""
        run_id = await make_run(scope, session)
        stream, _ = scripted(_result("42", reasoning_text="Thinking."))
        await execute_run(run_id, lambda _event: None, stream=stream)

        [result] = await reload_results(scope, session, run_id)
        await update_run_result(scope, session, run_id, result.id, RESET_TO_PENDING)
        await session.commit()

        [reset] = await reload_results(scope, session, run_id)
        assert reset.status == "pending"
        assert reset.response_text is None
        assert reset.reasoning_text is None


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
        endpoint = await create_endpoint(scope, session, name="Box", base_url="http://x/v1")
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
            endpoint_id=endpoint.id,
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


#: Two documents in one corpus, in the mixed German/English a customer's
#: documentation actually arrives in. The target sentence sits under its own
#: heading and well away from the top of the file, so a `ts_headline` fragment
#: around it can only resolve to that heading — which is the citation a model is
#: supposed to be able to act on.
REFUNDS_MD = """# Rückgaberichtlinie

Diese Richtlinie beschreibt, wie Kunden Artikel zurückgeben können und welche
Fristen dabei gelten. Sie gilt für alle Bestellungen im Onlineshop.

## Rückgabe innerhalb von 30 Tagen

Kunden können Artikel innerhalb von 30 Tagen ohne Angabe von Gründen
zurückgeben. Die Rücksendung ist für den Kunden kostenlos, sofern das
beigelegte Etikett verwendet wird.

## Refunds after 30 days

A refund requested more than thirty days after delivery needs warehouse
approval before the finance team may issue it. Ask the warehouse lead first and
record the ticket number in the order notes.
"""

SHIPPING_MD = """# Versand

Standardversand dauert zwei bis drei Werktage.

## Express

Express shipments leave the same day when the order arrives before 14:00.
"""


class TestDocumentRuns:
    """A retrieval workload end to end: search, read, answer.

    This is the whole point of the `documents` toolset kind, and it is here
    rather than in the pure suite because every part of it that could go wrong
    needs Postgres: the `simple`-configuration FTS match and its ranking, the
    `ts_headline` fragment the heading is resolved from, and the frozen
    `tools_snapshot` round trip that has to carry `source: "documents"` all the
    way to the executor's dispatcher.
    """

    async def _corpus_run(
        self,
        scope: Scope,
        session: AsyncSession,
        *,
        titles: Sequence[str] = ("Refund window",),
    ) -> tuple[int, int]:
        """A documents toolset with two documents, and a run over `titles`
        selecting it. The three retrieval tools are seeded by `create_toolset`.
        """
        toolset = await create_toolset(scope, session, name="Handbook", kind="documents")
        toolset_id = toolset.id
        await create_document(
            scope,
            session,
            toolset_id,
            title="Rückgaberichtlinie",
            path="guides/refunds.md",
            content=REFUNDS_MD,
        )
        await create_document(
            scope,
            session,
            toolset_id,
            title="Versand",
            path="guides/versand.md",
            content=SHIPPING_MD,
        )
        await session.commit()

        run_id = await make_run(
            scope, session, titles=titles, tool_mode="execute", toolset_id=toolset_id
        )
        return run_id, toolset_id

    async def test_the_model_searches_reads_and_answers_from_the_corpus(
        self, session: AsyncSession, scope: Scope
    ) -> None:
        run_id, toolset_id = await self._corpus_run(scope, session)
        stream, calls = scripted(
            _result(
                "",
                tool_calls=[
                    ToolCall(
                        id="c1",
                        name="search_documents",
                        arguments='{"query": "warehouse approval"}',
                    )
                ],
            ),
            _result(
                "",
                tool_calls=[
                    ToolCall(
                        id="c2",
                        name="read_document",
                        # `limit` as a *string*: models emit it often enough that
                        # refusing would measure their JSON habits, not retrieval.
                        arguments='{"path": "guides/refunds.md", "limit": "120"}',
                    )
                ],
            ),
            _result("A refund past thirty days needs warehouse approval."),
        )

        events: list[RunEvent] = []
        await execute_run(run_id, events.append, stream=stream)

        results = await reload_results(scope, session, run_id)
        assert results[0].status == "ok"
        assert results[0].error is None
        assert results[0].response_text == "A refund past thirty days needs warehouse approval."
        assert results[0].turn_count == 3
        assert results[0].tool_call_count == 2
        assert results[0].stopped_reason == "stop"

        # The search really ran in Postgres: one document matched, it is the
        # right one, and the hit came back with the heading it sits under.
        search = json.loads(calls[1][-1]["content"])
        assert search["query"] == "warehouse approval"
        assert search["match_count"] == 1
        [match] = search["matches"]
        assert match["path"] == "guides/refunds.md"
        assert match["heading"] == "Refunds after 30 days"
        assert "**warehouse**" in match["snippet"]

        # And the read is a window of the live markdown, in characters, with the
        # offset to continue from.
        read = json.loads(calls[2][-1]["content"])
        assert read["path"] == "guides/refunds.md"
        assert read["offset"] == 0
        assert read["chars"] == 120
        assert read["total_chars"] == len(REFUNDS_MD)
        assert read["truncated"] is True
        assert read["next_offset"] == 120
        assert read["content"] == REFUNDS_MD[:120]

        transcript = parse_transcript(results[0].transcript_json)
        assert transcript is not None
        tool_messages = [message for message in transcript if message.role == "tool"]
        assert [message.name for message in tool_messages] == [
            "search_documents",
            "read_document",
        ]
        # Neither is an error: a retrieval that worked must not read as one.
        assert [message.tool_is_error for message in tool_messages] == [False, False]

        # The frozen snapshot carried the source the dispatcher routes on, plus
        # the two forward-compat corpus crumbs.
        snapshot = parse_tools_snapshot(results[0].tools_snapshot)
        assert sorted(tool.name for tool in snapshot) == [
            "list_documents",
            "read_document",
            "search_documents",
        ]
        assert {tool.source for tool in snapshot} == {"documents"}
        assert {tool.toolset_id for tool in snapshot} == {toolset_id}
        assert {tool.mock_response for tool in snapshot} == {None}
        assert {tool.document_count for tool in snapshot} == {2}
        assert all(tool.corpus_updated_at for tool in snapshot)

    async def test_a_bad_path_is_a_tool_result_and_never_a_failed_row(
        self, session: AsyncSession, scope: Scope
    ) -> None:
        """The app's standing rule, on the path that most invites breaking it.

        A `path` the model invents is one more value in a `WHERE` clause — there
        is no filesystem behind it, so `../../etc/passwd` is not a traversal, it
        is a miss. What matters is that the miss reaches the *model*, with the
        paths that do exist in it, and that the row still finishes `ok`: a model
        recovering from its own bad guess is exactly the behaviour this workload
        is here to measure.
        """
        run_id, _ = await self._corpus_run(scope, session)
        stream, calls = scripted(
            _result(
                "",
                tool_calls=[
                    ToolCall(
                        id="c1",
                        name="read_document",
                        arguments='{"path": "../../etc/passwd"}',
                    )
                ],
            ),
            _result("I could not find that file; the corpus has guides/refunds.md."),
        )

        await execute_run(run_id, lambda _event: None, stream=stream)

        results = await reload_results(scope, session, run_id)
        assert results[0].status == "ok"
        assert results[0].error is None
        assert results[0].turn_count == 2

        answer = json.loads(calls[1][-1]["content"])
        assert 'There is no document at "../../etc/passwd"' in answer["error"]
        assert "guides/refunds.md" in answer["error"]
        assert "guides/versand.md" in answer["error"]

        transcript = parse_transcript(results[0].transcript_json)
        assert transcript is not None
        [tool_message] = [message for message in transcript if message.role == "tool"]
        # Flagged as an error *inside* the transcript, which is what the run
        # detail view colours — while the row itself is a completed measurement.
        assert tool_message.tool_is_error is True

        session.expire_all()
        run = await get_run(scope, session, run_id)
        assert run is not None and run.status == "completed"

    async def test_an_argument_postgres_refuses_costs_one_row_not_the_run(
        self, session: AsyncSession, scope: Scope
    ) -> None:
        """The blast radius of a bad tool argument is one row. It has to be.

        The document tools are the only ones that query on the executor's *own*
        session, and a statement Postgres refuses leaves that session's
        transaction aborted — after which every later statement on it raises. The
        row's own `ok` write happens after the tool loop returns and therefore
        *outside* the per-row handler, so without the closure's rollback this
        sequence failed that write, took the remaining rows down with it and left
        the run stuck `running` — one argument the model chose costing the whole
        suite.

        A NUL byte is the reachable way in: no corpus row can hold one (every
        write door refuses it), so the call was always a miss — but asyncpg
        rejects it as a bind parameter rather than returning no rows. The second
        row is the assertion that matters; a single-row run would pass either way.
        """
        run_id, _ = await self._corpus_run(
            scope, session, titles=("Refund window", "Shipping window")
        )
        stream, calls = scripted(
            _result(
                "",
                tool_calls=[
                    ToolCall(
                        id="c1",
                        name="search_documents",
                        arguments='{"query": "R\\u0000ckgabe"}',
                    )
                ],
            ),
            _result("I could not search for that term."),
            _result("Shipping takes three days."),
        )

        await execute_run(run_id, lambda _event: None, stream=stream)

        results = await reload_results(scope, session, run_id)
        # The refused statement reached the model as an ordinary tool error...
        assert results[0].status == "ok"
        assert results[0].error is None
        answer = json.loads(calls[1][-1]["content"])
        assert "corpus could not be queried" in answer["error"]

        # ...and the *second* row still ran, which is the whole point.
        assert results[1].status == "ok", results[1].error
        assert results[1].response_text == "Shipping takes three days."

        session.expire_all()
        run = await get_run(scope, session, run_id)
        assert run is not None and run.status == "completed"

    async def test_a_search_that_matches_nothing_is_not_an_error(
        self, session: AsyncSession, scope: Scope
    ) -> None:
        """A miss is a normal answer with a next step in it.

        The words a customer's documentation uses are frequently not the words
        the question used, so an empty result set carries a note pointing at
        `list_documents` and is deliberately *not* flagged as an error: mislabelling
        it would report an ordinary retrieval miss as a malfunction in `/results`.
        """
        run_id, _ = await self._corpus_run(scope, session)
        stream, calls = scripted(
            _result(
                "",
                tool_calls=[
                    ToolCall(
                        id="c1",
                        name="search_documents",
                        arguments='{"query": "helicopter maintenance"}',
                    )
                ],
            ),
            _result(
                "",
                tool_calls=[ToolCall(id="c2", name="list_documents", arguments="{}")],
            ),
            _result("The handbook covers refunds and shipping only."),
        )

        await execute_run(run_id, lambda _event: None, stream=stream)

        results = await reload_results(scope, session, run_id)
        assert results[0].status == "ok"

        miss = json.loads(calls[1][-1]["content"])
        assert miss["match_count"] == 0
        assert miss["matches"] == []
        assert "list_documents" in miss["note"]

        listing = json.loads(calls[2][-1]["content"])
        assert listing["document_count"] == 2
        assert [document["path"] for document in listing["documents"]] == [
            "guides/refunds.md",
            "guides/versand.md",
        ]
        assert listing["documents"][0]["chars"] == len(REFUNDS_MD)

        transcript = parse_transcript(results[0].transcript_json)
        assert transcript is not None
        assert [
            message.tool_is_error for message in transcript if message.role == "tool"
        ] == [False, False]
