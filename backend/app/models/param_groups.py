"""`param_groups` — named, reusable request-parameter presets.

A param group is a workspace's own vocabulary for a request-body shape it keeps
reaching for — "no thinking" holding vLLM's `chat_template_kwargs`, "temp 0",
"long output". It lives one level above endpoints and models on purpose: the
same preset is selectable against any box and any model, and run creation merges
it between the endpoint's `default_params` and the run's own overrides
(`app.services.params`). The app's no-abstraction stance on params is untouched —
the group's keys are still provider vocabulary sent verbatim; only the *name*
is an abstraction, and it is the user's.

Deliberately not shareable (`is_global` does not exist here): groups hold no
credentials, so like prompts and test groups they are the engagement's own
material. And deliberately never referenced by a run FK — a run freezes the
merged params and the selected names at creation, so editing or deleting a
group can never change what a past run sent or displays.
"""

from datetime import datetime

from sqlalchemy import ForeignKey, Index, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ParamGroup(Base):
    """One named preset: a JSON object of request-body params.

    `params` is validated by `app.services.params.validate_params` with
    `allow_null_values=True` at every write — a group is a *patch*, and a null
    value is how it unsets an endpoint default (`merge_params` drops nulls
    after merging, so one never reaches the wire).
    """

    __tablename__ = "param_groups"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id", ondelete="RESTRICT"))
    name: Mapped[str]
    description: Mapped[str | None]
    #: JSON object stored as text, like `Endpoint.default_params`.
    params: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())

    # Non-unique on purpose, like `test_groups`/`prompts` — app code and MCP
    # name resolution produce better messages than a constraint violation.
    __table_args__ = (Index("param_groups_customer_name_idx", "customer_id", "name"),)
