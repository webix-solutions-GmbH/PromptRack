"""An endpoint's platform, and the params every run against it starts from.

Two columns on `endpoints`, both about the same question: which knobs this box
takes, and which of them are already set for it.

`platform` is a **catalog key, not an adapter** — `generic`, `openai`, `ollama`,
`vllm`, `lmstudio`. Nothing about the request changes because of it; it selects
which parameter names, types and hints the editor suggests, and a user is free
to type past the suggestions. `Text` + a Python `Literal` per this schema's
rule, so a fifth platform needs no migration. `NOT NULL DEFAULT 'generic'`, so
every existing endpoint keeps behaving exactly as it did.

`default_params` is a JSON object stored as text (the same shape and storage
`runs.params` already uses), merged **under** a run's own params at run
creation. Nullable, meaning none. It is content rather than a credential, so
unlike `api_key` it round-trips through the API freely.

Neither column is read at execution time: run creation merges the defaults into
`runs.params` and freezes the result, which is the snapshot invariant applied to
parameters — editing an endpoint's defaults must never change what a past run
sent, or what resuming it would send.

Revision ID: 0006_endpoint_platform_params
Revises: 0005_rating_provenance
Create Date: 2026-08-15

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_endpoint_platform_params"
down_revision: str | None = "0005_rating_provenance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "endpoints",
        sa.Column("platform", sa.Text(), server_default="generic", nullable=False),
    )
    op.add_column("endpoints", sa.Column("default_params", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("endpoints", "default_params")
    op.drop_column("endpoints", "platform")
