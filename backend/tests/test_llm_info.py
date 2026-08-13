"""`app.services.llm_info` — extraction and probe merging only.

No database, no real network: `httpx.MockTransport` stands in for the machine's
endpoint, keeping this next to `test_discovery.py` in the fast suite. The
extractors are the half worth pinning down — every server this app cares about
puts its metadata somewhere else.
"""

from __future__ import annotations

import httpx

from app.services.llm_info import (
    api_root,
    extract_lmstudio_model_details,
    extract_model_entry_details,
    extract_ollama_show_details,
    parse_llm_info,
    probe_llm_info,
    serialize_llm_info,
)


def test_api_root_strips_the_v1_suffix_and_trailing_slashes():
    assert api_root("http://host:8000/v1") == "http://host:8000"
    assert api_root("http://host:8000/v1/") == "http://host:8000"
    assert api_root("http://host:8000/V1") == "http://host:8000"
    assert api_root("http://host:11434") == "http://host:11434"
    # Only a trailing `/v1` goes: a path-mounted server keeps its prefix.
    assert api_root("http://host/openai/v1") == "http://host/openai"


def test_model_entry_details_takes_the_matching_entry_and_drops_non_scalars():
    payload = {
        "data": [
            {"id": "other", "max_model_len": 1},
            {
                "id": "qwen3-32b",
                "object": "model",
                "created": 1,
                "permission": [{"id": "x"}],
                "max_model_len": 32768,
                "root": "qwen3-32b",
                "enabled": True,
                "nested": {"a": 1},
            },
        ]
    }

    assert extract_model_entry_details(payload, "qwen3-32b") == {
        "max_model_len": "32768",
        "root": "qwen3-32b",
        "enabled": "true",
    }


def test_model_entry_details_degrades_on_an_unexpected_payload():
    assert extract_model_entry_details(None, "qwen3-32b") == {}
    assert extract_model_entry_details({"data": "nope"}, "qwen3-32b") == {}
    assert extract_model_entry_details({"data": []}, "qwen3-32b") == {}


def test_ollama_show_details_flattens_the_three_interesting_blocks():
    payload = {
        "details": {"family": "llama", "quantization_level": "Q4_K_M"},
        "model_info": {
            "general.architecture": "llama",
            "llama.context_length": 8192,
            "llama.attention.head_count": 32,
        },
        "capabilities": ["completion", "tools", 7],
    }

    assert extract_ollama_show_details(payload) == {
        "family": "llama",
        "quantization_level": "Q4_K_M",
        "architecture": "llama",
        "context_length": "8192",
        "capabilities": "completion, tools",
    }


def test_lmstudio_details_skip_the_identifying_fields():
    payload = {
        "id": "qwen3-32b",
        "object": "model",
        "loaded_context_length": 4096,
        "arch": "qwen3",
        "quantization": "Q4_K_M",
    }

    assert extract_lmstudio_model_details(payload) == {
        "arch": "qwen3",
        "quantization": "Q4_K_M",
    }


def _transport(routes: dict[str, httpx.Response]) -> httpx.MockTransport:
    """Answers the listed paths and 404s everything else — which is exactly
    what a server that is not the probed kind does.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        response = routes.get(request.url.path)
        return response if response is not None else httpx.Response(404)

    return httpx.MockTransport(handler)


async def test_a_vllm_endpoint_is_identified_and_its_model_entry_merged():
    info = await probe_llm_info(
        "http://host:8000/v1",
        None,
        "qwen3-32b",
        transport=_transport(
            {
                "/v1/models": httpx.Response(
                    200, json={"data": [{"id": "qwen3-32b", "max_model_len": 32768}]}
                ),
                "/version": httpx.Response(200, json={"version": "0.8.5"}),
            }
        ),
    )

    assert info is not None
    assert info.server == "vLLM"
    assert info.version == "0.8.5"
    assert info.details == {"max_model_len": "32768"}


async def test_ollama_wins_over_vllm_and_contributes_its_show_payload():
    info = await probe_llm_info(
        "http://host:11434/v1",
        None,
        "llama3",
        transport=_transport(
            {
                "/api/version": httpx.Response(200, json={"version": "0.5.1"}),
                "/api/show": httpx.Response(200, json={"details": {"family": "llama"}}),
            }
        ),
    )

    assert info is not None
    assert info.server == "Ollama"
    assert info.version == "0.5.1"
    assert info.details == {"family": "llama"}


async def test_the_api_key_is_sent_as_a_bearer_token():
    seen: set[str | None] = set()

    def handler(request: httpx.Request) -> httpx.Response:
        seen.add(request.headers.get("authorization"))
        return httpx.Response(404)

    await probe_llm_info(
        "http://host/v1", "s3cret", "m", transport=httpx.MockTransport(handler)
    )

    assert seen == {"Bearer s3cret"}


async def test_a_silent_endpoint_yields_no_snapshot_at_all():
    info = await probe_llm_info(
        "http://host/v1", None, "m", transport=_transport({})
    )
    assert info is None


async def test_an_unreachable_endpoint_never_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    assert (
        await probe_llm_info(
            "http://host/v1", None, "m", transport=httpx.MockTransport(handler)
        )
        is None
    )


async def test_the_snapshot_round_trips_through_the_column():
    info = await probe_llm_info(
        "http://host/v1",
        None,
        "m",
        transport=_transport({"/version": httpx.Response(200, json={"version": "1.2"})}),
    )

    assert parse_llm_info(serialize_llm_info(info)) == info


def test_a_malformed_snapshot_reads_as_nothing_rather_than_raising():
    assert parse_llm_info(None) is None
    assert parse_llm_info("") is None
    assert parse_llm_info("{not json") is None
    assert parse_llm_info("[]") is None

    # Anything non-string inside `details` is dropped, not coerced.
    info = parse_llm_info('{"server": 7, "version": null, "details": {"a": "1", "b": 2}}')
    assert info is not None
    assert info.server is None
    assert info.version is None
    assert info.details == {"a": "1"}
