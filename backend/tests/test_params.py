"""Request parameters, database-free.

Everything the app is willing to send, and what an endpoint's defaults do when
a run overrides them. Pure, so the structural rules can be pinned down here and
the integration suite only has to show that the API and run creation call them.
"""

import json

import pytest

from app.services.params import (
    RESERVED_PARAM_KEYS,
    ParamsError,
    combine_group_params,
    merge_params,
    parse_params_json,
    strip_reserved,
    validate_params,
)


class TestValidateParams:
    def test_accepts_an_ordinary_params_object(self) -> None:
        params = {"temperature": 0.2, "max_tokens": 512, "stop": ["\n\n"]}
        assert validate_params(params) == params

    def test_accepts_nested_objects_and_arrays(self) -> None:
        # vLLM's `chat_template_kwargs` and `guided_json` are objects, and the
        # rule is deliberately structural: nothing here knows what they mean.
        params = {
            "chat_template_kwargs": {"enable_thinking": False},
            "guided_json": {"type": "object", "properties": {"total": {"type": "number"}}},
            "stop_token_ids": [128001, 128009],
        }
        assert validate_params(params) == params

    def test_accepts_an_empty_object(self) -> None:
        assert validate_params({}) == {}

    def test_returns_a_copy(self) -> None:
        original = {"temperature": 0.2}
        assert validate_params(original) is not original

    def test_refuses_a_non_dict(self) -> None:
        for value in ([("temperature", 0.2)], "temperature=0.2", 7, None):
            with pytest.raises(ParamsError):
                validate_params(value)

    def test_refuses_a_blank_key(self) -> None:
        for key in ("", "   ", "\n"):
            with pytest.raises(ParamsError):
                validate_params({key: 1})

    def test_refuses_a_non_string_key(self) -> None:
        with pytest.raises(ParamsError):
            validate_params({1: "one"})

    def test_refuses_every_reserved_key_and_names_it(self) -> None:
        # A param called `messages` would not tune the request, it would
        # replace it — so the refusal has to say which key.
        for key in RESERVED_PARAM_KEYS:
            with pytest.raises(ParamsError) as excinfo:
                validate_params({key: "anything"})
            assert f'"{key}"' in str(excinfo.value)

    def test_refuses_a_reserved_key_alongside_valid_ones(self) -> None:
        with pytest.raises(ParamsError):
            validate_params({"temperature": 0.2, "tools": []})

    def test_refuses_a_value_json_cannot_carry(self) -> None:
        with pytest.raises(ParamsError) as excinfo:
            validate_params({"stop": {1, 2}})
        assert '"stop"' in str(excinfo.value)

    def test_refuses_a_null_value_by_default(self) -> None:
        with pytest.raises(ParamsError) as excinfo:
            validate_params({"temperature": None})
        assert '"temperature"' in str(excinfo.value)

    def test_accepts_a_null_value_when_allowed(self) -> None:
        # A run's params are a patch over the endpoint's defaults, and a null
        # is how one of those defaults gets unset.
        params = {"temperature": 0.2, "chat_template_kwargs": None}
        assert validate_params(params, allow_null_values=True) == params

    def test_refuses_params_past_the_size_cap(self) -> None:
        with pytest.raises(ParamsError):
            validate_params({"stop": ["x" * 20_000]})

    def test_accepts_params_just_under_the_size_cap(self) -> None:
        params = {"stop": ["x" * 15_000]}
        assert len(json.dumps(params).encode("utf-8")) < 16 * 1024
        assert validate_params(params) == params

    def test_does_not_validate_values(self) -> None:
        # Deliberate: the provider is the authority on its own parameters, and a
        # range check here would only be this app's guess going stale.
        params = {"temperature": 42, "top_p": -1, "reasoning_effort": "extremely"}
        assert validate_params(params) == params


class TestMergeParams:
    def test_overrides_win_per_key(self) -> None:
        assert merge_params({"temperature": 0.2, "top_p": 0.9}, {"temperature": 0.7}) == {
            "temperature": 0.7,
            "top_p": 0.9,
        }

    def test_adds_keys_the_defaults_do_not_have(self) -> None:
        assert merge_params({"temperature": 0.2}, {"seed": 7}) == {
            "temperature": 0.2,
            "seed": 7,
        }

    def test_an_override_null_unsets_a_default(self) -> None:
        # The unset signal — and it never reaches the wire as a null.
        assert merge_params(
            {"temperature": 0.2, "chat_template_kwargs": {"enable_thinking": False}},
            {"chat_template_kwargs": None},
        ) == {"temperature": 0.2}

    def test_all_null_leaves_nothing(self) -> None:
        assert merge_params({"temperature": 0.2}, {"temperature": None}) is None

    def test_a_null_default_is_dropped_too(self) -> None:
        assert merge_params({"temperature": None}, None) is None

    def test_nothing_at_all_is_none(self) -> None:
        assert merge_params(None, None) is None
        assert merge_params({}, {}) is None
        assert merge_params(None, {}) is None

    def test_one_side_alone_is_that_side(self) -> None:
        assert merge_params({"temperature": 0.2}, None) == {"temperature": 0.2}
        assert merge_params(None, {"temperature": 0.7}) == {"temperature": 0.7}

    def test_a_nested_override_replaces_the_default_wholesale(self) -> None:
        # Shallow on purpose: the provider reads a nested object whole, so
        # merging into it would invent a combination nobody asked for.
        assert merge_params(
            {"chat_template_kwargs": {"enable_thinking": False, "tools_in_user": True}},
            {"chat_template_kwargs": {"enable_thinking": True}},
        ) == {"chat_template_kwargs": {"enable_thinking": True}}

    def test_does_not_mutate_its_inputs(self) -> None:
        defaults = {"temperature": 0.2}
        overrides = {"temperature": None, "seed": 7}
        merge_params(defaults, overrides)
        assert defaults == {"temperature": 0.2}
        assert overrides == {"temperature": None, "seed": 7}


class TestCombineGroupParams:
    def test_folds_disjoint_groups_into_one_layer(self) -> None:
        assert combine_group_params(
            [
                ("no thinking", {"chat_template_kwargs": {"enable_thinking": False}}),
                ("temp 0", {"temperature": 0}),
            ]
        ) == {"chat_template_kwargs": {"enable_thinking": False}, "temperature": 0}

    def test_nothing_selected_is_none(self) -> None:
        assert combine_group_params([]) is None
        assert combine_group_params([("empty", {}), ("also empty", None)]) is None

    def test_refuses_two_groups_fighting_over_one_key_and_names_all_three(self) -> None:
        # Groups are a set, not a stack: selection order deciding the value
        # would be exactly the silent combination nobody asked for.
        with pytest.raises(ParamsError) as excinfo:
            combine_group_params(
                [("temp 0", {"temperature": 0}), ("creative", {"temperature": 1.2})]
            )
        message = str(excinfo.value)
        assert '"temp 0"' in message
        assert '"creative"' in message
        assert '"temperature"' in message

    def test_the_same_value_twice_coexists(self) -> None:
        assert combine_group_params(
            [("a", {"temperature": 0}), ("b", {"temperature": 0})]
        ) == {"temperature": 0}

    def test_a_reordered_nested_object_is_the_same_value(self) -> None:
        # Compared as key-sorted JSON — the wire reading, not dict identity.
        assert combine_group_params(
            [
                ("a", {"chat_template_kwargs": {"x": 1, "y": 2}}),
                ("b", {"chat_template_kwargs": {"y": 2, "x": 1}}),
            ]
        ) == {"chat_template_kwargs": {"x": 1, "y": 2}}

    def test_an_int_and_a_float_of_equal_value_differ(self) -> None:
        # `1` and `1.0` serialize differently, so they would differ on the wire.
        with pytest.raises(ParamsError):
            combine_group_params([("a", {"seed": 1}), ("b", {"seed": 1.0})])

    def test_nulls_are_kept_for_the_merge_to_drop(self) -> None:
        # A group null is the unset signal aimed at an endpoint default;
        # dropping it here would blunt it before the merge.
        assert combine_group_params(
            [("no thinking", {"reasoning_effort": None}), ("temp 0", {"temperature": 0})]
        ) == {"reasoning_effort": None, "temperature": 0}

    def test_two_groups_agreeing_on_null_coexist(self) -> None:
        assert combine_group_params(
            [("a", {"reasoning_effort": None}), ("b", {"reasoning_effort": None})]
        ) == {"reasoning_effort": None}

    def test_three_level_merge_precedence(self) -> None:
        # The whole chain as run creation performs it: endpoint defaults under
        # the combined groups under the run's own overrides.
        defaults = {"temperature": 0.2, "top_p": 0.9, "seed": 7}
        groups = combine_group_params(
            [("no thinking", {"temperature": 0, "seed": None})]
        )
        overrides = {"temperature": 0.7}
        assert merge_params(merge_params(defaults, groups), overrides) == {
            "temperature": 0.7,
            "top_p": 0.9,
        }


class TestParseParamsJson:
    def test_parses_a_stored_object(self) -> None:
        assert parse_params_json('{"temperature": 0.2}') == {"temperature": 0.2}

    def test_degrades_malformed_json_to_none(self) -> None:
        # A blob that is somehow not readable must cost the params, not the run.
        assert parse_params_json("{not json") is None

    def test_degrades_json_that_is_not_an_object_to_none(self) -> None:
        assert parse_params_json("[1, 2]") is None
        assert parse_params_json('"temperature"') is None
        assert parse_params_json("null") is None

    def test_treats_nothing_stored_as_none(self) -> None:
        assert parse_params_json(None) is None
        assert parse_params_json("") is None


class TestStripReserved:
    def test_drops_a_reserved_key(self) -> None:
        assert strip_reserved({"messages": [], "temperature": 0.2}) == {"temperature": 0.2}

    def test_drops_every_reserved_key(self) -> None:
        assert strip_reserved(dict.fromkeys(RESERVED_PARAM_KEYS, "x")) == {}

    def test_keeps_everything_else(self) -> None:
        params = {"temperature": 0.2, "top_k": 40, "chat_template_kwargs": {"a": 1}}
        assert strip_reserved(params) == params
