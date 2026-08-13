# modelfit

Find the smallest model that does a specific job well enough — invoice intake, document
extraction, an agent over a company's RAG — and the hardware it needs. Any OpenAI-compatible
endpoint, local or hosted, in one matrix.

## What it can test

| | |
| --- | --- |
| **Plain prompt** | One completion, rated against an expected output. |
| **Tool definitions** | Tools offered, wanted calls recorded, nothing executed. |
| **Mocked tools** | Canned tool responses, identical for every model. No ERP or RAG index needed. |
| **Live MCP tools** | Tools discovered from an MCP server and executed for real. |
| **Injection resistance** | Seeded attacks via tool results and tool descriptions, scored for resistance *and* over-defense. |

## Quick start

```bash
nvm use 22 && npm install
cp .env.example .env.local   # set BETTER_AUTH_SECRET=$(openssl rand -base64 32)
npm run dev                  # postgres in docker, migrations, app on :3000
npm run db:seed              # optional sample suite
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
