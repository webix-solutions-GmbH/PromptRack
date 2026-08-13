"""`prompts` and `prompt_versions` — the versioned asset and its history.

This is the pivot's core: a prompt is the business logic Webix ships to a
customer, so it gets a mutable draft (`prompts.content`), immutable committed
versions, one `deployed` pointer per prompt and one `baseline` run pointer per
version.
"""

from datetime import datetime

from sqlalchemy import ForeignKey, Index, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Prompt(Base):
    """A versioned prompt asset (the old `system_prompts`).

    `content` **is the draft** — the editor writes it directly, and it is what
    a run always tests. Freezing it is an explicit commit, git-style.
    """

    __tablename__ = "prompts"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id", ondelete="RESTRICT"))
    name: Mapped[str]
    #: The mutable draft. `dirty` = this differs from the head version.
    content: Mapped[str]
    # The bookkeeping claim "this version is live at the customer", set by a
    # human in the UI (never over MCP). Must belong to *this* prompt — checked
    # in the repository layer, since Postgres cannot express it here.
    #
    # `use_alter` breaks the prompts <-> prompt_versions cycle: the constraint
    # is added after both tables exist instead of inline.
    deployed_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("prompt_versions.id", ondelete="SET NULL", use_alter=True)
    )
    deployed_at: Mapped[datetime | None]
    deployed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        # On `(customer_id, name)` rather than `customer_id` alone: the layer
        # looks these up by name inside a workspace, and a btree on the pair
        # already serves a customer-only predicate as its leftmost prefix.
        # Non-unique on purpose — app code produces a better message.
        Index("prompts_customer_name_idx", "customer_id", "name"),
    )


class PromptVersion(Base):
    """One immutable commit of a prompt's text.

    Never edited, never deleted individually: the whole history dies with the
    asset (`CASCADE`), and runs keep their own snapshots regardless. A commit
    whose content is byte-identical to the head version is refused ("no
    changes") in the repository layer.

    Scope is inherited through `prompt_id`; there is no `customer_id` here.
    """

    __tablename__ = "prompt_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    prompt_id: Mapped[int] = mapped_column(ForeignKey("prompts.id", ondelete="CASCADE"))
    #: Sequential per prompt, `max + 1` computed inside the commit transaction.
    #: The unique index below is the backstop against a concurrent commit.
    version: Mapped[int]
    content: Mapped[str]
    message: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    # The known-good run that justified deploying this version — the reference
    # point for a regression check after a model swap. `SET NULL` on run
    # delete: losing the run must not take the version with it.
    baseline_run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id", ondelete="SET NULL"))

    __table_args__ = (UniqueConstraint("prompt_id", "version"),)
