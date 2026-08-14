"""`toolsets` and `tools` — what a test case can offer a model to call."""

from datetime import datetime
from typing import Literal

from sqlalchemy import ForeignKey, Index, Text, UniqueConstraint, false, func, true
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

#: `manual` tools are authored in the UI and answer with `mock_response`
#: verbatim; `mcp` tools are discovered from an MCP server and really executed.
ToolsetKind = Literal["manual", "mcp"]
ToolSource = Literal["manual", "mcp"]

#: How a test case treats tools. Shared with `run_results`, which snapshots it:
#: `none` is the classic one-shot, `definitions` offers the tools and only
#: records what the model wanted to call, `execute` runs the full loop.
ToolMode = Literal["none", "definitions", "execute"]

#: Null leaves `tool_choice` out of the request entirely.
ToolChoice = Literal["auto", "required", "none"]


class Toolset(Base):
    """A named bundle of tools. A test case may combine several.

    `mcp_url` and `mcp_headers` are credentials, which is why a toolset is
    admin-writable in the UI and not writable over MCP at all — and why they
    are read live at execution rather than frozen into a run.
    """

    __tablename__ = "toolsets"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id", ondelete="RESTRICT"))
    #: Readable from every workspace, writable only from the one that owns it,
    #: and settable only on a row owned by the Base workspace — see
    #: `Endpoint.is_global`, which this mirrors exactly. The same three mock
    #: toolsets are wanted in every engagement, and an `mcp_url` plus headers
    #: is the other credential worth registering once.
    #:
    #: Deleting a global toolset is guarded (`app.repos.toolsets`): the
    #: `test_case_toolsets` FK cascades, so an ungated delete would silently
    #: strip the toolset from every engagement's test cases.
    is_global: Mapped[bool] = mapped_column(server_default=false())
    name: Mapped[str]
    description: Mapped[str | None]
    kind: Mapped[ToolsetKind] = mapped_column(Text, server_default="manual")
    mcp_url: Mapped[str | None]
    #: JSON object of extra request headers (auth), sent with every MCP call.
    mcp_headers: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())

    __table_args__ = (Index("toolsets_customer_name_idx", "customer_id", "name"),)


class Tool(Base):
    """One callable function.

    Like `endpoint_models`, MCP-discovered rows are upserted and **never
    deleted** — a tool that disappears from `tools/list` only flips `enabled`
    false, so a past run can still explain what it sent.

    Scope is inherited through `toolset_id`; there is no `customer_id` here.
    """

    __tablename__ = "tools"

    id: Mapped[int] = mapped_column(primary_key=True)
    toolset_id: Mapped[int] = mapped_column(ForeignKey("toolsets.id", ondelete="CASCADE"))
    name: Mapped[str]
    description: Mapped[str | None]
    #: JSON Schema for the function's arguments.
    parameters_json: Mapped[str] = mapped_column(server_default="{}")
    #: Canned output returned instead of calling anything (manual toolsets).
    #: This is what keeps a multi-turn test byte-identical across models.
    mock_response: Mapped[str | None]
    enabled: Mapped[bool] = mapped_column(server_default=true())
    source: Mapped[ToolSource] = mapped_column(Text, server_default="manual")
    first_seen_at: Mapped[datetime] = mapped_column(server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(server_default=func.now())

    __table_args__ = (UniqueConstraint("toolset_id", "name"),)
