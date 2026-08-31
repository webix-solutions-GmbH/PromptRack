"""Request-body parameters: what may be sent, and how two levels of them merge.

The parameters a run sends are **raw key/value pairs**, passed through to the
provider verbatim — `app.services.llm` does a blind `body.update(params)` into
an otherwise OpenAI-compatible request body. There is deliberately no
abstraction layer and no per-key value validation: `temperature`, `top_k`,
`reasoning_effort` and `chat_template_kwargs` are the provider's vocabulary, the
provider is the authority on what its own values mean, and a range check here
would only ever be this app's guess going stale. A platform catalog on the
frontend offers *suggestions*; nothing narrows what may actually be sent.

What is refused is therefore structural, not semantic:

* the six keys `llm.py` sets itself (:data:`RESERVED_PARAM_KEYS`) — a param
  named `messages` or `tools` would not tune a request, it would replace it;
* keys that are not non-blank strings, and values JSON cannot carry, since the
  column is a JSON object stored as text and the wire format is JSON;
* anything past 16 KiB serialized, so a paste accident cannot become a column.

Params attach at **three levels** — an endpoint's `default_params`, the
parameter groups a run selects (folded into one layer by
:func:`combine_group_params`), and the run's own overrides — merged in that
order by chaining :func:`merge_params`: shallow, per-key, the later layer wins.
Shallow because a nested object (vLLM's
`chat_template_kwargs`) is one setting the provider reads whole, so replacing it
wholesale is the only merge that cannot invent a combination nobody asked for.
A `None` override **unsets** a default rather than sending a null: nulls are the
unset signal at the API surface and never reach the wire.

The merged dict is what run creation freezes into `runs.params`, which is the
snapshot invariant applied to parameters — editing an endpoint's defaults must
never change what a past run sent, or what resuming it would send. Only the
merged result is stored: it is byte-for-byte what went over the wire, which is
what drift and reproducibility need, and the two-level provenance is not.

Kept free of the database and of Pydantic, the same split
`app.services.message_assembly` and `app.services.attribution` draw — the API
layer, MCP, run creation and the executor all reach the same rules through
these functions.
"""

import json
from collections.abc import Mapping, Sequence
from typing import Any

#: Keys `app.services.llm` sets itself when it builds the request body. A
#: parameter by any of these names would not tune the request, it would
#: overwrite the run: its model, its messages, the streaming it is measured
#: through, or the tools the test case offers.
RESERVED_PARAM_KEYS: frozenset[str] = frozenset(
    {"model", "messages", "stream", "stream_options", "tools", "tool_choice"}
)

#: Serialized ceiling for one params object. Generous for the knobs this is
#: for, small enough that a pasted transcript is refused rather than stored.
MAX_PARAMS_BYTES = 16 * 1024


class ParamsError(Exception):
    """Params that cannot be sent, raised with the sentence a caller can show
    verbatim — the same shape as `app.services.tool_config.ToolConfigError`.
    """


def validate_params(value: object, *, allow_null_values: bool = False) -> dict[str, Any]:
    """Checks one params object and returns it, or raises :class:`ParamsError`.

    Structural only — see the module docstring. No value is range-checked,
    enumerated or coerced; nested objects and arrays are explicitly fine, since
    that is what a provider like vLLM takes for `chat_template_kwargs` or
    `guided_json`.

    `allow_null_values` is the difference between the two levels. An endpoint's
    defaults are a set of values, so a null there means nothing (False, the
    default); a run's overrides are a *patch* over those defaults, where a null
    is how a key gets unset (True). :func:`merge_params` drops them afterwards,
    so a null never reaches the wire either way.
    """
    if not isinstance(value, dict):
        raise ParamsError("Parameters must be a JSON object of key/value pairs.")

    for key, item in value.items():
        if not isinstance(key, str) or not key.strip():
            raise ParamsError("Every parameter needs a name.")
        if key in RESERVED_PARAM_KEYS:
            raise ParamsError(f'"{key}" is set by the run itself and cannot be a parameter.')
        if item is None:
            if not allow_null_values:
                raise ParamsError(f'"{key}" has no value. Give it one, or remove the parameter.')
            continue
        try:
            json.dumps(item)
        except (TypeError, ValueError) as exc:
            raise ParamsError(f'"{key}" has a value that cannot be sent as JSON.') from exc

    serialized = json.dumps(value)
    if len(serialized.encode("utf-8")) > MAX_PARAMS_BYTES:
        raise ParamsError(
            f"Parameters are too large ({MAX_PARAMS_BYTES // 1024} KiB is the limit)."
        )

    return dict(value)


def merge_params(
    defaults: Mapping[str, Any] | None, overrides: Mapping[str, Any] | None
) -> dict[str, Any] | None:
    """An endpoint's defaults under a run's own params. `None` when nothing is left.

    Shallow and per-key: an override replaces a default's value whole, nested
    objects included. `None`-valued keys are dropped **after** merging, which is
    what makes an override null an unset rather than a null on the wire — and
    means a run can send fewer params than its endpoint defaults to.
    """
    merged: dict[str, Any] = {**(defaults or {}), **(overrides or {})}
    kept = {key: item for key, item in merged.items() if item is not None}
    return kept or None


def combine_group_params(
    groups: Sequence[tuple[str, Mapping[str, Any] | None]],
) -> dict[str, Any] | None:
    """The selected parameter groups folded into one layer, or `None`.

    Groups are selected as a set, not a stack, so two groups giving one key
    *different* values is refused by name rather than resolved by selection
    order — the same reasoning that refuses duplicate tool names across
    selected toolsets (`app.services.tool_config`). The same key with the same
    value (compared as key-sorted serialized JSON, so `1` and `1.0` differ the
    way they would on the wire while a reordered nested object does not)
    coexists fine, `None` included: a null here is the unset
    signal a group may aim at an endpoint default, exactly like a run override.

    Keys are preserved with their `None` values — dropping them is
    :func:`merge_params`'s job, *after* this layer has had its chance to unset
    a default underneath it.
    """
    combined: dict[str, Any] = {}
    origin: dict[str, str] = {}
    for group_name, params in groups:
        for key, item in (params or {}).items():
            if key in combined and json.dumps(item, sort_keys=True) != json.dumps(
                combined[key], sort_keys=True
            ):
                raise ParamsError(
                    f'Parameter groups "{origin[key]}" and "{group_name}" both set '
                    f'"{key}" to different values. Deselect one of them.'
                )
            combined[key] = item
            origin.setdefault(key, group_name)
    return combined or None


def parse_params_json(raw: str | None) -> dict[str, Any] | None:
    """A stored params column as a dict, or `None`.

    Degrades silently rather than raising: these columns are read on every run
    execution and every display, and a blob that somehow is not a JSON object
    must cost the params, not the run. Everything that writes one goes through
    :func:`validate_params` first.
    """
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def strip_reserved(params: Mapping[str, Any]) -> dict[str, Any]:
    """Drops :data:`RESERVED_PARAM_KEYS`. Defense in depth, at the last moment.

    :func:`validate_params` already refuses these by name at every write, so
    this only ever fires on a row written before that rule existed or edited in
    the database directly. The executor applies it anyway, because the cost is a
    dict comprehension and the failure it prevents is a run whose `messages`
    were replaced by a stored parameter.
    """
    return {key: item for key, item in params.items() if key not in RESERVED_PARAM_KEYS}
