# modelfit

Which model is good enough for a specific job — invoice intake, document extraction, an
agent calling tools against a company's own RAG — and what hardware it takes to run it.

Runs the customer's real work, not a benchmark, against any OpenAI-compatible endpoint:
Ollama, LM Studio and vLLM on your own boxes and hosted APIs in one comparison, every
measurement tied to the machine that produced it.

## What it can test

| | |
| --- | --- |
| **Plain prompt** | One completion, rated against an expected output. |
| **Tool definitions** | Tools offered, the calls the model wanted recorded, nothing executed. |
| **Mocked tools** | Canned tool responses, byte-identical for every model, looping to a turn limit — no ERP or RAG index needed. |
| **Live MCP tools** | Tools discovered from an MCP server and really executed, the customer's stack in the loop. |
| **Injection resistance** | Seeded attacks through tool results and tool descriptions, scored for resistance *and* over-defense. |

## Quick start

Node 22 and Docker.

```bash
nvm use 22 && npm install
cp .env.example .env.local   # set BETTER_AUTH_SECRET=$(openssl rand -base64 32)
npm run dev                  # postgres in docker, migrations, app on :3000
npm run db:seed              # optional: a worked suite to edit into shape
```

Open `/login` — the first account created becomes the administrator, and public sign-up
closes behind it.

## Docs

| | |
| --- | --- |
| [concepts](docs/concepts.md) | Workspaces, machines, prompts, tool tests, runs, results, metrics |
| [deployment](docs/deployment.md) | Docker and compose, configuration, backups, reverse proxy |
| [auth](docs/auth.md) | Roles, single sign-on, API tokens, security posture |
| [mcp-api](docs/mcp-api.md) | modelfit as an MCP server: author prompts and run evaluations from an agent |
| [contributing](CONTRIBUTING.md) | Setup and the checks a PR must pass |
| [architecture](CLAUDE.md) | Every decision and the reasoning behind it |

Next.js 16 · TypeScript · Tailwind v4 · Drizzle ORM · PostgreSQL · MIT
