"""`endpoints` and `endpoint_models` — an endpoint and every model seen on it."""

from datetime import datetime
from typing import Literal

from sqlalchemy import ForeignKey, Text, UniqueConstraint, false, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

#: How a model came to be recorded on an endpoint.
EndpointModelSource = Literal["discovered", "manual", "run"]


class Endpoint(Base):
    """An OpenAI-compatible endpoint: a base URL, an optional API key, and
    free-text hardware notes.

    Anything that speaks the protocol is one — an Ollama or vLLM box you own, a
    proxy, or a hosted frontier API. The hardware notes are half the product's
    answer where the endpoint *is* a box ("which model is good enough" is worth
    little without "and what box does it need"), which is why every run names
    the endpoint that produced its numbers, and why they are optional.

    `base_url` / `api_key` are credentials and are therefore read **live** at
    execution time rather than frozen into a run, so a moved endpoint does not
    break Resume.
    """

    __tablename__ = "endpoints"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.id", ondelete="RESTRICT"), index=True
    )
    #: Readable from **every** workspace, writable only from the one that owns
    #: it — which is why it may only be set on a row owned by the Base
    #: workspace (`app.repos.endpoints` enforces that on create and on update,
    #: so no call site can forget). A consultancy runs the same DGX Spark
    #: across every engagement, and re-registering it per workspace duplicates
    #: an API key and guarantees half the copies go stale.
    #:
    #: The sharing is expressed as one read-side seam,
    #: `app.scope.visible_where` — `customer_id` stays the ownership predicate
    #: every UPDATE and DELETE uses, so "read-only outside Base" needs no
    #: permission layer.
    is_global: Mapped[bool] = mapped_column(server_default=false())
    name: Mapped[str]
    base_url: Mapped[str]
    api_key: Mapped[str | None]
    cpu: Mapped[str | None]
    ram: Mapped[str | None]
    gpu: Mapped[str | None]
    notes: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class EndpointModel(Base):
    """Every model ever seen on an endpoint. Never deleted from.

    Discovery upserts and flips `currently_loaded` false for models absent from
    `/v1/models`; manual adds and run creation upsert too (`source`). Keeping
    the history is what lets a past run still name a model the endpoint no
    longer serves.

    Scope is inherited through `endpoint_id`; there is no `customer_id` here.

    On a **global** endpoint that means the history accumulates across every
    engagement: discovery, a manual add and every run write here, and on a
    shared box those runs come from whichever workspace booked it. That is
    intended, not a leak — shared hardware has one shared history, and "this
    box has already served qwen3:32b" is exactly what the next engagement needs
    to know. Nothing customer-specific lands here; a row is a model id, a
    timestamp and how it was first seen.
    """

    __tablename__ = "endpoint_models"

    id: Mapped[int] = mapped_column(primary_key=True)
    endpoint_id: Mapped[int] = mapped_column(ForeignKey("endpoints.id", ondelete="CASCADE"))
    model_id: Mapped[str]
    currently_loaded: Mapped[bool] = mapped_column(server_default=false())
    first_seen_at: Mapped[datetime] = mapped_column(server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(server_default=func.now())
    source: Mapped[EndpointModelSource] = mapped_column(Text)

    __table_args__ = (UniqueConstraint("endpoint_id", "model_id"),)
