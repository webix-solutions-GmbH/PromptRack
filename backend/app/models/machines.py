"""`machines` and `machine_models` — an endpoint and every model seen on it."""

from datetime import datetime
from typing import Literal

from sqlalchemy import ForeignKey, Text, UniqueConstraint, false, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

#: How a model came to be recorded on a machine.
MachineModelSource = Literal["discovered", "manual", "run"]


class Machine(Base):
    """A machine IS an endpoint: a base URL, an optional API key, and free-text
    hardware notes.

    The hardware notes are half the product's answer — "which model is good
    enough" is worth little without "and what box does it need" — so every run
    names the machine that produced its numbers.

    `base_url` / `api_key` are credentials and are therefore read **live** at
    execution time rather than frozen into a run, so a moved endpoint does not
    break Resume.
    """

    __tablename__ = "machines"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.id", ondelete="RESTRICT"), index=True
    )
    name: Mapped[str]
    base_url: Mapped[str]
    api_key: Mapped[str | None]
    cpu: Mapped[str | None]
    ram: Mapped[str | None]
    gpu: Mapped[str | None]
    notes: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class MachineModel(Base):
    """Every model ever seen on a machine. Never deleted from.

    Discovery upserts and flips `currently_loaded` false for models absent from
    `/v1/models`; manual adds and run creation upsert too (`source`). Keeping
    the history is what lets a past run still name a model the endpoint no
    longer serves.

    Scope is inherited through `machine_id`; there is no `customer_id` here.
    """

    __tablename__ = "machine_models"

    id: Mapped[int] = mapped_column(primary_key=True)
    machine_id: Mapped[int] = mapped_column(ForeignKey("machines.id", ondelete="CASCADE"))
    model_id: Mapped[str]
    currently_loaded: Mapped[bool] = mapped_column(server_default=false())
    first_seen_at: Mapped[datetime] = mapped_column(server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(server_default=func.now())
    source: Mapped[MachineModelSource] = mapped_column(Text)

    __table_args__ = (UniqueConstraint("machine_id", "model_id"),)
