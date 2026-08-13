# modelfit

Find the smallest model that does a specific job well enough — invoice intake, document
extraction, an agent over a company's RAG — and the hardware it needs. Any OpenAI-compatible
endpoint, local or hosted, in one matrix.

## What it can test

Each step adds one thing to the one above it.

| | |
| --- | --- |
| **Prompt** | Can the model do the task at all? |
| **+ expected output** | Graded against a rubric instead of a vibe. |
| **+ tool definitions** | Does it pick the right tool, with the right arguments? Nothing executes. |
| **+ mocked tools** | The full multi-turn loop. Canned responses, identical for every model, so no ERP or RAG index has to exist. |
| **+ live MCP tools** | The same loop against the customer's real stack. |

**Example.** A supplier invoice says 10 units; the purchase order says 8. The model gets
one tool, `lookup_purchase_order`.

Prompt only tells you whether it reconciles the two numbers at all. Add the mocked tool and
you see whether it looks the PO up *before* deciding, and whether it stops and asks rather
than booking a wrong amount — with the same canned response for every model, so the
difference you measure is the model. Point that toolset at the customer's MCP server and
the identical test runs against their ERP.

A worked suite of 38 prompts is written up in
[docs/example-suite](docs/example-suite/) — invoice intake, an invoice agent, general
capability, and prompt injection through the tool-result and tool-description channels.
Point your agent at it and tell it to set the same thing up for your customer.

## Quick start

```bash
nvm use 22 && npm install
cp .env.example .env.local   # set BETTER_AUTH_SECRET=$(openssl rand -base64 32)
npm run dev                  # postgres in docker, migrations, app on :3000
```

The first account created at `/login` becomes the administrator. Settings:
[`.env.example`](.env.example). Production: `docker compose up -d --build`.

## Concepts

| | |
| --- | --- |
| **Workspace** | One per customer engagement. Nothing crosses between them. |
| **Machine** | An endpoint plus hardware notes. |
| **Prompt group** | One job to be done. Expected output is the rubric, never sent to the model. |
| **Run** | One group selection × one model × one machine. Everything it used is snapshotted, so later edits cannot rewrite history. |
| **Result** | Response, rating, TTFT, tok/s, duration, tokens. |
| **`/results`** | The matrix — by model (latest result per prompt) or by run (two runs of one model side by side). |

tok/s measures decode only: TTFT and tool waits are excluded. A `~` means the endpoint sent
no usage data.

## MCP API

`POST /api/mcp` — author prompts and run evaluations from an agent.

```bash
claude mcp add --transport http modelfit https://modelfit.example.com/api/mcp \
  --header "x-api-key: amv_…" --header "x-customer: Acme GmbH"
```

Per-user token, carrying its owner's role. Machines, toolsets and workspaces stay UI-only.

## Roles

| | |
| --- | --- |
| **Admin** | Members' rights plus users, machines, toolset credentials. |
| **Member** | Prompts, tools, runs, ratings. |
| **Viewer** | Read-only. |

Endpoint keys and MCP headers are stored in plaintext — treat the database accordingly, and
keep `ENABLE_MOCKS` unset in production.

[CLAUDE.md](CLAUDE.md) has the architecture.
