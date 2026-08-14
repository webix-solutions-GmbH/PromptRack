"""Rename `machines` to `endpoints`, all the way down to the column names.

A "machine" has always *been* an OpenAI-compatible endpoint — an Ollama or vLLM
box you own, a proxy, or a hosted frontier API — and the schema was the last
place still using the narrower noun.

**Hand-written on purpose. Do not regenerate this with `alembic revision
--autogenerate`.** Autogenerate cannot infer a rename: it emits a drop plus a
create, which for `runs.machine_id` means every existing run silently loses the
endpoint it was measured against, and for the two tables means the rows are
gone outright. Unlike the baseline (which was rewritten in place while the
database was still disposable) this database holds imported production data, so
every step below is an `ALTER`, and nothing is dropped or recreated.

Renamed here beyond the tables and columns: the primary keys, foreign keys,
unique constraint, index and identity sequences. They all carry
`op.f()`-derived names from `app.models.base.NAMING_CONVENTION`, so leaving
them behind would give the new schema names that lie about it *and* make the
next `--autogenerate` want to churn them. The old names were read off the live
database rather than inferred, since one wrong constraint name fails the whole
migration.

Revision ID: 0002_endpoints_rename
Revises: 0001_baseline
Create Date: 2026-08-14

"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002_endpoints_rename"
down_revision: str | None = "0001_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Tables first: `ALTER TABLE ... RENAME CONSTRAINT` below names the table
    # by whatever it is called at that point.
    op.rename_table("machines", "endpoints")
    op.rename_table("machine_models", "endpoint_models")

    op.alter_column("endpoint_models", "machine_id", new_column_name="endpoint_id")
    op.alter_column("runs", "machine_id", new_column_name="endpoint_id")
    # The frozen `{name, base_url, cpu, ram, gpu}` JSON a run displays. Only the
    # column is renamed — none of the keys *inside* the document say "machine",
    # so there is no data rewrite to do.
    op.alter_column("runs", "machine_snapshot", new_column_name="endpoint_snapshot")

    op.execute("ALTER TABLE endpoints RENAME CONSTRAINT pk_machines TO pk_endpoints")
    op.execute(
        "ALTER TABLE endpoints "
        "RENAME CONSTRAINT fk_machines_customer_id TO fk_endpoints_customer_id"
    )
    op.execute("ALTER INDEX ix_machines_customer_id RENAME TO ix_endpoints_customer_id")

    op.execute(
        "ALTER TABLE endpoint_models RENAME CONSTRAINT pk_machine_models TO pk_endpoint_models"
    )
    op.execute(
        "ALTER TABLE endpoint_models "
        "RENAME CONSTRAINT fk_machine_models_machine_id TO fk_endpoint_models_endpoint_id"
    )
    op.execute(
        "ALTER TABLE endpoint_models "
        "RENAME CONSTRAINT uq_machine_models_machine_id_model_id "
        "TO uq_endpoint_models_endpoint_id_model_id"
    )

    op.execute("ALTER TABLE runs RENAME CONSTRAINT fk_runs_machine_id TO fk_runs_endpoint_id")

    # `serial` primary keys: the column default references the sequence by OID,
    # so renaming one is cosmetic and cannot break `nextval`.
    op.execute("ALTER SEQUENCE machines_id_seq RENAME TO endpoints_id_seq")
    op.execute("ALTER SEQUENCE machine_models_id_seq RENAME TO endpoint_models_id_seq")


def downgrade() -> None:
    # The exact inverse, in reverse order: constraints and sequences while the
    # tables still carry their new names, tables last.
    op.execute("ALTER SEQUENCE endpoint_models_id_seq RENAME TO machine_models_id_seq")
    op.execute("ALTER SEQUENCE endpoints_id_seq RENAME TO machines_id_seq")

    op.execute("ALTER TABLE runs RENAME CONSTRAINT fk_runs_endpoint_id TO fk_runs_machine_id")

    op.execute(
        "ALTER TABLE endpoint_models "
        "RENAME CONSTRAINT uq_endpoint_models_endpoint_id_model_id "
        "TO uq_machine_models_machine_id_model_id"
    )
    op.execute(
        "ALTER TABLE endpoint_models "
        "RENAME CONSTRAINT fk_endpoint_models_endpoint_id TO fk_machine_models_machine_id"
    )
    op.execute(
        "ALTER TABLE endpoint_models RENAME CONSTRAINT pk_endpoint_models TO pk_machine_models"
    )

    op.execute("ALTER INDEX ix_endpoints_customer_id RENAME TO ix_machines_customer_id")
    op.execute(
        "ALTER TABLE endpoints "
        "RENAME CONSTRAINT fk_endpoints_customer_id TO fk_machines_customer_id"
    )
    op.execute("ALTER TABLE endpoints RENAME CONSTRAINT pk_endpoints TO pk_machines")

    op.alter_column("runs", "endpoint_snapshot", new_column_name="machine_snapshot")
    op.alter_column("runs", "endpoint_id", new_column_name="machine_id")
    op.alter_column("endpoint_models", "endpoint_id", new_column_name="machine_id")

    op.rename_table("endpoint_models", "machine_models")
    op.rename_table("endpoints", "machines")
