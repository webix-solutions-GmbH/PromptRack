"""Parameter groups: named, reusable request-param presets.

Testing one model with reasoning on and off meant retyping the params into
every run, or abusing the endpoint's `default_params` and deleting them again.
A **param group** is a workspace-scoped named preset — "no thinking" holding
vLLM's `chat_template_kwargs`, "temp 0" — selected at run creation and merged
between the endpoint's defaults and the run's own overrides. It lives one level
*above* endpoints and models on purpose: the same preset is reusable against
any box and any model, so it is a sixth root table carrying `customer_id`, not
a child of `endpoints`.

Deliberately not shareable (no `is_global`): groups hold no credentials, so
like prompts and test groups they are the engagement's own material.

`runs.param_group_names` is a frozen JSON array of the selected groups' names,
`NULL` when none were selected — display-only provenance, the same pattern as
`runs.group_names`. There is deliberately **no FK** from runs to param_groups:
the merged params are already frozen into `runs.params`, so editing or deleting
a group must never change what a past run sent or displays, and a foreign key
would only create delete-time questions the snapshot model exists to avoid.

Revision ID: 0009_param_groups
Revises: 0008_reasoning_metrics
Create Date: 2026-08-31

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_param_groups"
down_revision: str | None = "0008_reasoning_metrics"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "param_groups",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("params", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["customers.id"],
            name=op.f("fk_param_groups_customer_id"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_param_groups")),
    )
    op.create_index(
        "param_groups_customer_name_idx",
        "param_groups",
        ["customer_id", "name"],
        unique=False,
    )
    op.add_column("runs", sa.Column("param_group_names", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("runs", "param_group_names")
    op.drop_index("param_groups_customer_name_idx", table_name="param_groups")
    op.drop_table("param_groups")
