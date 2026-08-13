"""Tool-mode validation shared between the test-case editor and run creation.

A test case with `tool_mode != "none"` has to actually offer something real: no
selected toolset with any *enabled* tool would quietly turn a tool test into an
ordinary prompt that measured nothing, and two selected toolsets defining the
same tool name would only ever let the model see one of the two definitions.
`assert_tool_config` is the one place that check lives — called from
`app.api.test_cases` at authoring time, and (unchanged) from run creation
(Task 4.3) — so a test case saved through this API can never be one a run
would later refuse.

Ported from `git show master:src/lib/tools.ts` (`collectToolNameCollisions`,
`normalizeMaxTurns`, `DEFAULT_MAX_TURNS`, `MAX_TURNS_LIMIT`) and the two call
sites that used to duplicate the "no enabled tools" / collision rule:
`git show master:src/lib/mcp/tools-authoring.ts`'s `assertToolConfig` (checked
from toolset refs, before resolving them) and
`git show master:src/lib/run-create.ts`'s inline version (checked from an
already-resolved tool snapshot). This module checks from toolset ids, which
both callers can produce.

Kept in `app.services` rather than `app.repos` because it is a rule with a
database lookup inside it, not a query — the same split `app.services.diff`
and `app.services.attribution` draw, except this one cannot stay pure since
"which tools are enabled" is a live read.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Toolset
from app.repos.customers import assert_same_customer
from app.repos.toolsets import list_tools
from app.scope import Scope

#: The agentic loop's own turn budget: the default offered when nothing is
#: specified, and the hard ceiling regardless of what a test case asks for.
DEFAULT_MAX_TURNS = 6
MAX_TURNS_LIMIT = 20


def normalize_max_turns(value: int | None) -> int:
    """Clamps to `[1, MAX_TURNS_LIMIT]`; `None` is the default.

    Pydantic's own `int` coercion already turns "not a number" into a 422
    before this runs (the old JS version additionally floored a fractional
    `float` — there is no such input to floor once the wire type is `int`),
    so this only ever has an in-range-or-not integer left to clamp.
    """
    if value is None:
        return DEFAULT_MAX_TURNS
    if value < 1:
        return 1
    if value > MAX_TURNS_LIMIT:
        return MAX_TURNS_LIMIT
    return value


@dataclass(frozen=True)
class OfferedTool:
    """Just enough of a tool to decide whether it collides with another —
    mirrors the old `ToolLike` shape so `collect_tool_name_collisions` can
    filter disabled rows itself, exactly like `collectToolNameCollisions` did.
    """

    name: str
    enabled: bool = True


def collect_tool_name_collisions(tools: Sequence[OfferedTool]) -> list[str]:
    """Names offered by more than one *enabled* tool, sorted.

    A disabled tool is never sent, so it cannot collide with anything —
    filtered out here rather than by the caller, matching the old function's
    own behavior.
    """
    seen: set[str] = set()
    collisions: set[str] = set()
    for tool in tools:
        if not tool.enabled:
            continue
        if tool.name in seen:
            collisions.add(tool.name)
        seen.add(tool.name)
    return sorted(collisions)


class ToolConfigError(Exception):
    """A tool test that would run with nothing meaningful to offer, or with
    an unresolvable name collision. Raised with the sentence a caller can show
    verbatim — see the two messages below.
    """


async def assert_tool_config(
    scope: Scope,
    session: AsyncSession,
    *,
    tool_mode: str,
    toolset_ids: Sequence[int],
    subject: str,
) -> None:
    """Refuses a `tool_mode` that would run with no enabled tools, or with a
    name collision across the selected toolsets.

    `subject` names what is wrong in the raised message, e.g.
    `Test case "Reconcile invoice"`. A no-op for `tool_mode == "none"` —
    nothing is offered, so there is nothing to check.

    Toolset ids outside this workspace raise `CrossCustomerError` (from
    `assert_same_customer`) rather than `ToolConfigError`, so a caller can
    still tell "misconfigured" from "doesn't exist here" apart.
    """
    if tool_mode == "none":
        return

    no_tools_message = (
        f'{subject} has tool mode "{tool_mode}" but no enabled tools. '
        "Pick a toolset or set the mode back to none."
    )
    if not toolset_ids:
        raise ToolConfigError(no_tools_message)

    await assert_same_customer(session, scope, Toolset, toolset_ids)
    tools = await list_tools(scope, session, toolset_ids=list(toolset_ids))
    offered = [OfferedTool(name=tool.name, enabled=tool.enabled) for tool in tools]

    if not any(tool.enabled for tool in offered):
        raise ToolConfigError(no_tools_message)

    collisions = collect_tool_name_collisions(offered)
    if collisions:
        raise ToolConfigError(
            f"{subject} selects toolsets that both define: {', '.join(collisions)}. "
            "Tool names must be unique within one prompt."
        )
