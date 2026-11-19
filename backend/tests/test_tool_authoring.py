"""`app.services.tool_authoring` — the rules both tool write doors share.

Pure on purpose: what a valid tool name, schema or canned response is must be
the same sentence whether the tool arrived through the toolset editor or from an
agent over MCP, so the rules are pinned here once rather than per door.
"""

from __future__ import annotations

import json

import pytest

from app.services.tool_authoring import (
    EMPTY_PARAMETERS_JSON,
    MAX_MOCK_RESPONSES,
    MAX_TOOL_TEXT_CHARS,
    clean_mock_overrides,
    clean_mock_responses,
    clean_parameters_json,
    clean_tool_description,
    clean_tool_name,
    decode_mock_overrides,
    decode_mock_responses,
    encode_mock_overrides,
    encode_mock_responses,
)


class TestToolName:
    def test_trims_and_keeps_a_valid_function_name(self) -> None:
        assert clean_tool_name("  lookup_order-v2 ") == "lookup_order-v2"

    @pytest.mark.parametrize("name", ["", "   ", "has space", "dotted.name", "ü", "x" * 65])
    def test_refuses_anything_a_provider_would_reject(self, name: str) -> None:
        with pytest.raises(ValueError, match="letters, digits, underscores and hyphens"):
            clean_tool_name(name)

    def test_sixty_four_characters_is_the_ceiling_not_past_it(self) -> None:
        assert clean_tool_name("x" * 64) == "x" * 64


class TestDescription:
    def test_blank_is_none(self) -> None:
        assert clean_tool_description(None) is None
        assert clean_tool_description("  \n ") is None

    def test_keeps_a_long_multi_line_description_trimmed(self) -> None:
        text = "Line one.\n" * 2500  # ~25k characters, a real customer's tool
        assert clean_tool_description(f"\n{text}\n") == text.strip()

    def test_refuses_past_the_ceiling(self) -> None:
        with pytest.raises(ValueError, match="200,000"):
            clean_tool_description("x" * (MAX_TOOL_TEXT_CHARS + 1))

    def test_refuses_a_nul_byte(self) -> None:
        # Postgres cannot store one in a text column; the alternative is a 500.
        with pytest.raises(ValueError, match="NUL"):
            clean_tool_description("a\x00b")


class TestParameters:
    def test_empty_is_the_no_argument_schema(self) -> None:
        for value in (None, "", "   ", {}):
            assert clean_parameters_json(value) == EMPTY_PARAMETERS_JSON

    def test_a_string_is_kept_as_typed(self) -> None:
        typed = '{\n  "type": "object",\n  "properties": {"id": {"type": "integer"}}\n}'
        assert clean_parameters_json(f"  {typed}  ") == typed

    def test_an_object_is_serialized(self) -> None:
        schema = {"type": "object", "properties": {"id": {"type": "integer"}}}
        assert json.loads(clean_parameters_json(schema)) == schema

    def test_refuses_invalid_json(self) -> None:
        with pytest.raises(ValueError, match="valid JSON"):
            clean_parameters_json("{not json")

    @pytest.mark.parametrize("value", ["[]", '"string"', "42", "null"])
    def test_refuses_anything_but_an_object(self, value: str) -> None:
        with pytest.raises(ValueError, match="JSON object"):
            clean_parameters_json(value)


class TestMockResponses:
    def test_none_and_empty_mean_none_configured(self) -> None:
        assert clean_mock_responses(None) == []
        assert clean_mock_responses([]) == []
        assert clean_mock_responses("   ") == []
        assert clean_mock_responses(["  "]) == []

    def test_a_string_is_the_one_element_case(self) -> None:
        assert clean_mock_responses('  {"rate": 1.1}\n') == ['{"rate": 1.1}']

    def test_a_list_keeps_its_order_and_trims_each(self) -> None:
        assert clean_mock_responses([" first ", "second\n"]) == ["first", "second"]

    def test_a_blank_entry_inside_several_is_refused_naming_it(self) -> None:
        with pytest.raises(ValueError, match="Canned response 2 is blank"):
            clean_mock_responses(["first", "  ", "third"])

    def test_refuses_more_than_the_cap(self) -> None:
        assert len(clean_mock_responses(["x"] * MAX_MOCK_RESPONSES)) == MAX_MOCK_RESPONSES
        with pytest.raises(ValueError, match="at most 20"):
            clean_mock_responses(["x"] * (MAX_MOCK_RESPONSES + 1))

    def test_a_long_json_answer_is_fine_past_the_ceiling_is_not(self) -> None:
        answer = json.dumps({"rows": ["x" * 100] * 500})  # ~50k characters
        assert clean_mock_responses(answer) == [answer]
        with pytest.raises(ValueError, match="Canned response 1 is"):
            clean_mock_responses("x" * (MAX_TOOL_TEXT_CHARS + 1))

    def test_refuses_a_nul_byte_naming_the_entry(self) -> None:
        with pytest.raises(ValueError, match="Canned response 2 may not contain NUL"):
            clean_mock_responses(["ok", "a\x00b"])

    def test_refuses_a_non_string_entry(self) -> None:
        with pytest.raises(ValueError, match="must be a string"):
            clean_mock_responses(["ok", 3])  # type: ignore[list-item]

    def test_refuses_an_object_asking_for_it_as_a_string(self) -> None:
        with pytest.raises(ValueError, match="pass a JSON answer as a string"):
            clean_mock_responses({"rate": 1.1})  # type: ignore[arg-type]

    def test_a_json_array_string_is_one_response_not_several(self) -> None:
        # The answer a tool gives can itself be a JSON array.
        assert clean_mock_responses('["a", "b"]') == ['["a", "b"]']


class TestColumnEncoding:
    def test_none_configured_is_null_never_an_empty_array(self) -> None:
        assert encode_mock_responses([]) is None

    def test_round_trips(self) -> None:
        responses = ['{"status": "pending"}', '{"status": "done"}']
        assert decode_mock_responses(encode_mock_responses(responses)) == responses

    def test_reads_anything_malformed_as_none(self) -> None:
        for raw in (None, "", "not json", '{"a": 1}', '"x"'):
            assert decode_mock_responses(raw) == []


class TestCleanMockOverrides:
    def test_none_and_empty_mean_no_overrides(self) -> None:
        assert clean_mock_overrides(None) == {}
        assert clean_mock_overrides({}) == {}

    def test_a_string_is_a_one_element_list_and_json_text_stays_text(self) -> None:
        assert clean_mock_overrides({" lookup ": '{"ok": false}', "search": ["a", " b "]}) == {
            "lookup": ['{"ok": false}'],
            "search": ["a", "b"],
        }

    @pytest.mark.parametrize("value", [[], "x", ["lookup"], 3])
    def test_refuses_anything_but_an_object(self, value: object) -> None:
        with pytest.raises(ValueError, match="must be an object"):
            clean_mock_overrides(value)

    def test_refuses_a_blank_tool_name(self) -> None:
        with pytest.raises(ValueError, match="needs a tool name"):
            clean_mock_overrides({"  ": "x"})

    def test_refuses_two_keys_that_trim_to_one_name(self) -> None:
        with pytest.raises(ValueError, match="given twice"):
            clean_mock_overrides({"lookup": "a", "lookup ": "b"})

    def test_refuses_an_override_with_nothing_in_it(self) -> None:
        for empty in ("", "   ", [], None):
            with pytest.raises(ValueError, match='"lookup" has no canned response'):
                clean_mock_overrides({"lookup": empty})

    def test_each_value_follows_the_tools_own_rules_and_names_the_tool(self) -> None:
        with pytest.raises(ValueError, match='override for "lookup": Canned response 2 is blank'):
            clean_mock_overrides({"lookup": ["a", " "]})
        with pytest.raises(ValueError, match='override for "lookup": .*pass a JSON answer'):
            clean_mock_overrides({"lookup": {"ok": True}})
        with pytest.raises(ValueError, match="at most"):
            clean_mock_overrides({"lookup": ["x"] * (MAX_MOCK_RESPONSES + 1)})


class TestMockOverridesColumn:
    def test_round_trips_and_none_is_null(self) -> None:
        overrides = {"lookup": ['{"ok": true}', "second"]}
        raw = encode_mock_overrides(overrides)
        assert raw is not None and json.loads(raw) == overrides
        assert decode_mock_overrides(raw) == overrides
        assert encode_mock_overrides({}) is None

    def test_reads_anything_malformed_as_none(self) -> None:
        for raw in (None, "", "not json", "[]", '{"a": "x"}', '{"a": [1]}'):
            assert decode_mock_overrides(raw) == {}
