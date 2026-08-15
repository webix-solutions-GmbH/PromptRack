"""The administrator surface: deactivation and single-use invites.

Two additions, both under `app/auth/` rather than in any workspace:

`users.disabled_at` is nullable with no default, so every existing account is
active and nothing changes until an admin deactivates one. Its presence *is*
the deactivation — there is no boolean beside it to drift from it — and it is
deliberately not called `deleted_at`, because deleting a user here is a real
`DELETE`.

`user_invites` is the way in now that sign-up closes forever after the first
account. It holds no email: an invite names a role, and whoever opens the link
first supplies their own address. The token is stored the way an `api_token`
is — SHA-256 of a 32-byte secret, plus a 12-character display prefix for
recognising a row — so what is at rest can recognise a link but never
reconstruct one. Both user FKs are `SET NULL` so deleting the admin who sent an
invite, or the account that redeemed one, keeps the audit row.

`downgrade()` drops both. That discards the invite history, which is the honest
outcome: there is nowhere else for it to live, and an install that goes back to
0003 has no surface that could read it.

Revision ID: 0004_users_admin_and_invites
Revises: 0003_base_workspace_and_globals
Create Date: 2026-08-15

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_users_admin_and_invites"
down_revision: str | None = "0003_base_workspace_and_globals"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        "user_invites",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("display_prefix", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("redeemed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("redeemed_by", sa.Integer(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_user_invites_created_by"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["redeemed_by"],
            ["users.id"],
            name=op.f("fk_user_invites_redeemed_by"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_invites")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_user_invites_token_hash")),
    )


def downgrade() -> None:
    op.drop_table("user_invites")
    op.drop_column("users", "disabled_at")
