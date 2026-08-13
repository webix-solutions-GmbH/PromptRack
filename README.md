# modelfit

Find the smallest AI model that is good enough for a customer's real work—and the
hardware needed to run it.

modelfit runs customer-specific prompt suites against models served by any
OpenAI-compatible endpoint, including Ollama, LM Studio, vLLM, and hosted APIs. It
records quality ratings alongside latency, throughput, token usage, endpoint, and
hardware notes, so model and hardware recommendations are backed by evidence.

Real business work is agentic, so a prompt is not limited to one-shot chat. It can offer
the model tools and either record the calls it wanted or execute them for real. Tools can
be **mocked** — written in the UI, answering with a fixed canned response — which makes a
multi-turn agent test deterministic and byte-identical for every model compared, with no
ERP, invoice system, or RAG index needed to run it. Or they can be **live**, discovered
from an MCP server and executed against the customer's actual stack.

Next.js 16 · TypeScript · Tailwind CSS v4 · Drizzle ORM · PostgreSQL · MIT

## What it can test

| | |
| --- | --- |
| Plain prompts | One-shot completion, rated against an optional expected output. |
| Tool definitions | Tools are offered and the calls the model wanted are recorded; nothing is executed. |
| Mocked tools | A manual toolset answers with its canned response, looping until the model answers or hits its turn limit. Identical for every model, so the comparison is fair. |
| Live MCP tools | Tools discovered from a streamable-HTTP MCP server and really executed against it — the customer's own RAG or ERP in the loop. |
| Injection resistance | A seeded suite that attacks through the tool-result and tool-description channels, scoring task completion, resistance, and over-defense. |

Tool failures are fed back to the model as tool output rather than failing the result:
that is what a real agent sees, and it is itself worth measuring.

A mixed deployment is a valid outcome. Routine volume often stays on a small local model
while hard cases are routed to a frontier API; modelfit is how you locate that line.

## Quick start

Requirements: Node.js 22 and Docker.

```bash
nvm use 22
npm install
cp .env.example .env.local
```

Set a unique auth secret in `.env.local`:

```bash
openssl rand -base64 32
```

Copy the result to `BETTER_AUTH_SECRET`, then start the app:

```bash
npm run dev
npm run db:seed # optional sample evaluation suite
```

Open [http://localhost:3000/login](http://localhost:3000/login). The first account
created becomes the administrator; public sign-up closes afterward.

`npm run dev` starts the development PostgreSQL container, applies migrations, and then
starts Next.js. Set `DATABASE_URL` yourself to use an existing database instead.

`db:seed` fills one workspace — the oldest, unless `SEED_CUSTOMER` names another by name
or ID. It is additive and respects deletions per workspace, so it is safe to re-run and
safe to run again for the next customer.

## Core concepts

| Concept | Meaning |
| --- | --- |
| Workspace | Customer-specific endpoints, prompts, and run history |
| Machine | OpenAI-compatible endpoint plus hardware notes |
| Prompt group | A real job to be evaluated, such as invoice intake |
| Toolset | Manual tools with canned responses, or tools discovered from an MCP server |
| Run | One prompt selection executed against one model and machine |
| Result | Response, rating, notes, TTFT, tok/s, duration, and token usage |

One workspace per customer engagement keeps one customer's endpoints, credentials and
history out of another's. Machines carry free-text hardware notes, which is what turns a
tok/s number into a hardware recommendation — a hosted API is simply a machine whose
notes say so.

Runs snapshot their prompts, system prompts, tools, and machine configuration, so later
edits do not alter historical results. An expected output is never sent to the model; it
is the rubric a rater grades against.

`/results` pivots two ways, both encoded in the URL so a view can be bookmarked: **by
model**, where rows are your live prompts and each cell is that model's most recent
usable result from any run, and **by run**, the only pivot that can place two runs of the
same model side by side — a quantization swap, a temperature A/B, a prompt rewrite.

## Everyday commands

```bash
npm run dev              # database, migrations, and development server
npm run build            # production build
npm run lint
npm test                 # unit tests
npm run test:integration # tests against a temporary PostgreSQL database
npm run db:seed          # add the sample evaluation suite
npm run db:generate      # generate a Drizzle migration
npm run db:migrate       # apply migrations
npm run db:reset         # reset the local development database
```

## Performance metrics

- **TTFT:** time from request to first generated token. Mostly prompt processing and
  queueing, so it is what suffers on long documents.
- **tok/s:** completion tokens per generation second, excluding TTFT. On a multi-turn
  tool run the denominator sums each turn's own generation window, so tool wait time is
  never counted as generation.
- **duration:** total request wall-clock time.
- **~estimated tokens:** the endpoint returned no usage data, so token counts and
  throughput are approximate (about 4 characters per token).

## Documentation

| | |
| --- | --- |
| [docs/deployment.md](docs/deployment.md) | Docker and compose, configuration, backups, reverse proxy, schema bootstrap |
| [docs/auth.md](docs/auth.md) | Accounts, roles, single sign-on, API tokens, security posture |
| [docs/mcp-api.md](docs/mcp-api.md) | modelfit's own MCP server: authoring prompts and running evaluations from an agent |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Setup, the checks a PR must pass, schema changes |
| [CLAUDE.md](CLAUDE.md) | Architecture and the reasoning behind each decision |

Two things worth knowing before production: endpoint API keys and MCP toolset headers are
stored in PostgreSQL **in plaintext**, and `ENABLE_MOCKS` must stay unset. Both are
covered in [docs/auth.md](docs/auth.md).
