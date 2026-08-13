# modelfit

Which model is good enough for a specific job — invoice intake, document extraction, an
agent calling tools against a company's own RAG — and what hardware it takes to run it.
Runs the customer's real work, not a benchmark, against any OpenAI-compatible endpoint:
Ollama, LM Studio and vLLM on your own boxes and hosted APIs in one comparison, every
measurement tied to the machine that produced it. A mixed deployment is a normal answer,
and finding that line is the exercise.

## What it can test

| | |
| --- | --- |
| **Plain prompt** | One completion, rated against an expected output. |
| **Tool definitions** | Tools offered, the calls the model wanted recorded, nothing executed. |
| **Mocked tools** | Canned tool responses, byte-identical for every model, looping to a turn limit — no ERP or RAG index needed. |
| **Live MCP tools** | Tools discovered from an MCP server and really executed, the customer's stack in the loop. |
| **Injection resistance** | Seeded attacks through tool results and tool descriptions, scored for resistance *and* over-defense. |

A failing tool call is fed back to the model as tool output rather than failing the row —
what a real agent sees, and how it recovers is the measurement.

## Quick start

```bash
nvm use 22 && npm install
cp .env.example .env.local   # set BETTER_AUTH_SECRET=$(openssl rand -base64 32)
npm run dev                  # postgres in docker, migrations, app on :3000
npm run db:seed              # optional: a worked suite to edit into shape
```

Open `/login` — the first account created becomes the administrator. Every setting is
documented in [`.env.example`](.env.example); production is `docker compose up -d --build`,
published on `127.0.0.1:3100` behind a proxy of your choosing, with state in the `pgdata`
volume.

## Concepts

| | |
| --- | --- |
| **Workspace** | One per customer engagement. Endpoints, prompts and history never mix. |
| **Machine** | An endpoint plus hardware notes. One model on two machines stays two columns — throughput belongs to the hardware. |
| **Prompt group** | One job to be done. The expected output is the rubric and is never sent to the model. |
| **Run** | Every prompt of the selected groups against one model on one machine. Prompts, system prompt, tools and machine are snapshotted in, so later edits cannot falsify history. |
| **Result** | Response, rating (good / meh / bad), TTFT, tok/s, duration, tokens. |
| **`/results`** | The matrix. *By model*: live prompts as rows, each cell that model's latest usable result from any run. *By run*: the only way to put two runs of the same model side by side. |

TTFT is request to first token. tok/s counts only the generation window after it, summed
per turn on tool runs so tool waits never inflate it; a `~` means the endpoint sent no
usage block and the tokens were estimated.

## MCP API

modelfit is itself an MCP server at `POST /api/mcp` — push the prompts that matter out of
the customer's own repo, start a run, read the measurements back.

```bash
claude mcp add --transport http modelfit https://modelfit.example.com/api/mcp \
  --header "x-api-key: amv_…" --header "x-customer: Acme GmbH"
```

Auth is a per-user token from `/account/tokens` carrying its owner's role, and every call
names a workspace. Machines, toolsets and workspaces stay UI-only — those are credentials.

## Roles

| | |
| --- | --- |
| **Admin** | Everything a member can, plus users, machines and toolset credentials. |
| **Member** | Prompts, system prompts, tools, runs and ratings. |
| **Viewer** | Read-only. |

Content versus credentials, so a member may edit the tools inside a toolset but not the
MCP URL it points at. **Endpoint API keys and MCP headers are stored in Postgres in
plaintext** — treat the database and its backups accordingly, and keep `ENABLE_MOCKS` unset
in production.

Next.js 16 · TypeScript · Tailwind v4 · Drizzle ORM · PostgreSQL · MIT.
Architecture and the reasoning behind each decision: [CLAUDE.md](CLAUDE.md).
