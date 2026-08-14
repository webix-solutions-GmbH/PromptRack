"""`runs` and `run_results` — one execution of a suite against one model.

`run_results` is where the **snapshot invariant** lives: editing or deleting a
prompt, test case, endpoint or toolset must never change how a past run
displays, so run creation freezes the **three texts** (`system_prompt_text`,
`task_prompt_text`, `test_case_text`), the **two version ids** attributing them
(`system_prompt_version_id`, `task_prompt_version_id`) and the tool
configuration into these rows. The texts stay separate rather than
pre-assembled: it is what lets `/results` say *the task prompt changed* instead
of *the user message changed*. Assembly happens at execution time
(`app.services.message_assembly`).

The FKs back to the live rows are kept (all `SET NULL`) only for cross-run
comparison and version attribution; rendering always uses the snapshot columns.
"""

from datetime import datetime
from typing import Literal

from sqlalchemy import ForeignKey, Index, Text, false, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.toolsets import ToolChoice, ToolMode

#: Execution state machine of a whole run. `failed` is reserved for "every
#: attempted result died at connection level"; partial errors still end
#: `completed`.
RunStatus = Literal["pending", "running", "completed", "failed"]

#: Execution state of one row. `ok` means "completed without error" — which is
#: exactly why the middle rating is `meh` and not `ok`.
ResultStatus = Literal["pending", "running", "ok", "error"]

#: Why the tool loop stopped.
StoppedReason = Literal["stop", "max_turns", "definitions_only"]

#: Manual verdict. `meh` = "not wrong, but not good enough" — usually a signal
#: the test case needs work rather than the model.
Rating = Literal["good", "meh", "bad"]


class Run(Base):
    """One suite executed against one model on one endpoint."""

    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.id", ondelete="RESTRICT"), index=True
    )
    #: Kept for cross-run comparison; `endpoint_snapshot` is what renders.
    endpoint_id: Mapped[int | None] = mapped_column(ForeignKey("endpoints.id", ondelete="SET NULL"))
    #: JSON snapshot of the endpoint at creation time: name, base URL, and the
    #: hardware notes if it is a box you own rather than a hosted API.
    endpoint_snapshot: Mapped[str]
    model_id: Mapped[str]
    #: JSON of the request parameters (temperature, …).
    params: Mapped[str | None]
    comment: Mapped[str | None]
    #: JSON array of the group names this run covered.
    group_names: Mapped[str]
    #: JSON snapshot of endpoint/server/model metadata probed at creation time.
    llm_info: Mapped[str | None]
    status: Mapped[RunStatus] = mapped_column(Text, server_default="pending")
    # Deliberately *not* a `status` value: status is the execution state
    # machine Resume depends on, so an archived run with pending rows has to
    # stay `pending` and can be unarchived and finished.
    archived_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]


class RunResult(Base):
    """One test case's result inside a run — and its frozen inputs.

    Scope is inherited through `run_id`; there is no `customer_id` here.
    """

    __tablename__ = "run_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    #: Primary match key when comparing runs; the normalized-text fallback in
    #: the compare matrix exists for rows whose test case was deleted.
    test_case_id: Mapped[int | None] = mapped_column(
        ForeignKey("test_cases.id", ondelete="SET NULL")
    )
    # Attribution, not selection: a run always tests the current draft, and
    # these are set only when that draft was byte-equal to a committed version
    # (a clean working tree). Null = the run tested a dirty draft, or that slot
    # held no prompt. One per slot, because the two drafts are independent —
    # a dirty system prompt must not cost the task prompt its attribution.
    system_prompt_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("prompt_versions.id", ondelete="SET NULL")
    )
    task_prompt_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("prompt_versions.id", ondelete="SET NULL")
    )
    sort_order: Mapped[int] = mapped_column(server_default=text("0"))

    # --- frozen inputs -----------------------------------------------------
    group_name: Mapped[str]
    test_case_title: Mapped[str]
    #: The test case's own `content` — the data half of the user message, and
    #: nothing else. Nullable, because a case whose task prompt is the whole
    #: user message has no data of its own.
    test_case_text: Mapped[str | None]
    expected_output: Mapped[str | None]
    #: The system prompt's draft text, verbatim, exactly as it will be sent.
    #: Null = no system prompt. Not derived from anything, which is what makes
    #: `system_prompt_version_id` above able to name the version it really is.
    system_prompt_text: Mapped[str | None]
    #: The task prompt's draft text, verbatim. Prepended to `test_case_text`
    #: (blank line between) to make the user message.
    task_prompt_text: Mapped[str | None]
    #: The exact JSON array of tool definitions sent to the model. Editing or
    #: deleting a toolset afterwards can never rewrite what a past run asked
    #: for.
    tools_snapshot: Mapped[str | None]
    tool_mode: Mapped[ToolMode] = mapped_column(Text, server_default="none")
    tool_choice: Mapped[ToolChoice | None] = mapped_column(Text)
    max_turns: Mapped[int] = mapped_column(server_default=text("6"))

    # --- outcome -----------------------------------------------------------
    status: Mapped[ResultStatus] = mapped_column(Text, server_default="pending")
    #: Final assistant text — same meaning whether tools were used or not.
    response_text: Mapped[str | None]
    #: Full message array of a tool run (assistant, tool_calls, tool results).
    transcript_json: Mapped[str | None]
    #: Per-turn metrics array; the columns below are its aggregates.
    turns_json: Mapped[str | None]
    turn_count: Mapped[int | None]
    tool_call_count: Mapped[int | None]
    stopped_reason: Mapped[StoppedReason | None] = mapped_column(Text)
    error: Mapped[str | None]

    # --- metrics -----------------------------------------------------------
    #: Sums over model turns only; tool wait time lives per call in the
    #: transcript and is excluded here.
    duration_ms: Mapped[int | None]
    #: First turn's time to first token (content delta or tool-call fragment).
    ttft_ms: Mapped[int | None]
    prompt_tokens: Mapped[int | None]
    completion_tokens: Mapped[int | None]
    #: Rate over the generation window, not total duration. `Double` on
    #: purpose: float4 would silently round.
    tokens_per_sec: Mapped[float | None]
    #: True when the provider sent no usage and the counts were estimated.
    tokens_estimated: Mapped[bool] = mapped_column(server_default=false())

    # --- manual verdict ----------------------------------------------------
    rating: Mapped[Rating | None] = mapped_column(Text)
    rating_note: Mapped[str | None]

    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]

    __table_args__ = (
        Index("run_results_run_id_idx", "run_id"),
        Index("run_results_test_case_id_idx", "test_case_id"),
        # Both slots are indexed: `set_baseline`'s attribution check is an OR
        # across the two columns, so it scans either.
        Index("run_results_system_prompt_version_id_idx", "system_prompt_version_id"),
        Index("run_results_task_prompt_version_id_idx", "task_prompt_version_id"),
    )
