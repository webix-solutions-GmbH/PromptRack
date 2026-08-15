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
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import users as user_store
from app.auth.passwords import hash_password
from app.auth.policy import Role
from app.auth.tokens import create_token
from app.main import app
from app.repos.endpoints import create_endpoint
from app.repos.runs import list_run_results
from app.repos.test_cases import create_test_group
from app.scope import Scope

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
    await create_endpoint(acme, session, name="Spark", base_url=DEAD_ENDPOINT, api_key=None)
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
            assert len(listed["result"]["tools"]) == 20

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

            failed, payload = await call(
                client, token, "get_run", {"customer": "Acme", "run_id": run_id}
            )
            assert not failed
            assert payload["run"]["status"] == "pending"
            assert payload["run"]["results"]["pending"] == 2
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
                {"customer": "Acme", "result_id": no_input["result_id"]},
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
                {"customer": "Acme", "result_id": result_id, "rating": "good"},
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

            # The run belongs to Acme, so Globex cannot see it at all.
            failed, message = await call(
                client, token, "get_run", {"customer": "Globex", "run_id": run_id}
            )
            assert failed

            # --- the read-only gate ------------------------------------------
            failed, message = await call(
                client, viewer, "create_test_group", {"customer": "Acme", "name": "Nope"}
            )
            assert failed
            assert "read-only" in message

            failed, payload = await call(client, viewer, "list_test_groups", {"customer": "Acme"})
            assert not failed
            assert payload["groups"]
