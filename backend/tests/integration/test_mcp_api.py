"""`/mcp` end to end: real app, real Postgres, real JSON-RPC over HTTP.

The pure half of the MCP server (reference resolution, workspace precedence,
the registry and the read-only gate) lives in `tests/test_mcp.py`. What only a
database can show is here: that a tool call really lands in the workspace the
call named, that a name can never resolve across workspaces, and that the
refusals the authoring tools share with the UI (no changes to commit, a tool
test with no tools, a rating on a row that has not answered) fire through the
wire format a client actually sees.

**One test function on purpose.** The SDK's streamable-HTTP session manager
refuses a second `run()` per instance, and the app's MCP endpoint is one
module-level instance — so the app lifespan can be entered exactly once per
test process. It is entered *inside* the test rather than from a fixture for
the same reason a task group has to be exited by the task that entered it:
pytest-asyncio finalizes a fixture in a different task, which anyio rejects.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from httpx import ASGITransport, AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import users as user_store
from app.auth.passwords import hash_password
from app.auth.policy import Role
from app.auth.tokens import create_token
from app.main import app
from app.models import Customer
from app.models import TestCase as CaseModel
from app.repos.customers import create_customer
from app.repos.endpoints import create_endpoint
from app.repos.param_groups import create_param_group
from app.repos.runs import list_run_results
from app.repos.test_cases import create_test_group
from app.repos.toolsets import create_tool, create_toolset
from app.scope import Scope, scope_from_row

CreateWorkspace = Callable[[str], Awaitable[tuple[int, Scope]]]

PASSWORD = "correct horse battery staple"

#: Nothing listens on port 1, so the endpoint probe run creation makes fails
#: at connection level immediately and simply learns nothing.
DEAD_ENDPOINT = "http://127.0.0.1:1/v1"

#: A streamable-HTTP client has to declare it can read either answer shape.
MCP_HEADERS = {
    "content-type": "application/json",
    "accept": "application/json, text/event-stream",
}


async def make_token(session: AsyncSession, email: str, role: Role) -> str:
    user = await user_store.create_user(
        session, email=email, name=email, password_hash=hash_password(PASSWORD), role=role
    )
    _, raw = await create_token(session, user_id=user.id, name="mcp", expires_at=None)
    await session.commit()
    return raw


async def rpc(
    client: AsyncClient,
    token: str,
    payload: dict[str, Any],
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    response = await client.post(
        "/mcp",
        json=payload,
        headers={**MCP_HEADERS, "x-api-key": token, **(headers or {})},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def call(
    client: AsyncClient,
    token: str,
    name: str,
    arguments: dict[str, Any],
    headers: dict[str, str] | None = None,
) -> tuple[bool, Any]:
    """Calls one tool, returning `(is_error, payload)`.

    A refusal is `isError` *content*, never a JSON-RPC error, so the payload is
    the message the calling model reads; a success is the JSON the tool built.
    """
    reply = await rpc(
        client,
        token,
        {
            "jsonrpc": "2.0",
            "id": 99,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
        headers,
    )
    result = reply["result"]
    text = result["content"][0]["text"]
    if result.get("isError"):
        return True, text
    return False, json.loads(text)


async def test_mcp_endpoint(
    session: AsyncSession, create_workspace: CreateWorkspace
) -> None:
    acme_id, acme = await create_workspace("Acme")
    _, globex = await create_workspace("Globex")
    token = await make_token(session, "admin@example.com", "admin")
    viewer = await make_token(session, "viewer@example.com", "viewer")

    # A byte-identical group name in both workspaces: what name resolution
    # must never cross.
    await create_test_group(globex, session, name="Invoices")
    await create_endpoint(
        acme,
        session,
        name="Spark",
        base_url=DEAD_ENDPOINT,
        api_key=None,
        platform="vllm",
        default_params=json.dumps({"temperature": 0.1, "top_k": 40}),
    )
    await session.commit()

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            # --- auth -------------------------------------------------------
            anonymous = await client.post(
                "/mcp",
                json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                headers=MCP_HEADERS,
            )
            assert anonymous.status_code == 401
            assert anonymous.headers["www-authenticate"].startswith("Bearer")

            bearer = await client.post(
                "/mcp",
                json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                headers={**MCP_HEADERS, "authorization": f"Bearer {token}"},
            )
            assert bearer.status_code == 200

            # --- handshake --------------------------------------------------
            init = await rpc(
                client,
                token,
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "clientInfo": {"name": "test", "version": "0"},
                    },
                },
            )
            assert init["result"]["serverInfo"]["name"] == "promptrack"

            listed = await rpc(client, token, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
            # The registry is asserted exhaustively in `tests/test_mcp.py`; what
            # the wire adds is that every registered tool really reaches a client,
            # with the `readOnlyHint` the `_WRITES` declaration derives.
            advertised = {tool["name"]: tool for tool in listed["result"]["tools"]}
            assert len(advertised) == 31
            assert advertised["list_toolsets"]["annotations"]["readOnlyHint"] is True
            assert advertised["delete_tool"]["annotations"]["destructiveHint"] is True
            assert advertised["list_param_groups"]["annotations"]["readOnlyHint"] is True
            assert advertised["list_documents"]["annotations"]["readOnlyHint"] is True
            assert advertised["create_document"]["annotations"]["readOnlyHint"] is False
            assert advertised["update_document"]["annotations"]["readOnlyHint"] is False

            # --- which workspace a call runs in -----------------------------
            failed, message = await call(client, token, "list_test_groups", {})
            assert failed
            # Names both ways of supplying it, and what exists.
            assert '"customer"' in message and "Acme (1)" in message

            failed, payload = await call(
                client, token, "create_test_group", {"name": "Invoices"}, {"x-customer": "Acme"}
            )
            assert not failed
            assert payload["created"] is True
            acme_group_id = payload["group"]["id"]

            # Name-idempotent, so pushing the same suite twice cannot duplicate it.
            failed, payload = await call(
                client,
                token,
                "create_test_group",
                {"customer": str(acme_id), "name": "invoices"},
            )
            assert not failed
            assert payload["created"] is False
            assert payload["group"]["id"] == acme_group_id

            # The argument wins over the header, and the header's workspace is
            # never consulted for the row.
            failed, payload = await call(
                client, token, "list_test_groups", {"customer": "Globex"}, {"x-customer": "Acme"}
            )
            assert not failed
            globex_group_ids = [group["id"] for group in payload["groups"]]
            assert acme_group_id not in globex_group_ids

            # A foreign id is refused the same way a missing one is.
            failed, message = await call(
                client,
                token,
                "create_test_case",
                {
                    "customer": "Acme",
                    "group": globex_group_ids[0],
                    "title": "x",
                    "content": "y",
                },
            )
            assert failed
            assert f"No test group with id {globex_group_ids[0]}" in message

            # --- prompts and their history ----------------------------------
            failed, payload = await call(
                client,
                token,
                "create_prompt",
                {"customer": "Acme", "name": "Agent", "content": "You are helpful."},
            )
            assert not failed
            # Nothing committed yet, so the draft is a dirty working tree.
            assert payload["prompt"]["dirty"] is True
            assert payload["prompt"]["head_version"] is None
            # Omitting `kind` sends the prompt on the channel everything was
            # authored on before the pivot.
            assert payload["prompt"]["kind"] == "system"

            # The other channel, named explicitly.
            failed, payload = await call(
                client,
                token,
                "create_prompt",
                {
                    "customer": "Acme",
                    "name": "Judge",
                    "content": "Pick the matching PO.",
                    "kind": "task",
                },
            )
            assert not failed
            assert payload["prompt"]["kind"] == "task"

            # An unrecognised kind is refused, never coerced: guessing a
            # channel would silently move the text between messages.
            failed, message = await call(
                client,
                token,
                "create_prompt",
                {"customer": "Acme", "name": "Nope", "content": "x", "kind": "user"},
            )
            assert failed
            assert "kind" in message

            failed, payload = await call(client, token, "list_prompts", {"customer": "Acme"})
            assert not failed
            assert {p["name"]: p["kind"] for p in payload["prompts"]} == {
                "Agent": "system",
                "Judge": "task",
            }

            failed, payload = await call(
                client,
                token,
                "commit_prompt",
                {"customer": "Acme", "prompt": "Agent", "message": "first"},
            )
            assert not failed
            version_id = payload["version"]["id"]
            assert payload["version"]["version"] == 1

            failed, message = await call(
                client,
                token,
                "commit_prompt",
                {"customer": "Acme", "prompt": "Agent", "message": "again"},
            )
            assert failed
            assert "nothing to commit" in message

            failed, payload = await call(client, token, "list_prompts", {"customer": "Acme"})
            assert not failed
            agent = next(p for p in payload["prompts"] if p["name"] == "Agent")
            assert agent["dirty"] is False
            assert agent["head_version"] == {"id": version_id, "version": 1}

            failed, payload = await call(
                client, token, "get_prompt_version", {"customer": "Acme", "version_id": version_id}
            )
            assert not failed
            assert payload["version"]["content"] == "You are helpful."

            # Editing the draft makes it dirty again and leaves the version
            # alone — a commit is the only thing that freezes text.
            failed, payload = await call(
                client,
                token,
                "update_prompt",
                {"customer": "Acme", "prompt": "Agent", "content": "You are very helpful."},
            )
            assert not failed
            assert payload["prompt"]["content"] == "You are very helpful."
            assert payload["prompt"]["dirty"] is True
            assert payload["prompt"]["head_version"] == {"id": version_id, "version": 1}

            # Back to the committed text: the working tree is clean again,
            # which is what makes the run below attributable to v1.
            failed, payload = await call(
                client,
                token,
                "update_prompt",
                {"customer": "Acme", "prompt": "Agent", "content": "You are helpful."},
            )
            assert not failed
            assert payload["prompt"]["dirty"] is False

            # A version of another workspace's prompt does not exist here.
            failed, message = await call(
                client,
                token,
                "get_prompt_version",
                {"customer": "Globex", "version_id": version_id},
            )
            assert failed

            # --- test cases -------------------------------------------------
            # Both slots by *name*: the two new `RowRef` arguments that
            # replaced `prompt` / `mode` / `custom_text`.
            failed, payload = await call(
                client,
                token,
                "create_test_case",
                {
                    "customer": "Acme",
                    "group": "Invoices",
                    "title": "Reconcile",
                    "content": "Invoice 4711 has a quantity mismatch.",
                    "expected_output": "asks a question",
                    "system_prompt": "Agent",
                    "task_prompt": "Judge",
                },
            )
            assert not failed
            case = payload["test_case"]
            assert case["system_prompt"]["name"] == "Agent"
            assert case["task_prompt"]["name"] == "Judge"
            # The two texts each slot currently holds, under the same key names
            # `get_run_result` uses for the frozen copies — one vocabulary for
            # authoring a case and reading its result.
            assert case["system_prompt_text"] == "You are helpful."
            assert case["task_prompt_text"] == "Pick the matching PO."

            # A slot only accepts its own kind, and the refusal says which.
            failed, message = await call(
                client,
                token,
                "create_test_case",
                {
                    "customer": "Acme",
                    "group": "Invoices",
                    "title": "Wrong slot",
                    "content": "x",
                    "system_prompt": "Judge",
                },
            )
            assert failed
            assert "task prompt" in message

            # No task prompt and no content is no user message at all.
            failed, message = await call(
                client,
                token,
                "create_test_case",
                {
                    "customer": "Acme",
                    "group": "Invoices",
                    "title": "Empty",
                    "system_prompt": "Agent",
                },
            )
            assert failed
            assert "no user message" in message

            # A task prompt *is* a user message, so `content` may be omitted.
            failed, payload = await call(
                client,
                token,
                "create_test_case",
                {
                    "customer": "Acme",
                    "group": "Invoices",
                    "title": "No input",
                    "task_prompt": "Judge",
                },
            )
            assert not failed
            no_input_id = payload["test_case"]["id"]
            assert payload["test_case"]["content"] is None

            # Patch semantics: an explicit null clears a slot, an absent key
            # leaves it alone.
            failed, payload = await call(
                client,
                token,
                "update_test_case",
                {"customer": "Acme", "test_case_id": case["id"], "task_prompt": None},
            )
            assert not failed
            assert payload["test_case"]["task_prompt"] is None
            assert payload["test_case"]["task_prompt_text"] is None
            assert payload["test_case"]["system_prompt"]["name"] == "Agent"
            assert payload["test_case"]["expected_output"] == "asks a question"

            # The merged post-patch state is what the guard reads: clearing the
            # only user message this case has must not be saveable.
            failed, message = await call(
                client,
                token,
                "update_test_case",
                {"customer": "Acme", "test_case_id": no_input_id, "task_prompt": None},
            )
            assert failed
            assert "no user message" in message

            # A prompt cannot change channel while a case references it: that
            # would move its text to the other message behind those cases'
            # backs. Nothing is written — the kind is still "system" below.
            failed, message = await call(
                client,
                token,
                "update_prompt",
                {"customer": "Acme", "prompt": "Agent", "kind": "task"},
            )
            assert failed
            assert "Agent" in message

            failed, payload = await call(
                client, token, "list_prompts", {"customer": "Acme"}
            )
            assert not failed
            assert next(p for p in payload["prompts"] if p["name"] == "Agent")["kind"] == (
                "system"
            )

            # Commit the task prompt too, so the run below can attribute a
            # version through each of the two columns.
            failed, payload = await call(
                client,
                token,
                "commit_prompt",
                {"customer": "Acme", "prompt": "Judge", "message": "first"},
            )
            assert not failed
            task_version_id = payload["version"]["id"]

            # The test-case editor's own rule, through the same function.
            failed, message = await call(
                client,
                token,
                "update_test_case",
                {"customer": "Acme", "test_case_id": case["id"], "tool_mode": "execute"},
            )
            assert failed
            assert "no enabled tools" in message

            # A miss reports what exists — the discovery path for a guess.
            failed, message = await call(
                client,
                token,
                "create_test_case",
                {"customer": "Acme", "group": "Nope", "title": "x", "content": "y"},
            )
            assert failed
            assert "Known: Invoices" in message

            # --- runs -------------------------------------------------------
            failed, payload = await call(
                client,
                token,
                "create_run",
                {
                    "customer": "Acme",
                    "endpoint": "Spark",
                    "model": "qwen3:8b",
                    "groups": ["Invoices"],
                    "comment": "over mcp",
                },
            )
            assert not failed
            run_id = payload["run"]["id"]
            assert payload["run"]["test_case_count"] == 2
            assert payload["executing"] is False

            # Id-addressed calls need no workspace at all: a run id is globally
            # unique, so the row resolves its own.
            failed, payload = await call(client, token, "get_run", {"run_id": run_id})
            assert not failed
            assert payload["run"]["status"] == "pending"
            assert payload["run"]["results"]["pending"] == 2
            assert all("expected_output" in row for row in payload["results"])

            # A grading work list drops the rubrics — absent, not null, since
            # null already means "this case has no rubric".
            failed, lean = await call(
                client, token, "get_run", {"run_id": run_id, "include_rubrics": False}
            )
            assert not failed
            assert all("expected_output" not in row for row in lean["results"])
            assert [row["title"] for row in lean["results"]] == [
                row["title"] for row in payload["results"]
            ]
            reconcile, no_input = payload["results"]
            result_id = reconcile["result_id"]
            # A run of a committed draft records which version it tested, per
            # slot: "Reconcile" has only a system prompt left, "No input" only
            # a task prompt, and each fills exactly its own column.
            assert reconcile["system_prompt_version_id"] == version_id
            assert reconcile["task_prompt_version_id"] is None
            assert no_input["system_prompt_version_id"] is None
            assert no_input["task_prompt_version_id"] == task_version_id

            # The frozen texts come back under the same two key names, and
            # `test_case_text` is null for the case that carries no data.
            failed, payload = await call(
                client,
                token,
                "get_run_result",
                {"result_id": no_input["result_id"]},
            )
            assert not failed
            assert payload["result"]["system_prompt_text"] is None
            assert payload["result"]["task_prompt_text"] == "Pick the matching PO."
            assert payload["result"]["test_case_text"] is None
            assert "prompt_text" not in payload["result"]

            failed, payload = await call(client, token, "list_runs", {"customer": "Acme"})
            assert not failed
            assert [run["id"] for run in payload["runs"]] == [run_id]
            assert payload["runs"][0]["results"]["total"] == 2

            # A row that has not answered yet cannot be judged: `execute_run`
            # is fire-and-forget, so a grading loop can outrun it.
            failed, message = await call(
                client,
                token,
                "set_rating",
                {"result_id": result_id, "rating": "good"},
            )
            assert failed
            assert "still pending" in message

            # --- rating provenance ------------------------------------------
            # One row finished by hand: what is under test is who a verdict is
            # attributed to, not the executor that produced the answer.
            results = await list_run_results(acme, session, run_id)
            results[0].status = "ok"
            results[0].response_text = "PO-4711"
            await session.commit()

            failed, payload = await call(
                client,
                token,
                "set_rating",
                {
                    "customer": "Acme",
                    "result_id": result_id,
                    "rating": "good",
                    "note": "canary present",
                },
            )
            assert not failed
            assert payload["result"]["rating"] == "good"
            # An API token judged this, and the run detail view badges it as
            # such until a human re-rates the row.
            assert payload["result"]["rated_via"] == "token"

            # The stored column rather than the payload alone: the badge reads
            # the column, so a view field derived from something else would
            # still pass here.
            session.expire_all()
            assert (await list_run_results(acme, session, run_id))[0].rated_via == "token"

            failed, payload = await call(
                client, token, "get_run", {"customer": "Acme", "run_id": run_id}
            )
            assert not failed
            graded = payload["results"][0]
            assert graded["rated_via"] == "token"
            # A one-shot row offers a grading loop no tool names to check, so
            # the key is absent rather than an empty list claiming it called
            # nothing.
            assert "tools_called" not in graded

            # A workspace that contradicts the run's own is refused naming the
            # actual one — a named refusal rather than a lying "not found",
            # since workspaces are labels, not tenants.
            failed, message = await call(
                client, token, "get_run", {"customer": "Globex", "run_id": run_id}
            )
            assert failed
            assert f'Run {run_id} belongs to the workspace "Acme"' in message

            # The same contradiction via the pinned connection header, and the
            # explicit argument is the documented way past it.
            failed, message = await call(
                client, token, "get_run", {"run_id": run_id}, {"x-customer": "Globex"}
            )
            assert failed
            assert "belongs to the workspace" in message
            failed, _ = await call(
                client,
                token,
                "get_run",
                {"customer": "Acme", "run_id": run_id},
                {"x-customer": "Globex"},
            )
            assert not failed

            # A missing row still reads as missing, not as a workspace problem.
            failed, message = await call(client, token, "get_run", {"run_id": 999999})
            assert failed
            assert "No run with id 999999" in message

            # --- the read-only gate ------------------------------------------
            failed, message = await call(
                client, viewer, "create_test_group", {"customer": "Acme", "name": "Nope"}
            )
            assert failed
            assert "read-only" in message

            failed, payload = await call(client, viewer, "list_test_groups", {"customer": "Acme"})
            assert not failed
            assert payload["groups"]

            # --- list_endpoints surfaces platform + default_params -----------
            failed, payload = await call(client, token, "list_endpoints", {"customer": "Acme"})
            assert not failed
            spark = next(e for e in payload["endpoints"] if e["name"] == "Spark")
            assert spark["platform"] == "vllm"
            assert spark["default_params"] == {"temperature": 0.1, "top_k": 40}

            # --- create_run params: merged with the endpoint's defaults ------
            failed, payload = await call(
                client,
                token,
                "create_run",
                {
                    "customer": "Acme",
                    "endpoint": "Spark",
                    "model": "qwen3:8b",
                    "groups": ["Invoices"],
                    "params": {"temperature": 0.4, "top_k": None, "seed": 7},
                },
            )
            assert not failed
            params_run_id = payload["run"]["id"]

            failed, payload = await call(
                client, token, "get_run", {"customer": "Acme", "run_id": params_run_id}
            )
            assert not failed
            # The run's own params win over the endpoint default (temperature),
            # a null unsets a default (top_k) rather than sending one, and a
            # brand-new key merges in untouched (seed).
            assert payload["run"]["params"] == {"temperature": 0.4, "seed": 7}

            # Naming the same key both as the shorthand argument and inside
            # params is refused rather than picking one silently.
            failed, message = await call(
                client,
                token,
                "create_run",
                {
                    "customer": "Acme",
                    "endpoint": "Spark",
                    "model": "qwen3:8b",
                    "groups": ["Invoices"],
                    "temperature": 0.5,
                    "params": {"temperature": 0.2},
                },
            )
            assert failed
            assert '"temperature"' in message and "params" in message

            # The named shorthand alone still works, merging with the
            # endpoint's own defaults exactly like a "params" override would.
            failed, payload = await call(
                client,
                token,
                "create_run",
                {
                    "customer": "Acme",
                    "endpoint": "Spark",
                    "model": "qwen3:8b",
                    "groups": ["Invoices"],
                    "max_tokens": 256,
                },
            )
            assert not failed
            named_run_id = payload["run"]["id"]

            failed, payload = await call(
                client, token, "get_run", {"customer": "Acme", "run_id": named_run_id}
            )
            assert not failed
            assert payload["run"]["params"] == {
                "temperature": 0.1,
                "top_k": 40,
                "max_tokens": 256,
            }

            # A reserved key inside params is refused by name, same as it
            # would be through the API or the UI.
            failed, message = await call(
                client,
                token,
                "create_run",
                {
                    "customer": "Acme",
                    "endpoint": "Spark",
                    "model": "qwen3:8b",
                    "groups": ["Invoices"],
                    "params": {"messages": []},
                },
            )
            assert failed
            assert '"messages"' in message

            # --- parameter groups: the third merge level, by name -------------
            # The group itself is seeded through the repo — like endpoints and
            # toolsets it is not creatable over MCP in v1, only listed and used.
            await create_param_group(
                acme,
                session,
                name="no thinking",
                params=json.dumps(
                    {"top_k": None, "chat_template_kwargs": {"enable_thinking": False}}
                ),
            )
            await session.commit()

            failed, payload = await call(
                client, viewer, "list_param_groups", {"customer": "Acme"}
            )
            assert not failed
            assert payload["param_groups"] == [
                {
                    "id": payload["param_groups"][0]["id"],
                    "name": "no thinking",
                    "description": None,
                    "params": {
                        "top_k": None,
                        "chat_template_kwargs": {"enable_thinking": False},
                    },
                }
            ]

            failed, payload = await call(
                client,
                token,
                "create_run",
                {
                    "customer": "Acme",
                    "endpoint": "Spark",
                    "model": "qwen3:8b",
                    "groups": ["Invoices"],
                    "param_groups": ["no thinking"],
                    "params": {"seed": 3},
                },
            )
            assert not failed
            grouped_run_id = payload["run"]["id"]

            failed, payload = await call(
                client, token, "get_run", {"customer": "Acme", "run_id": grouped_run_id}
            )
            assert not failed
            # The group unset the endpoint's top_k with a null, added its own
            # nested key, and the run's own params still merged on top.
            assert payload["run"]["params"] == {
                "temperature": 0.1,
                "chat_template_kwargs": {"enable_thinking": False},
                "seed": 3,
            }
            assert payload["run"]["param_groups"] == ["no thinking"]

            # An unknown group name is refused with the discovery list, same
            # as every other ref miss.
            failed, message = await call(
                client,
                token,
                "create_run",
                {
                    "customer": "Acme",
                    "endpoint": "Spark",
                    "model": "qwen3:8b",
                    "groups": ["Invoices"],
                    "param_groups": ["nope"],
                },
            )
            assert failed
            assert "no thinking" in message

            await _manual_toolsets_over_mcp(client, session, token, viewer, acme)
            await _history_and_overrides_over_mcp(client, token)
            await _json_looking_text_over_mcp(client, token)
            await _overridden_tools_over_mcp(client, session, token)


#: Free text that the SDK's `pre_parse_json` would turn into something else:
#: an object, an array, and the literal word null.
JSON_LOOKING_TEXTS = ('{"invoice": 1}', '["a"]', "null")


async def _json_looking_text_over_mcp(client: AsyncClient, token: str) -> None:
    """Free-text arguments arrive as the exact strings sent.

    The SDK `json.loads` every string argument not annotated as plain `str`, so
    under `str | None` an extraction suite's JSON content was refused ("Input
    should be a valid string") and the text "null" silently became None. Every
    optional free-text argument is read raw instead (`_raw_text`).
    """
    failed, payload = await call(
        client, token, "create_test_group", {"customer": "Acme", "name": "Extraction"}
    )
    assert not failed, payload

    for text in JSON_LOOKING_TEXTS:
        failed, payload = await call(
            client,
            token,
            "create_test_case",
            {
                "customer": "Acme",
                "group": "Extraction",
                "title": f"Verbatim {text}",
                "content": text,
                "expected_output": text,
            },
        )
        assert not failed, (text, payload)
        case = payload["test_case"]
        assert (case["content"], case["expected_output"]) == (text, text)

        failed, payload = await call(
            client,
            token,
            "update_test_case",
            {"customer": "Acme", "test_case_id": case["id"], "content": f"{text}", "title": "t"},
        )
        assert not failed, (text, payload)
        assert payload["test_case"]["content"] == text

        failed, payload = await call(
            client,
            token,
            "update_test_case",
            {"customer": "Acme", "test_case_id": case["id"], "expected_output": text},
        )
        assert not failed, (text, payload)
        assert payload["test_case"]["expected_output"] == text

    # An explicit JSON null still clears, and a non-string is refused by name.
    failed, payload = await call(
        client,
        token,
        "update_test_case",
        {"customer": "Acme", "test_case_id": case["id"], "expected_output": None},
    )
    assert not failed, payload
    assert payload["test_case"]["expected_output"] is None
    failed, message = await call(
        client,
        token,
        "update_test_case",
        {"customer": "Acme", "test_case_id": case["id"], "content": {"invoice": 1}},
    )
    assert failed
    assert '"content" must be a string' in message

    # Tool and toolset descriptions.
    for index, text in enumerate(JSON_LOOKING_TEXTS):
        failed, payload = await call(
            client,
            token,
            "create_toolset",
            {"customer": "Acme", "name": f"Verbatim set {index}", "description": text},
        )
        assert not failed, (text, payload)
        assert payload["toolset"]["description"] == text
        failed, payload = await call(
            client,
            token,
            "update_toolset",
            {"customer": "Acme", "toolset": f"Verbatim set {index}", "description": text},
        )
        assert not failed, (text, payload)
        assert payload["toolset"]["description"] == text
        failed, payload = await call(
            client,
            token,
            "create_tool",
            {
                "customer": "Acme",
                "toolset": f"Verbatim set {index}",
                "name": "echo",
                "description": text,
            },
        )
        assert not failed, (text, payload)
        failed, payload = await call(
            client,
            token,
            "update_tool",
            {
                "customer": "Acme",
                "toolset": f"Verbatim set {index}",
                "tool": "echo",
                "description": text,
            },
        )
        assert not failed, (text, payload)
        failed, payload = await call(
            client,
            token,
            "get_tool",
            {"customer": "Acme", "toolset": f"Verbatim set {index}", "tool": "echo"},
        )
        assert payload["tool"]["description"] == text

    # Prompt drafts and document titles/markdown.
    for index, text in enumerate(JSON_LOOKING_TEXTS):
        failed, payload = await call(
            client,
            token,
            "create_prompt",
            {"customer": "Acme", "name": f"Verbatim prompt {index}", "content": "draft"},
        )
        assert not failed, payload
        failed, payload = await call(
            client,
            token,
            "update_prompt",
            {"customer": "Acme", "prompt": f"Verbatim prompt {index}", "content": text},
        )
        assert not failed, (text, payload)
        assert payload["prompt"]["content"] == text

        failed, payload = await call(
            client,
            token,
            "create_document",
            {
                "customer": "Acme",
                "toolset": "Handbook",
                "path": f"verbatim-{index}.md",
                "content": "# Heading\n\nBody.",
                "title": text,
            },
        )
        assert not failed, (text, payload)
        assert payload["document"]["title"] == text
        failed, payload = await call(
            client,
            token,
            "update_document",
            {
                "customer": "Acme",
                "toolset": "Handbook",
                "document": f"verbatim-{index}.md",
                "content": text,
                "title": text,
            },
        )
        assert not failed, (text, payload)
        assert payload["document"]["title"] == text
        assert payload["document"]["chars"] == len(text)


async def _overridden_tools_over_mcp(
    client: AsyncClient, session: AsyncSession, token: str
) -> None:
    """A tool test cases override by name cannot be renamed, disabled or
    deleted out from under them, and `mock_responses` is `mock_response`'s alias.
    """
    failed, payload = await call(
        client, token, "create_toolset", {"customer": "Acme", "name": "Override guard"}
    )
    assert not failed, payload
    # The alias, under the name get_tool reads it back as.
    failed, payload = await call(
        client,
        token,
        "create_tool",
        {
            "customer": "Acme",
            "toolset": "Override guard",
            "name": "fetch_page",
            "mock_responses": ["page 1", "page 2"],
        },
    )
    assert not failed, payload
    assert payload["tool"]["mock_response_count"] == 2
    failed, message = await call(
        client,
        token,
        "create_tool",
        {
            "customer": "Acme",
            "toolset": "Override guard",
            "name": "both",
            "mock_response": "a",
            "mock_responses": ["b"],
        },
    )
    assert failed
    assert "not both" in message
    failed, payload = await call(
        client,
        token,
        "update_tool",
        {
            "customer": "Acme",
            "toolset": "Override guard",
            "tool": "fetch_page",
            "mock_responses": '{"page": 3}',
        },
    )
    assert not failed, payload
    assert payload["changed"] == ["mock_response"]
    failed, payload = await call(
        client,
        token,
        "get_tool",
        {"customer": "Acme", "toolset": "Override guard", "tool": "fetch_page"},
    )
    assert payload["tool"]["mock_responses"] == ['{"page": 3}']
    failed, message = await call(
        client,
        token,
        "update_tool",
        {
            "customer": "Acme",
            "toolset": "Override guard",
            "tool": "fetch_page",
            "mock_response": "a",
            "mock_responses": "b",
        },
    )
    assert failed
    assert "not both" in message

    failed, payload = await call(
        client,
        token,
        "create_test_case",
        {
            "customer": "Acme",
            "group": "Extraction",
            "title": "Paged",
            "content": "Read every page.",
            "tool_mode": "execute",
            "toolsets": ["Override guard"],
            "mock_overrides": {"fetch_page": ["only page"]},
        },
    )
    assert not failed, payload
    case_id = payload["test_case"]["id"]

    for arguments in (
        {"tool": "fetch_page", "name": "get_page"},
        {"tool": "fetch_page", "enabled": False},
    ):
        failed, message = await call(
            client,
            token,
            "update_tool",
            {"customer": "Acme", "toolset": "Override guard", **arguments},
        )
        assert failed, arguments
        assert '1 test case overrides the canned responses of "fetch_page" (1 in Acme)' in message
        assert "Remove that override" in message
    failed, message = await call(
        client,
        token,
        "delete_tool",
        {"customer": "Acme", "toolset": "Override guard", "tool": "fetch_page"},
    )
    assert failed
    assert "before deleting the tool" in message

    # A stale override that got in some other way does not block unrelated
    # edits of the case; touching the tool configuration re-validates it.
    await session.execute(
        update(CaseModel)
        .where(CaseModel.id == case_id)
        .values(mock_overrides=json.dumps({"gone_tool": ["x"]}))
    )
    await session.commit()
    failed, payload = await call(
        client,
        token,
        "update_test_case",
        {"customer": "Acme", "test_case_id": case_id, "title": "Paged (renamed)"},
    )
    assert not failed, payload
    failed, message = await call(
        client,
        token,
        "update_test_case",
        {"customer": "Acme", "test_case_id": case_id, "tool_mode": "definitions"},
    )
    assert failed
    assert 'overrides the canned response of "gone_tool"' in message

    # With the override gone, the tool is free to change.
    failed, payload = await call(
        client,
        token,
        "update_test_case",
        {"customer": "Acme", "test_case_id": case_id, "mock_overrides": None},
    )
    assert not failed, payload
    failed, payload = await call(
        client,
        token,
        "update_tool",
        {"customer": "Acme", "toolset": "Override guard", "tool": "fetch_page", "name": "get_page"},
    )
    assert not failed, payload


async def _history_and_overrides_over_mcp(client: AsyncClient, token: str) -> None:
    """`history` and `mock_overrides` on create/update_test_case, and the
    frozen history on `get_run_result`.

    The JSON-text answer is the point of `MockOverridesArg` being `Any`: an
    override that *is* JSON has to arrive as the string it is, not as the
    object the SDK's pre-parser would make of it.
    """
    history = [
        {"role": "user", "content": "List open orders."},
        {"role": "assistant", "content": '{"orders": [4711, 4712]}'},
    ]
    json_answer = '{"status": "shipped", "items": [1, 2]}'

    failed, payload = await call(
        client, token, "create_toolset", {"customer": "Acme", "name": "Follow-up mocks"}
    )
    assert not failed, payload
    for name in ("lookup_order", "list_orders"):
        failed, payload = await call(
            client,
            token,
            "create_tool",
            {
                "customer": "Acme",
                "toolset": "Follow-up mocks",
                "name": name,
                "mock_response": "SHARED",
            },
        )
        assert not failed, payload
    failed, payload = await call(
        client, token, "create_test_group", {"customer": "Acme", "name": "Follow-ups"}
    )
    assert not failed, payload

    failed, payload = await call(
        client,
        token,
        "create_test_case",
        {
            "customer": "Acme",
            "group": "Follow-ups",
            "title": "Only Germany",
            "content": "Same list, only Germany.",
            "tool_mode": "execute",
            "toolsets": ["Follow-up mocks"],
            "history": history,
            "mock_overrides": {"lookup_order": json_answer, "list_orders": ["p1", "p2"]},
        },
    )
    assert not failed, payload
    case = payload["test_case"]
    assert case["history"] == history
    assert case["mock_overrides"] == {
        "lookup_order": [json_answer],
        "list_orders": ["p1", "p2"],
    }
    case_id = case["id"]

    # Refusals read as sentences naming the case.
    failed, message = await call(
        client,
        token,
        "create_test_case",
        {
            "customer": "Acme",
            "group": "Follow-ups",
            "title": "Dangling",
            "content": "x",
            "history": history[:1],
        },
    )
    assert failed
    assert 'Test case "Dangling": ' in message
    assert 'end with an "assistant"' in message
    failed, message = await call(
        client,
        token,
        "create_test_case",
        {
            "customer": "Acme",
            "group": "Follow-ups",
            "title": "Typo",
            "content": "x",
            "tool_mode": "execute",
            "toolsets": ["Follow-up mocks"],
            "mock_overrides": {"lookup_ordr": "x"},
        },
    )
    assert failed
    assert 'overrides the canned response of "lookup_ordr"' in message
    assert "list_orders, lookup_order" in message
    failed, message = await call(
        client,
        token,
        "create_test_case",
        {
            "customer": "Acme",
            "group": "Follow-ups",
            "title": "Object",
            "content": "x",
            "tool_mode": "execute",
            "toolsets": ["Follow-up mocks"],
            "mock_overrides": {"lookup_order": {"status": "shipped"}},
        },
    )
    assert failed
    assert "pass a JSON answer as a string" in message

    # Presence-patched: an unrelated field leaves both alone ...
    failed, payload = await call(
        client,
        token,
        "update_test_case",
        {"customer": "Acme", "test_case_id": case_id, "title": "Only Germany (DE)"},
    )
    assert not failed, payload
    assert payload["test_case"]["history"] == history
    assert payload["test_case"]["mock_overrides"]["lookup_order"] == [json_answer]
    # ... a string override is a one-element list ...
    failed, payload = await call(
        client,
        token,
        "update_test_case",
        {"customer": "Acme", "test_case_id": case_id, "mock_overrides": {"list_orders": "one"}},
    )
    assert not failed, payload
    assert payload["test_case"]["mock_overrides"] == {"list_orders": ["one"]}
    # ... and null, {} and [] all clear.
    for clearing in (None, {}, []):
        failed, payload = await call(
            client,
            token,
            "update_test_case",
            {"customer": "Acme", "test_case_id": case_id, "mock_overrides": clearing},
        )
        assert not failed, payload
        assert payload["test_case"]["mock_overrides"] == {}
    for clearing in (None, []):
        failed, payload = await call(
            client,
            token,
            "update_test_case",
            {"customer": "Acme", "test_case_id": case_id, "history": clearing},
        )
        assert not failed, payload
        assert payload["test_case"]["history"] == []
    failed, payload = await call(
        client,
        token,
        "update_test_case",
        {"customer": "Acme", "test_case_id": case_id, "history": history},
    )
    assert not failed, payload

    # The frozen copy comes back from the result.
    failed, payload = await call(
        client,
        token,
        "create_run",
        {"customer": "Acme", "endpoint": "Spark", "model": "qwen3:8b", "groups": ["Follow-ups"]},
    )
    assert not failed, payload
    failed, payload = await call(client, token, "get_run", {"run_id": payload["run"]["id"]})
    assert not failed, payload
    [row] = payload["results"]
    failed, payload = await call(client, token, "get_run_result", {"result_id": row["result_id"]})
    assert not failed, payload
    assert payload["result"]["history"] == history


async def _manual_toolsets_over_mcp(
    client: AsyncClient, session: AsyncSession, token: str, viewer: str, acme: Scope
) -> None:
    """The seven toolset tools: manual toolsets are content and writable here,
    every other kind and every borrowed row is refused by name.

    A helper of the one test function rather than a test of its own — see the
    module docstring on why the lifespan can be entered only once.
    """
    base_id, base = await _make_base(session)
    shared = await create_toolset(base, session, name="Shared mocks", is_global=True)
    await create_tool(base, session, shared.id, name="shared_lookup", mock_responses=["{}"])
    await create_toolset(acme, session, name="Odoo live", kind="mcp", mcp_url=DEAD_ENDPOINT)
    await create_toolset(acme, session, name="Handbook", kind="documents")
    await session.commit()
    del base_id

    # --- create_toolset ----------------------------------------------------
    failed, payload = await call(
        client,
        token,
        "create_toolset",
        {"customer": "Acme", "name": "SIMPOS mocks", "description": "  Stand-ins.  "},
    )
    assert not failed, payload
    toolset = payload["toolset"]
    assert (toolset["kind"], toolset["description"], toolset["editable"]) == (
        "manual",
        "Stand-ins.",
        True,
    )

    # A duplicate name — any kind, case-insensitively, shared ones included —
    # would make every later name reference ambiguous.
    for name in ("simpos MOCKS", "Odoo live", "shared mocks"):
        failed, message = await call(
            client, token, "create_toolset", {"customer": "Acme", "name": name}
        )
        assert failed, name
        assert "ambiguous" in message
    failed, message = await call(
        client, token, "create_toolset", {"customer": "Acme", "name": "simpos mocks"}
    )
    assert "update_toolset" in message

    # --- create_tool: a ~60k description and canned answer round-trip -----
    description = ("Looks a SIMPOS record up by its key.\n" * 1700)[:60_000].strip()
    answer = json.dumps({"rows": ["y" * 50] * 1150})
    assert len(answer) > 58_000
    failed, payload = await call(
        client,
        token,
        "create_tool",
        {
            "customer": "Acme",
            "toolset": "SIMPOS mocks",
            "name": "lookup_record",
            "description": description,
            "parameters": {"type": "object", "properties": {"key": {"type": "string"}}},
            "mock_response": answer,
        },
    )
    assert not failed, payload
    created = payload["tool"]
    # Writes echo sizes, never the texts back.
    assert created["description_chars"] == len(description)
    assert created["mock_response_count"] == 1
    assert created["mock_response_chars"] == [len(answer)]
    assert "description" not in created

    failed, payload = await call(
        client,
        token,
        "get_tool",
        {"customer": "Acme", "toolset": toolset["id"], "tool": "lookup_record"},
    )
    assert not failed, payload
    full = payload["tool"]
    assert full["description"] == description
    assert full["mock_responses"] == [answer]
    assert full["parameters"]["properties"]["key"] == {"type": "string"}
    assert full["source"] == "manual"

    # A list answers by call order; a JSON-string schema is accepted too.
    failed, payload = await call(
        client,
        token,
        "create_tool",
        {
            "customer": "Acme",
            "toolset": "SIMPOS mocks",
            "name": "poll_status",
            "parameters": '{"type": "object", "properties": {}}',
            "mock_response": ['{"status": "pending"}', '{"status": "done"}'],
        },
    )
    assert not failed, payload
    assert payload["tool"]["mock_response_count"] == 2

    # The SDK pre-parses JSON-looking strings; a canned answer that is itself a
    # JSON array must still be *one* response, read verbatim.
    failed, payload = await call(
        client,
        token,
        "create_tool",
        {
            "customer": "Acme",
            "toolset": "SIMPOS mocks",
            "name": "list_ids",
            "mock_response": '["a", "b"]',
        },
    )
    assert not failed, payload
    assert payload["tool"]["mock_response_count"] == 1
    failed, payload = await call(
        client,
        token,
        "get_tool",
        {"customer": "Acme", "toolset": "SIMPOS mocks", "tool": "list_ids"},
    )
    assert payload["tool"]["mock_responses"] == ['["a", "b"]']

    # The shared rules, in this door's vocabulary.
    refusals = [
        ({"name": "lookup_record"}, "already has a tool"),
        ({"name": "has space"}, "letters, digits"),
        ({"name": "ok", "parameters": "[1]"}, "JSON object"),
        ({"name": "ok", "mock_response": ["one", " "]}, "is blank"),
        ({"name": "ok", "description": "x" * 200_001}, "200,000"),
        ({"name": "ok", "mock_response": "a\x00b"}, "NUL"),
        ({"name": "ok", "mock_response": {"rate": 1.1}}, "as a string"),
    ]
    for extra, expected in refusals:
        failed, message = await call(
            client,
            token,
            "create_tool",
            {"customer": "Acme", "toolset": "SIMPOS mocks", **extra},
        )
        assert failed, extra
        assert expected in message, (extra, message)

    # --- list_toolsets: every kind, sizes only -----------------------------
    failed, payload = await call(client, viewer, "list_toolsets", {"customer": "Acme"})
    assert not failed, payload
    by_name = {row["name"]: row for row in payload["toolsets"]}
    assert set(by_name) == {"SIMPOS mocks", "Odoo live", "Handbook", "Shared mocks"}
    assert by_name["Handbook"]["kind"] == "documents"
    assert by_name["Handbook"]["tool_count"] == 3
    assert (by_name["Shared mocks"]["is_global"], by_name["Shared mocks"]["editable"]) == (
        True,
        False,
    )
    simpos_tools = {tool["name"]: tool for tool in by_name["SIMPOS mocks"]["tools"]}
    assert simpos_tools["lookup_record"]["description_chars"] == len(description)
    assert simpos_tools["poll_status"]["mock_response_count"] == 2
    assert "description" not in json.dumps(simpos_tools).replace("description_chars", "")

    # --- update_tool: patch by presence ------------------------------------
    failed, payload = await call(
        client,
        token,
        "update_tool",
        {
            "customer": "Acme",
            "toolset": "SIMPOS mocks",
            "tool": "poll_status",
            "description": "Polls a job.",
        },
    )
    assert not failed, payload
    assert payload["changed"] == ["description"]
    # Untouched fields stay as they were.
    assert payload["tool"]["mock_response_count"] == 2

    failed, payload = await call(
        client,
        token,
        "update_tool",
        {
            "customer": "Acme",
            "toolset": "SIMPOS mocks",
            "tool": "poll_status",
            "description": None,
            "mock_response": None,
            "enabled": False,
        },
    )
    assert not failed, payload
    assert payload["changed"] == ["description", "enabled", "mock_response"]
    failed, payload = await call(
        client,
        token,
        "get_tool",
        {"customer": "Acme", "toolset": "SIMPOS mocks", "tool": "poll_status"},
    )
    assert (payload["tool"]["description"], payload["tool"]["mock_responses"]) == (None, [])
    assert payload["tool"]["enabled"] is False

    failed, message = await call(
        client,
        token,
        "update_tool",
        {
            "customer": "Acme",
            "toolset": "SIMPOS mocks",
            "tool": "poll_status",
            "name": "lookup_record",
        },
    )
    assert failed
    assert "already has a tool" in message

    failed, payload = await call(
        client,
        token,
        "update_tool",
        {
            "customer": "Acme",
            "toolset": "SIMPOS mocks",
            "tool": "poll_status",
            "name": "poll_job",
        },
    )
    assert not failed, payload
    assert payload["tool"]["name"] == "poll_job"

    # --- update_toolset ----------------------------------------------------
    failed, payload = await call(
        client,
        token,
        "update_toolset",
        {"customer": "Acme", "toolset": "SIMPOS mocks", "description": None},
    )
    assert not failed, payload
    assert payload["toolset"]["description"] is None
    assert payload["toolset"]["name"] == "SIMPOS mocks"
    failed, payload = await call(
        client,
        token,
        "update_toolset",
        {"customer": "Acme", "toolset": "SIMPOS mocks", "name": "SIMPOS"},
    )
    assert not failed, payload
    assert payload["changed"] == ["name"]
    failed, message = await call(
        client,
        token,
        "update_toolset",
        {"customer": "Acme", "toolset": "SIMPOS", "name": "Handbook"},
    )
    assert failed
    assert "ambiguous" in message

    # --- other kinds and borrowed rows are refused by name -----------------
    for tool_name, arguments in (
        ("create_tool", {"toolset": "Odoo live", "name": "x"}),
        ("update_toolset", {"toolset": "Odoo live", "name": "y"}),
        ("delete_tool", {"toolset": "Handbook", "tool": "read_document"}),
        ("update_tool", {"toolset": "Handbook", "tool": "read_document", "enabled": False}),
    ):
        failed, message = await call(
            client, token, tool_name, {"customer": "Acme", **arguments}
        )
        assert failed, (tool_name, arguments)
        assert 'is kind "' in message
        assert "SIMPOS (" in message
    failed, message = await call(
        client, token, "create_tool", {"customer": "Acme", "toolset": "Odoo live", "name": "x"}
    )
    assert "credentials" in message

    for tool_name, arguments in (
        ("create_tool", {"toolset": "Shared mocks", "name": "x"}),
        ("update_tool", {"toolset": "Shared mocks", "tool": "shared_lookup", "enabled": False}),
        ("delete_tool", {"toolset": "Shared mocks", "tool": "shared_lookup"}),
        ("update_toolset", {"toolset": "Shared mocks", "name": "Mine now"}),
    ):
        failed, message = await call(
            client, token, tool_name, {"customer": "Acme", **arguments}
        )
        assert failed, tool_name
        assert "shared from the Base workspace" in message
    # Readable from here all the same.
    failed, payload = await call(
        client,
        token,
        "get_tool",
        {"customer": "Acme", "toolset": "Shared mocks", "tool": "shared_lookup"},
    )
    assert not failed, payload
    # And writable where it lives.
    failed, payload = await call(
        client,
        token,
        "update_tool",
        {
            "customer": base.customer_id,
            "toolset": "Shared mocks",
            "tool": "shared_lookup",
            "mock_response": ["a", "b"],
        },
    )
    assert not failed, payload

    # --- the read-only gate ------------------------------------------------
    for tool_name, arguments in (
        ("create_toolset", {"name": "Nope"}),
        ("create_tool", {"toolset": "SIMPOS", "name": "nope"}),
        ("update_tool", {"toolset": "SIMPOS", "tool": "poll_job", "enabled": True}),
        ("delete_tool", {"toolset": "SIMPOS", "tool": "poll_job"}),
    ):
        failed, message = await call(
            client, viewer, tool_name, {"customer": "Acme", **arguments}
        )
        assert failed, tool_name
        assert "read-only" in message

    # --- delete_tool -------------------------------------------------------
    failed, payload = await call(
        client,
        token,
        "delete_tool",
        {"customer": "Acme", "toolset": "SIMPOS", "tool": "poll_job"},
    )
    assert not failed, payload
    assert payload["deleted"]["name"] == "poll_job"
    failed, message = await call(
        client,
        token,
        "get_tool",
        {"customer": "Acme", "toolset": "SIMPOS", "tool": "poll_job"},
    )
    assert failed
    assert "lookup_record (" in message


async def _make_base(session: AsyncSession) -> tuple[int, Scope]:
    """A Base workspace, flagged the way the migration flags it — see
    `test_workspaces._make_base`, which this mirrors.
    """
    customer = await create_customer(session, name="Base")
    await session.execute(
        update(Customer).where(Customer.id == customer.id).values(is_base=True)
    )
    return customer.id, scope_from_row(customer.id)
