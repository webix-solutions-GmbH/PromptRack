"""The MCP server's own halves: reference resolution, workspace precedence,
the tool registry and the read-only gate.

Database-free on purpose, the same split the old app made between
`src/lib/mcp/args.test.ts` / `customer.test.ts` / `protocol.test.ts` and its
integration suite: what a tool *does* with a scope needs Postgres and is
exercised there, while what decides the scope, what a name resolves to and
which tools a role may call are pure decisions and belong in the fast suite.

`app.mcp.server` is imported for its registry, which is built at import time by
the `@_tool` decorators — importing it is itself the check that every tool
registers with a valid signature and schema.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest
from starlette.requests import Request

from app.auth.guards import Actor
from app.mcp.customer import (
    CUSTOMER_HEADER,
    McpScopeSource,
    pick_customer_ref,
    resolve_customer_ref,
    scope_source_from_headers,
)
from app.mcp.refs import (
    McpToolError,
    RowRef,
    has_key,
    optional_row_ref,
    parse_row_ref,
    parse_row_refs,
    resolve_row_ref,
    truncate,
)
from app.mcp.server import (
    _WRITES,
    KIND_VALUES,
    _call,
    _parse_kind,
    mcp_server,
    raw_arguments,
)


@dataclass(frozen=True)
class Row:
    """The shape every `RowRef` resolves against: an id and a name."""

    id: int
    name: str


# ---------------------------------------------------------------------------
# Row references
# ---------------------------------------------------------------------------


class TestParseRowRef:
    def test_a_number_is_an_id(self) -> None:
        assert parse_row_ref(7, '"group"') == RowRef.by_id(7)

    def test_a_numeric_string_is_an_id_too(self) -> None:
        # "12" is never a sensible name, and treating it as one would silently
        # create a second group called "12".
        assert parse_row_ref("12", '"group"') == RowRef.by_id(12)

    def test_a_word_is_a_name(self) -> None:
        assert parse_row_ref("  Odoo  ", '"group"') == RowRef.by_name("Odoo")

    def test_a_boolean_is_not_an_id(self) -> None:
        # `True` is an `int` in Python and would otherwise resolve as id 1.
        with pytest.raises(McpToolError):
            parse_row_ref(True, '"group"')

    def test_an_empty_string_is_refused(self) -> None:
        with pytest.raises(McpToolError, match="must not be empty"):
            parse_row_ref("   ", '"group"')

    def test_a_fractional_number_is_refused(self) -> None:
        with pytest.raises(McpToolError, match="whole-number id"):
            parse_row_ref(1.5, '"group"')

    def test_an_integral_float_still_resolves(self) -> None:
        assert parse_row_ref(3.0, '"group"') == RowRef.by_id(3)

    def test_the_label_names_the_argument(self) -> None:
        with pytest.raises(McpToolError, match='"machine"'):
            parse_row_ref({}, '"machine"')


class TestParseRowRefs:
    def test_a_single_value_counts_as_a_one_element_list(self) -> None:
        assert parse_row_refs("Invoices", "groups") == [RowRef.by_name("Invoices")]

    def test_an_absent_list_is_empty_not_an_error(self) -> None:
        assert parse_row_refs(None, "groups") == []

    def test_each_entry_is_parsed(self) -> None:
        assert parse_row_refs(["Invoices", 4], "groups") == [
            RowRef.by_name("Invoices"),
            RowRef.by_id(4),
        ]


class TestResolveRowRef:
    rows = [Row(1, "Invoices"), Row(2, "Support desk")]

    def test_resolves_by_id(self) -> None:
        assert resolve_row_ref(RowRef.by_id(2), self.rows, "test group").name == "Support desk"

    def test_resolves_by_name_case_insensitively(self) -> None:
        assert resolve_row_ref(RowRef.by_name("invoices"), self.rows, "test group").id == 1

    def test_an_unknown_id_is_refused(self) -> None:
        with pytest.raises(McpToolError, match="No test group with id 9"):
            resolve_row_ref(RowRef.by_id(9), self.rows, "test group")

    def test_an_unknown_name_lists_what_exists(self) -> None:
        # The miss doubles as the discovery path for a caller that guessed.
        with pytest.raises(McpToolError, match=r"Known: Invoices \(1\), Support desk \(2\)\."):
            resolve_row_ref(RowRef.by_name("invoicing"), self.rows, "test group")

    def test_an_ambiguous_name_is_refused_never_guessed(self) -> None:
        rows = [Row(1, "Invoices"), Row(5, "invoices")]
        with pytest.raises(McpToolError, match=r"ids 1, 5"):
            resolve_row_ref(RowRef.by_name("Invoices"), rows, "test group")


class TestTruncate:
    def test_short_text_is_returned_whole(self) -> None:
        assert truncate("hello", 10) == truncate("hello", 10)
        assert truncate("hello", 10).text == "hello"
        assert truncate("hello", 10).truncated is False

    def test_zero_means_no_limit(self) -> None:
        assert truncate("hello", 0).text == "hello"

    def test_long_text_is_marked_as_cut(self) -> None:
        result = truncate("hello", 2)
        assert result.text == "he…"
        assert result.truncated is True

    def test_none_stays_none(self) -> None:
        assert truncate(None, 5).text is None


class TestHasKey:
    def test_an_explicit_null_still_counts_as_mentioned(self) -> None:
        # What patch semantics rest on: `null` clears, absent leaves alone.
        assert has_key({"note": None}, "note") is True

    def test_an_absent_key_does_not(self) -> None:
        assert has_key({}, "note") is False
        assert has_key(None, "note") is False


# ---------------------------------------------------------------------------
# Which workspace a call runs in
# ---------------------------------------------------------------------------


class TestScopeSourceFromHeaders:
    def test_reads_the_customer_header(self) -> None:
        source = scope_source_from_headers({CUSTOMER_HEADER: "Acme"})
        assert source.header == RowRef.by_name("Acme")

    def test_a_numeric_header_is_an_id(self) -> None:
        assert scope_source_from_headers({CUSTOMER_HEADER: "3"}).header == RowRef.by_id(3)

    def test_an_empty_header_counts_as_absent(self) -> None:
        assert scope_source_from_headers({CUSTOMER_HEADER: "  "}).header is None

    def test_no_headers_at_all(self) -> None:
        assert scope_source_from_headers(None).header is None


class TestPickCustomerRef:
    def test_the_argument_wins_over_the_header(self) -> None:
        source = McpScopeSource(header=RowRef.by_name("Acme"))
        assert pick_customer_ref("Globex", source) == RowRef.by_name("Globex")

    def test_the_header_applies_when_the_call_says_nothing(self) -> None:
        source = McpScopeSource(header=RowRef.by_name("Acme"))
        assert pick_customer_ref(None, source) == RowRef.by_name("Acme")

    def test_the_tokens_default_is_the_last_resort(self) -> None:
        source = McpScopeSource(header=None, token_default=4)
        assert pick_customer_ref(None, source) == RowRef.by_id(4)

    def test_nothing_at_all_stays_nothing(self) -> None:
        assert pick_customer_ref(None, McpScopeSource(header=None)) is None


class TestResolveCustomerRef:
    rows = [Row(1, "Acme"), Row(2, "Globex")]

    def test_resolves_a_named_workspace(self) -> None:
        assert resolve_customer_ref(RowRef.by_name("globex"), self.rows) == 2

    def test_refusing_names_both_ways_of_saying_it(self) -> None:
        # An unscoped write has no defined destination, so the refusal has to
        # be actionable rather than a guess.
        with pytest.raises(McpToolError) as excinfo:
            resolve_customer_ref(None, self.rows)
        message = str(excinfo.value)
        assert '"customer"' in message
        assert CUSTOMER_HEADER in message.lower()
        assert "Acme (1), Globex (2)" in message

    def test_refusing_with_no_workspaces_at_all(self) -> None:
        with pytest.raises(McpToolError, match="Every call is scoped"):
            resolve_customer_ref(None, [])


# ---------------------------------------------------------------------------
# The registry
# ---------------------------------------------------------------------------


#: Exactly the surface the plan specifies. `deploy` is deliberately absent —
#: marking a version deployed is a human claim about a customer's production
#: system — as are machines, toolsets and customer workspaces as writes.
EXPECTED_TOOLS = {
    "list_customers": False,
    "list_machines": False,
    "list_test_groups": False,
    "create_test_group": True,
    "list_prompts": False,
    "create_prompt": True,
    "update_prompt": True,
    "commit_prompt": True,
    "list_prompt_versions": False,
    "get_prompt_version": False,
    "set_baseline": True,
    "create_test_case": True,
    "update_test_case": True,
    "list_test_cases": False,
    "create_run": True,
    "execute_run": True,
    "get_run": False,
    "get_run_result": False,
    "list_runs": False,
    "set_rating": True,
}


async def _tools() -> dict[str, Any]:
    return {tool.name: tool for tool in await mcp_server.list_tools()}


class TestRegistry:
    async def test_exactly_the_specified_tools_are_offered(self) -> None:
        assert set(await _tools()) == set(EXPECTED_TOOLS)

    async def test_no_tool_writes_to_machines_toolsets_or_workspaces(self) -> None:
        names = set(await _tools())
        assert not {name for name in names if name.startswith(("create_machine", "update_machine"))}
        assert not {name for name in names if "toolset" in name}
        assert not {name for name in names if name.startswith("create_customer")}
        assert "deploy" not in names and "mark_deployed" not in names

    async def test_read_only_annotation_and_the_role_gate_agree(self) -> None:
        # One declaration per tool feeds both, which is what keeps a writing
        # tool from ever being advertised as read-only.
        for name, tool in (await _tools()).items():
            assert tool.annotations is not None
            assert tool.annotations.read_only_hint is not EXPECTED_TOOLS[name]
            assert _WRITES[name] is EXPECTED_TOOLS[name]

    async def test_every_tool_describes_itself(self) -> None:
        for tool in (await _tools()).values():
            assert tool.description and len(tool.description) > 40

    async def test_every_scoped_tool_takes_an_optional_customer(self) -> None:
        # Not `required`: the X-Customer header can supply it, which JSON
        # Schema cannot express — the runtime refusal carries that instead.
        for name, tool in (await _tools()).items():
            if name == "list_customers":
                assert "customer" not in tool.input_schema.get("properties", {})
                continue
            assert "customer" in tool.input_schema["properties"], name
            assert "customer" not in tool.input_schema.get("required", []), name

    async def test_required_arguments_are_the_ones_without_defaults(self) -> None:
        tools = await _tools()
        # `content` is *not* required any more: a task prompt can be the whole
        # user message, so "this prompt takes no input" has to be expressible.
        assert set(tools["create_test_case"].input_schema["required"]) == {
            "group",
            "title",
        }
        assert set(tools["set_rating"].input_schema["required"]) == {"result_id", "rating"}
        assert set(tools["create_run"].input_schema["required"]) == {"machine", "groups"}

    async def test_a_name_or_an_id_is_accepted_wherever_a_row_is_named(self) -> None:
        schema = (await _tools())["create_test_case"].input_schema
        types = {entry.get("type") for entry in schema["properties"]["group"]["anyOf"]}
        assert types == {"string", "integer"}


# ---------------------------------------------------------------------------
# Prompt kinds
# ---------------------------------------------------------------------------


class TestPromptKindArgument:
    """`kind` on the two prompt writes: advertised as an enum, and re-checked.

    The schema and the runtime check are two independent defences on purpose —
    a client that skips schema validation still has to get a readable refusal
    rather than an unknown value reaching the column.
    """

    async def test_create_advertises_the_two_kinds_and_defaults_to_system(self) -> None:
        schema = (await _tools())["create_prompt"].input_schema["properties"]["kind"]
        assert schema["enum"] == list(KIND_VALUES)
        # "system" is the channel everything authored before the pivot was sent
        # on; defaulting anywhere else would move text between channels.
        assert schema["default"] == "system"
        assert "kind" not in (await _tools())["create_prompt"].input_schema["required"]

    async def test_update_advertises_the_two_kinds_and_defaults_to_leaving_it(self) -> None:
        schema = (await _tools())["update_prompt"].input_schema["properties"]["kind"]
        enums = [entry["enum"] for entry in schema["anyOf"] if "enum" in entry]
        assert enums == [list(KIND_VALUES)]
        # `null` = "say nothing about kind", which is what keeps a plain draft
        # edit from moving the prompt's channel.
        assert schema["default"] is None
        assert {"type": "null"} in schema["anyOf"]


class TestParseKind:
    def test_accepts_each_known_kind_unchanged(self) -> None:
        assert _parse_kind("system") == "system"
        assert _parse_kind("task") == "task"

    def test_refuses_an_unrecognised_kind_never_coercing_it(self) -> None:
        # Deliberately the opposite of `parse_role`, which degrades an unknown
        # role to `viewer`: there the fallback is the least privileged value,
        # here guessing a channel would silently move the text.
        with pytest.raises(McpToolError, match='"kind" must be "system" or "task"'):
            _parse_kind("user")

    def test_the_refusal_quotes_what_was_actually_sent(self) -> None:
        with pytest.raises(McpToolError, match="'assistant'"):
            _parse_kind("assistant")

    def test_case_and_whitespace_are_not_guessed_at_either(self) -> None:
        for value in ("System", "SYSTEM", " system ", "system\n"):
            with pytest.raises(McpToolError):
                _parse_kind(value)

    def test_refuses_a_non_string_and_an_absent_value(self) -> None:
        for value in (None, 0, 1, True, ["system"], {"kind": "system"}):
            with pytest.raises(McpToolError):
                _parse_kind(value)


class TestPromptSlotArguments:
    """The two new `RowRef` arguments that replaced `prompt`/`mode`/`custom_text`."""

    async def test_both_slots_take_a_name_or_an_id_on_both_write_tools(self) -> None:
        tools = await _tools()
        for name in ("create_test_case", "update_test_case"):
            properties = tools[name].input_schema["properties"]
            for slot in ("system_prompt", "task_prompt"):
                types = {entry.get("type") for entry in properties[slot]["anyOf"]}
                # `null` is in there so a slot can be left empty (create) and
                # explicitly cleared (update).
                assert types == {"string", "integer", "null"}, (name, slot)
                assert properties[slot]["default"] is None

    async def test_the_removed_arguments_are_gone_from_both_write_tools(self) -> None:
        # `mode` and `custom_text` existed only to splice unversioned text into
        # a versioned asset; with two slots there is nothing left to splice.
        tools = await _tools()
        for name in ("create_test_case", "update_test_case"):
            properties = tools[name].input_schema["properties"]
            assert "mode" not in properties, name
            assert "custom_text" not in properties, name
            assert "prompt" not in properties, name

    def test_a_slot_argument_parses_by_name_and_by_id(self) -> None:
        assert parse_row_ref("PO judge", '"task_prompt"') == RowRef.by_name("PO judge")
        assert parse_row_ref(4, '"system_prompt"') == RowRef.by_id(4)
        assert parse_row_ref("4", '"system_prompt"') == RowRef.by_id(4)

    def test_an_empty_slot_is_absent_rather_than_a_ref(self) -> None:
        assert optional_row_ref(None, '"task_prompt"') is None

    def test_a_refusal_names_the_slot_that_was_wrong(self) -> None:
        # Two slots now, so "which one did I get wrong" has to be in the text.
        with pytest.raises(McpToolError, match='"task_prompt"'):
            parse_row_ref({}, '"task_prompt"')
        with pytest.raises(McpToolError, match='"system_prompt"'):
            parse_row_ref("   ", '"system_prompt"')


# ---------------------------------------------------------------------------
# The read-only gate
# ---------------------------------------------------------------------------


def _ctx(actor: Actor | None, arguments: dict[str, Any] | None = None) -> Any:
    """A stand-in for the SDK's `Context`, carrying what our code reads off it:
    the authenticated actor (put on the ASGI scope by `McpAuthMiddleware`) and
    the raw `tools/call` arguments.
    """
    state: dict[str, Any] = {} if actor is None else {"mcp_actor": actor}
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/mcp",
            "headers": [],
            "query_string": b"",
            "state": state,
        }
    )
    return SimpleNamespace(
        request_context=SimpleNamespace(
            request=request, params={"name": "x", "arguments": arguments}
        ),
        headers={},
    )


def _actor_with(role: str) -> Actor:
    return Actor(user_id=1, email="a@example.com", name="A", role=role, via="token")


class TestRoleGate:
    async def test_a_viewers_token_is_refused_a_writing_tool(self) -> None:
        # `isError` content rather than a protocol error: the calling model
        # reads the message and stops trying.
        with pytest.raises(McpToolError, match="read-only"):
            async with _call(_ctx(_actor_with("viewer")), "create_prompt"):
                pass  # pragma: no cover - the gate raises first

    async def test_an_unauthenticated_call_never_reaches_a_tool(self) -> None:
        with pytest.raises(McpToolError, match="no credentials"):
            async with _call(_ctx(None), "list_customers"):
                pass  # pragma: no cover - the gate raises first

    async def test_an_unknown_tool_name_is_treated_as_a_write(self) -> None:
        # Fail closed: a tool that forgot to register cannot be reachable by a
        # read-only account.
        with pytest.raises(McpToolError, match="read-only"):
            async with _call(_ctx(_actor_with("viewer")), "not_registered"):
                pass  # pragma: no cover - the gate raises first


class TestRawArguments:
    def test_reads_the_arguments_the_caller_actually_sent(self) -> None:
        assert raw_arguments(_ctx(None, {"note": None})) == {"note": None}

    def test_absent_arguments_read_as_empty(self) -> None:
        assert raw_arguments(_ctx(None, None)) == {}
