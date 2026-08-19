"""Reasoning models: keep the thinking, and stop letting it wreck the rate.

Three nullable columns on `run_results`, all from one discovery: a model with a
thinking phase was being measured wrong end to end.

* **`reasoning_text`** — the chain of thought when the endpoint streams it on
  `delta.reasoning_content` (vLLM with `--reasoning-parser`, DeepSeek) instead of
  inlining `<think>` tags. Nothing read that field, so the thinking was discarded
  and the answer kept the chat template's post-`</think>` newline pair at its
  head — enough to fail a rubric demanding raw JSON with no preamble.
* **`ttft_content_ms`** — time to the first *visible* token, which is what
  `ttft_ms` used to hold. A real latency for a thinking model; just not a
  throughput denominator.
* **`reasoning_tokens`** — part of `completion_tokens`, never additional to it.

`ttft_ms` keeps its column and changes meaning: first output of **any** kind. That
is the actual bug, and a code change rather than a schema one — with the old
reading the whole chain of thought counted as prefill, `duration - ttft` collapsed
to ~120ms while `completion_tokens` still counted every reasoning token, and the
stored rate came out at 3958 tok/s against a real ~65.

**Nothing is backfilled, deliberately.** Existing rows hold a `ttft_ms` measured
the old way, so their generation window is unrecoverable and `tokens_per_sec`
could only be guessed at — and overwriting a stored measurement with a number
nobody measured is what the snapshot model exists to prevent. The affected runs
get re-run instead; `compute_tokens_per_sec`'s plausibility guard keeps the next
unrecognised reasoning field writing `NULL` rather than fiction.

Revision ID: 0008_reasoning_metrics
Revises: 0007_documents
Create Date: 2026-08-19

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_reasoning_metrics"
down_revision: str | None = "0007_documents"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("run_results", sa.Column("reasoning_text", sa.Text(), nullable=True))
    op.add_column("run_results", sa.Column("ttft_content_ms", sa.Integer(), nullable=True))
    op.add_column("run_results", sa.Column("reasoning_tokens", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("run_results", "reasoning_tokens")
    op.drop_column("run_results", "ttft_content_ms")
    op.drop_column("run_results", "reasoning_text")
