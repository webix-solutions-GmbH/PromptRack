"""`test_groups`, `test_cases`, `test_case_toolsets` — the regression suite.

A test case holds no prompt text of its own. It is the data that varies
(`content`) plus its rubric (`expected_output`) plus the tool configuration to
run it with, and it *references* up to two prompt assets by slot: a `system`
prompt and a `task` prompt. The prompts are not duplicated here, so committing
a new version changes what every test case sends next run.
"""

from datetime import datetime

from sqlalchemy import ForeignKey, Index, PrimaryKeyConstraint, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.toolsets import ToolChoice, ToolMode


class TestGroup(Base):
    """A named group of test cases — the suite's unit of selection."""

    __tablename__ = "test_groups"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id", ondelete="RESTRICT"))
    name: Mapped[str]
    description: Mapped[str | None]
    sort_order: Mapped[int] = mapped_column(server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    __table_args__ = (Index("test_groups_customer_name_idx", "customer_id", "name"),)


class TestCase(Base):
    """One test case (the old `prompts` table).

    Scope is inherited through `group_id`; there is no `customer_id` here. The
    cross-root references — the group, the two prompt slots, and the toolsets
    linked below — are the ones only app code can check. The group and the
    toolsets go through `assert_same_customer`; the two prompt slots go through
    `assert_prompt_slot` (`app.repos.prompts`), which checks same-workspace
    **and** kind in one read. Both are called inside the repository functions,
    so no call site can forget them.
    """

    __tablename__ = "test_cases"

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(
        ForeignKey("test_groups.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str]
    #: The *data half* of the user message — what varies from case to case. The
    #: task prompt (below) supplies the instruction half, and the two are
    #: concatenated at execution time. Nullable, because "this prompt takes no
    #: input" is expressible; the guard is that at least one of the two must
    #: resolve to non-blank text.
    content: Mapped[str | None]
    #: The rubric. Never sent to the model — it exists to rate the answer,
    #: which is what lets it state an injection payload or a canary outright.
    expected_output: Mapped[str | None]
    #: The `kind="system"` prompt this case runs against, sent as the system
    #: message. `SET NULL` on delete: the case survives, and past runs keep
    #: their own snapshots.
    system_prompt_id: Mapped[int | None] = mapped_column(
        ForeignKey("prompts.id", ondelete="SET NULL"), index=True
    )
    #: The `kind="task"` prompt, sent at the head of the user message. Indexed
    #: for the same two reads as the system slot: the kind-change refusal and
    #: the "used by N test cases" count both scan by prompt id.
    task_prompt_id: Mapped[int | None] = mapped_column(
        ForeignKey("prompts.id", ondelete="SET NULL"), index=True
    )
    tool_mode: Mapped[ToolMode] = mapped_column(Text, server_default="none")
    tool_choice: Mapped[ToolChoice | None] = mapped_column(Text)
    max_turns: Mapped[int] = mapped_column(server_default=text("6"))
    sort_order: Mapped[int] = mapped_column(server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class TestCaseToolset(Base):
    """Which toolsets a test case pulls in — any number, so one case can
    combine e.g. an ERP and a websearch server. Duplicate tool names across the
    selected toolsets are refused at authoring time and again at run creation.
    """

    __tablename__ = "test_case_toolsets"

    test_case_id: Mapped[int] = mapped_column(ForeignKey("test_cases.id", ondelete="CASCADE"))
    toolset_id: Mapped[int] = mapped_column(ForeignKey("toolsets.id", ondelete="CASCADE"))
    sort_order: Mapped[int] = mapped_column(server_default=text("0"))

    __table_args__ = (PrimaryKeyConstraint("test_case_id", "toolset_id"),)
