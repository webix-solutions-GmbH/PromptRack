"""Mock responses by call order: `tools.mock_response` becomes `mock_responses`.

A manual tool used to answer every call with the same canned text. Real agents
page, poll and retry — "fetch page 1, then page 2", "status is pending, then
done" — and a single canned answer cannot express any of it, so a manual tool
now holds a **JSON array of strings** answered by call order: the first call
gets the first response, and the last one repeats for every call after it.

One column, one source of truth: the old `mock_response` is copied into a
one-element array and dropped, rather than kept beside the new column where the
two could disagree about what the tool says. NULL stays "none configured" (the
app never writes `[]`).

`json_build_array(text)::text` is what makes the copy exact: Postgres does the
JSON string escaping, so a canned response full of quotes and newlines lands as
the one-element array the app's own `json.loads` reads back byte-for-byte.

The downgrade restores the **first** element, which is lossless for every tool
that only ever had one response and the only defensible reading for one that
had several — it is what the first call would have received.

Hand-written: autogenerate would emit a drop and an add with nothing copied.

Revision ID: 0010_mock_responses
Revises: 0009_param_groups
Create Date: 2026-11-19

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_mock_responses"
down_revision: str | None = "0009_param_groups"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tools", sa.Column("mock_responses", sa.Text(), nullable=True))
    op.execute(
        "UPDATE tools SET mock_responses = json_build_array(mock_response)::text "
        "WHERE mock_response IS NOT NULL"
    )
    op.drop_column("tools", "mock_response")


def downgrade() -> None:
    op.add_column("tools", sa.Column("mock_response", sa.Text(), nullable=True))
    op.execute(
        "UPDATE tools SET mock_response = (mock_responses::json ->> 0) "
        "WHERE mock_responses IS NOT NULL"
    )
    op.drop_column("tools", "mock_responses")
