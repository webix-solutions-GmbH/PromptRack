"""`customers` — the workspace root every scoped table hangs off."""

from datetime import datetime

from sqlalchemy import Index, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Customer(Base):
    """A customer workspace.

    Not a tenant: customers never log in, and every signed-in user can switch
    into any of them. It is the label that keeps one engagement's machines —
    i.e. base URLs with API keys — prompts and runs from mixing with another's.

    Deleting a workspace is guarded by `ON DELETE RESTRICT` on all five root
    tables rather than a cascade: a cascade would silently destroy run history.
    `archived_at` is the soft path.
    """

    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    description: Mapped[str | None]
    # Hidden from the workspace switcher without destroying anything it owns.
    archived_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        # Unique case-insensitively: MCP callers name a workspace and an
        # ambiguous name is refused rather than guessed, so two workspaces
        # differing only in case would make every by-name call fail.
        Index("customers_name_lower_idx", text("lower(name)"), unique=True),
    )
