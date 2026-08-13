"""`test_groups`, `test_cases`, `test_case_toolsets` — the regression suite.

A test case is one input plus its rubric (`expected_output`) plus the tool
configuration to run it with. It *references* a prompt (the versioned asset)
and either appends to it or overrides it — the prompt itself is not duplicated
here, so committing a new version changes what every test case sends next run.
"""

from datetime import datetime
from typing import Literal

from sqlalchemy import ForeignKey, Index, PrimaryKeyConstraint, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.toolsets import ToolChoice, ToolMode

#: How `custom_text` combines with the referenced prompt: `append` sends
#: prompt + "\n\n" + custom, `override` sends the custom text alone. An
#: empty/whitespace result means no system message at all.
PromptMode = Literal["append", "override"]


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
    cross-root references (`prompt_id`, and the toolsets linked below) are the
    ones only app code can check — `assert_same_customer`, called inside the
    repository functions so no call site can forget it.
    """

    __tablename__ = "test_cases"

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(
        ForeignKey("test_groups.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str]
    #: The user message sent to the model.
    content: Mapped[str]
    #: The rubric. Never sent to the model — it exists to rate the answer,
    #: which is what lets it state an injection payload or a canary outright.
    expected_output: Mapped[str | None]
    #: The prompt asset this case runs against. `SET NULL` on delete: the case
    #: survives with its custom text, and past runs keep their snapshots.
    prompt_id: Mapped[int | None] = mapped_column(ForeignKey("prompts.id", ondelete="SET NULL"))
    mode: Mapped[PromptMode] = mapped_column(Text, server_default="append")
    custom_text: Mapped[str | None]
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
