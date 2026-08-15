"""Record which credential set a result's verdict.

`run_results.rated_via` holds `Actor.via`'s vocabulary — `session` for a human
clicking a rating in the UI, `token` for an agent judging over MCP — so a
verdict says who reached it rather than reading as an anonymous claim. Nullable
with no default, which is what makes every existing rating honest: they were
written before anything recorded provenance, and `NULL` says exactly that
instead of inventing a human for them. `Text` rather than an enum, the same
rule `rating` and `status` follow: another credential type would need no
migration.

Written only alongside the rating itself (see `app.repos.runs.rate_result`), so
a note-only edit leaves it untouched and clearing a rating clears it too.

Revision ID: 0005_rating_provenance
Revises: 0004_users_admin_and_invites
Create Date: 2026-08-15

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_rating_provenance"
down_revision: str | None = "0004_users_admin_and_invites"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("run_results", sa.Column("rated_via", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("run_results", "rated_via")
