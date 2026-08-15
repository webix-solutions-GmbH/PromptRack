"""Declarative base and the conventions every model in this package follows.

Three things are settled here so no model has to repeat them:

* **Constraint naming.** A `MetaData` naming convention gives every index,
  unique constraint and foreign key a deterministic name, so Alembic can drop
  or alter one by name in a later migration instead of guessing what Postgres
  auto-generated.
* **Python type -> column type.** `type_annotation_map` is what makes
  ``Mapped[str]`` a `TEXT` column, ``Mapped[datetime]`` a `TIMESTAMPTZ`
  (server code therefore always holds *aware* datetimes) and ``Mapped[float]``
  a `DOUBLE PRECISION` (`real` would be float4 and would silently round every
  `tokens_per_sec`).
* **No pg enums.** Enum-ish columns are `Text` annotated with a Python
  ``Literal``: adding a rating or status value stays a code change with no
  migration, and a value written by an older build can still be read back.

There are deliberately **no ORM relationships** in this package. Every read
goes through a scoped repository function that joins its parent explicitly (the
`Scope` pattern), and implicit lazy loads are an error under asyncio anyway.
"""

from datetime import datetime

from sqlalchemy import DateTime, Double, MetaData, Text
from sqlalchemy.orm import DeclarativeBase

# `%(column_0_N_name)s` names multi-column constraints after all of their
# columns, so a composite unique constraint reads as what it constrains.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base for every table in the app."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    type_annotation_map = {
        str: Text(),
        datetime: DateTime(timezone=True),
        float: Double(),
    }
