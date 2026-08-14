"""The Base workspace, and global endpoints and toolsets.

Three booleans and one row. `customers.is_base` marks the single workspace —
named "Base" — that may own shared infrastructure; `endpoints.is_global` and
`toolsets.is_global` mark the rows it shares with every other workspace. The
two shareable tables are exactly the two that hold credentials (a base URL plus
an API key, an MCP URL plus headers), which is not a coincidence: a consultancy
runs the same box and the same mock toolsets across every engagement, and
re-registering them per workspace duplicates a secret and guarantees half the
copies go stale.

All three are `NOT NULL DEFAULT false`, so every existing row is local and
nothing changes behavior until someone flips a flag in Base.

**Create or adopt, never blindly insert.** `customers_name_lower_idx` is unique
on `lower(name)`, so an insert would simply fail on an install that already has
a workspace called "Base" — and there is one: `backend/scripts/split_base_
workspace.py` has already run against the user's imported data and moved the
reusable baseline suite into a workspace of exactly that name. Its id is
whatever the data holds, never an assumed 1, and it is not empty: it owns
groups, cases, prompts and toolsets, which is why nothing here or in the app
may assume Base is infrastructure-only.

`downgrade()` drops the three columns and **leaves the workspace in place**. By
the time it runs Base may own rows, and a workspace delete is `RESTRICT`-guarded
precisely so that history is never destroyed by a schema step; an install that
downgrades keeps an ordinary workspace called "Base", which is exactly what it
was before this revision.

Revision ID: 0003_base_workspace_and_globals
Revises: 0002_endpoints_rename
Create Date: 2026-08-14

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_base_workspace_and_globals"
down_revision: str | None = "0002_endpoints_rename"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BASE_NAME = "Base"
BASE_DESCRIPTION = (
    "The shared workspace: endpoints and toolsets marked global here are usable "
    "from every other workspace, and editable only from this one."
)


def upgrade() -> None:
    op.add_column(
        "customers",
        sa.Column("is_base", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "endpoints",
        sa.Column("is_global", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "toolsets",
        sa.Column("is_global", sa.Boolean(), nullable=False, server_default=sa.false()),
    )

    connection = op.get_bind()
    # Matched case-insensitively, the same way `customers_name_lower_idx`
    # enforces uniqueness and the same way an MCP caller resolves a workspace by
    # name — "base" and "Base" are one workspace everywhere else in this app.
    existing = connection.execute(
        sa.text(
            "SELECT id FROM customers WHERE lower(name) = lower(:name) "
            "ORDER BY id ASC LIMIT 1"
        ),
        {"name": BASE_NAME},
    ).scalar()

    if existing is not None:
        # Adopt it, keeping its name, description and everything it owns. A user
        # who pre-made "Base" made it for this.
        connection.execute(
            sa.text("UPDATE customers SET is_base = true WHERE id = :id"),
            {"id": existing},
        )
        return

    connection.execute(
        sa.text(
            "INSERT INTO customers (name, description, is_base) "
            "VALUES (:name, :description, true)"
        ),
        {"name": BASE_NAME, "description": BASE_DESCRIPTION},
    )


def downgrade() -> None:
    op.drop_column("toolsets", "is_global")
    op.drop_column("endpoints", "is_global")
    op.drop_column("customers", "is_base")
