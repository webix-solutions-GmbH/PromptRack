"""`toolsets`, `tools` and `documents` — what a test case can offer a model.

A toolset is the bundle; the two child tables are what fills it. `tools` is the
callable surface the model sees in every kind of toolset, and `documents` is the
corpus behind a `documents` toolset's three synthesized tools.
"""

from datetime import datetime
from typing import Literal

from sqlalchemy import Computed, ForeignKey, Index, Text, UniqueConstraint, false, func, true
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

#: `manual` tools are authored in the UI and answer with `mock_response`
#: verbatim; `mcp` tools are discovered from an MCP server and really executed;
#: a `documents` toolset holds a markdown corpus and its three retrieval tools
#: are **synthesized** — neither authored nor discovered — and executed against
#: the `documents` rows of that same toolset.
ToolsetKind = Literal["manual", "mcp", "documents"]
ToolSource = Literal["manual", "mcp", "documents"]

#: The text-search configuration behind `documents.content_tsv`, and therefore
#: the one every `websearch_to_tsquery` / `ts_headline` call against it must
#: name too — a query parsed with a different configuration than the vector was
#: built with silently stops matching. `simple` on purpose: see `Document`.
DOCUMENT_SEARCH_CONFIG = "simple"

#: `documents.content_tsv`'s generating expression. A generated column requires
#: an **IMMUTABLE** expression, which the one-argument `to_tsvector(text)` is
#: not — it reads `default_text_search_config` at call time — so the two-argument
#: form naming the configuration explicitly is not a style choice, it is the
#: only form Postgres accepts here.
DOCUMENT_TSV_EXPRESSION = (
    f"to_tsvector('{DOCUMENT_SEARCH_CONFIG}', coalesce(title, '') || ' ' || coalesce(content, ''))"
)

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


class Document(Base):
    """One markdown document in a `documents` toolset's corpus.

    The corpus is what makes "the agent answers from the customer's own
    documentation" a measurable workload rather than a claim: the model gets
    `list_documents`, `search_documents` and `read_document` as real `tools`
    rows, and what it can reach through them is exactly the `documents` rows of
    the toolset those tools belong to. What is being measured is **retrieval
    behavior** — does the model search well, open the right document, answer from
    the corpus instead of from memory, recover from a path that does not exist.

    A second child table of `toolsets`, sitting beside `tools`, and it carries
    **no `customer_id`** for the same reason `tools` and `endpoint_models` carry
    none: scope is inherited through `toolset_id`, expressed once in
    `app.repos.scoped.scope_through_parent` (a join on a read, an
    `IN (SELECT ...)` on an `UPDATE`/`DELETE`). Adding a `customer_id` here would
    create a second, independently-writable answer to "whose corpus is this",
    and the two could then disagree — and it would break sharing outright, since
    a global toolset borrowed by another engagement must bring its documents
    along, which is precisely what `visible=True` on the parent lookup does for
    free. That inheritance is also why `app.scope`'s `_SHAREABLE` map needs no
    entry for this table: only root tables appear there.

    It is also why the model's `path` argument cannot escape the corpus. The
    lookup is `toolset_id` (from the frozen `tools_snapshot`) plus the run's own
    `Scope`, i.e. a `WHERE` clause rather than a sanitizer, and nothing here ever
    touches a filesystem — so there is no traversal surface to defend, and a
    `path` that matches nothing is an ordinary "not found" tool result fed back
    to the model, never a failed row.

    `content_tsv` is a **STORED generated column** over title and content, and
    its configuration is `simple`, not `english`: this consultancy's customer
    documentation is frequently German, and English stemming applied to German
    text degrades retrieval in a way that would read as a *model* failure in
    `/results` — the one misattribution this whole app exists to prevent.
    `simple` stems nothing and folds case, which is the honest default across
    mixed-language corpora. Every query against the column must name the same
    configuration (`DOCUMENT_SEARCH_CONFIG`).
    """

    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    toolset_id: Mapped[int] = mapped_column(ForeignKey("toolsets.id", ondelete="CASCADE"))
    title: Mapped[str]
    #: The `read_document` key — a relative, slash-separated label such as
    #: `guides/refunds.md`. It is an identifier the model quotes back, not a
    #: filesystem location: nothing resolves it against a disk.
    path: Mapped[str]
    #: The markdown verbatim, exactly as uploaded or pasted. Retrieval windows
    #: it on read; it is never chunked or rewritten at rest.
    content: Mapped[str]
    #: Generated, never written by the app. `coalesce` in the expression is
    #: deliberate belt-and-braces: both inputs are `NOT NULL` today, and a
    #: single `NULL` would otherwise make the whole vector `NULL` and the
    #: document silently unfindable.
    content_tsv: Mapped[str] = mapped_column(
        TSVECTOR, Computed(DOCUMENT_TSV_EXPRESSION, persisted=True)
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("toolset_id", "path"),
        Index("documents_content_tsv_idx", "content_tsv", postgresql_using="gin"),
    )
