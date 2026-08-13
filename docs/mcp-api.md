# MCP API

modelfit is itself an **MCP server**, so an agent (Claude Code, for instance) can push the
system prompts and prompts of the customer's own project straight in, start a run and read the
measurements back. The interesting test cases already live in someone's repo; retyping them
into a web form is how an evaluation dies.

- Endpoint: `POST /api/mcp` — streamable HTTP, stateless, no session id.
- Auth: a **per-user API token** from `/account/tokens`, sent as `x-api-key` (or
  `Authorization: Bearer <token>`). `x-api-key` is checked first on purpose: if a reverse proxy
  in front of the app wants HTTP basic auth, both credentials have to fit in one request. A
  session cookie is accepted too, so the endpoint can be poked from a signed-in tab.
- A token carries its owner's role. A viewer's token is refused every tool that writes — as
  `isError` tool content rather than a protocol error, because that is what the calling model
  actually reads.

## Registering it

```bash
claude mcp add --transport http modelfit https://modelfit.example.com/api/mcp \
  --header "x-api-key: amv_…" \
  --header "x-customer: Acme GmbH"
  # add --header "Authorization: Basic $(printf 'user:password' | base64)"
  # if a reverse proxy in front of the app demands its own basic auth

claude mcp add --transport http modelfit-dev http://localhost:3000/api/mcp \
  --header "x-api-key: amv_…" \
  --header "x-customer: Default"
```

## Workspace scoping

**Every call names a customer workspace**: pass `customer` (name or id) as a tool argument, or
send an `X-Customer` header on the connection so it applies to all of them. An explicit
argument wins over the header. `list_customers` is the only tool needing neither, because it
is how you find one.

A call naming no workspace is refused, with the list of workspaces in the message — a write
with no defined destination is worse than an error.

## Tools

| | |
| --- | --- |
| Authoring | `list_prompt_groups`, `create_prompt_group`, `list_system_prompts`, `create_system_prompt`, `update_system_prompt`, `list_prompts`, `get_prompt`, `create_prompt`, `update_prompt`, `delete_prompt` |
| Workspaces | `list_customers` |
| Reference | `list_toolsets`, `list_machines` |
| Running | `create_run`, `execute_run` |
| Results | `list_runs`, `get_run`, `get_run_result` |
| Rating | `set_rating` |

- Anything the app relates by name — group, system prompt, toolset, machine — takes a **name
  or a numeric id**. An ambiguous name is refused rather than guessed.
- `create_prompt` covers tool tests (`tool_mode`, `tool_choice`, `max_turns`, `toolsets`) and
  refuses the same combinations the prompt editor refuses, so a prompt authored over MCP can
  never be one `create_run` would later reject.
- `create_prompt_group` is name-idempotent, so pushing a set twice cannot duplicate it.
  `create_system_prompt` is not: two versions of one name would silently disagree.
- `execute_run` (and `create_run` with `execute: true`) starts the run in the background and
  returns at once, because a run outlives any tool-call timeout. Poll `get_run` for progress;
  it doubles as Resume, since only pending rows are executed.
- `set_rating` writes the same column the UI writes, with no provenance flag — a rating set by
  an agent is indistinguishable from a hand-clicked one, which is the deliberate cost of
  automating it. Use `note` to record that a check rather than a person decided. A row still
  `pending`/`running` is refused, since a grading loop can outrun a fire-and-forget run.

**Not writable over MCP**: machines, toolsets and their tools (an endpoint with an API key and
an MCP server URL are credentials, configured in the UI), and customer workspaces — creating
an engagement is a human decision with billing behind it.

## Mock endpoints

`/api/mock-llm` and `/api/mock-mcp` exercise the executor without real hardware — a mock LLM
that streams tool calls and canned answers, and a mock MCP server serving `echo_upper` and
`add_numbers`. They answer in development and are **404 in a production build** unless
`ENABLE_MOCKS=true`.

These are for testing modelfit itself. To make a *model* test deterministic, use a manual
toolset instead: see the tool modes in the [README](../README.md).
