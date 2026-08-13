# modelfit

Which model is good enough for a specific job — invoice intake, extraction from scanned
documents, an agent calling tools against a company's own RAG — and what hardware it takes
to run it. modelfit answers both by running the customer's real work, not a benchmark.

Models are reached over any OpenAI-compatible endpoint, so Ollama, LM Studio and vLLM on
your own boxes sit in the same comparison as hosted frontier APIs. Every measurement is
recorded against the machine that produced it — an endpoint plus free-text hardware notes —
which is what turns a tok/s number into "this fits on a Mac Mini" instead of a guess. A
mixed deployment is a normal answer: most of the volume on a small local model, the hard
cases routed out, and finding that line is the whole exercise.

Next.js 16 · TypeScript · Tailwind v4 · Drizzle ORM · PostgreSQL · MIT

## What it can test

Business work is agentic, so a prompt is not limited to one-shot chat.

| | |
| --- | --- |
| **Plain prompt** | One completion, rated against an optional expected output. |
| **Tool definitions** | The model is offered tools and which ones it wanted to call is recorded; nothing executes. Cheap, and often the entire question. |
| **Mocked tools** | Tools written in the UI answer with a fixed canned response, looping until the model answers or runs out of turns. Deterministic and byte-identical for every model compared — and nothing has to exist yet: no ERP, no ticket system, no RAG index. |
| **Live MCP tools** | Tools discovered from a streamable-HTTP MCP server and really executed against it, with the customer's actual stack in the loop. |
| **Injection resistance** | A seeded suite attacking through the tool-result and tool-description channels — where an agent meets attacker-controlled text, and where refusal training is thinnest. Scored for resistance *and* over-defense, since a model that refuses every instruction-shaped sentence is useless on real correspondence. |

A failing tool call is fed back to the model as tool output rather than failing the row.
That is what a real agent sees, and how the model recovers is itself the measurement.

## Quick start

Node 22 and Docker.

```bash
nvm use 22
npm install
cp .env.example .env.local     # then set BETTER_AUTH_SECRET=$(openssl rand -base64 32)
npm run dev                    # dev postgres in docker, migrations, then the app
npm run db:seed                # optional: a worked suite to edit into shape
```

Open `http://localhost:3000/login` — **the first account created becomes the
administrator**, and public sign-up closes behind it. `npm run dev` is idempotent and costs
under a second once the container is up; setting `DATABASE_URL` yourself skips Docker
entirely and points at any Postgres.

`db:seed` fills one workspace — the oldest, or `SEED_CUSTOMER` by name or id — with a
German invoice workflow, a multi-step invoice agent, a general capability set and the
injection suite. It is additive and remembers deletions per workspace, so re-running it is
safe and so is seeding the next customer.

The rest: `npm run lint`, `npm test` (vitest, no database), `npm run test:integration`
(throwaway Postgres in Docker), `npm run build`, `npm run db:generate` / `db:migrate` /
`db:reset` for schema work.

## How it works

**Workspaces** — one per customer engagement, which is the whole reason they exist: one
customer's endpoints, API keys, prompts and history stay out of another's. Deleting one
refuses while it still holds anything, so a delete can never take run history with it.

**Machines and models** — a machine is an endpoint (base URL, optional API key) plus
hardware notes. "Discover models" reads `/v1/models`, so a run can only pick a model known
to be served there. One model on two machines stays two columns: throughput belongs to the
hardware, and a hosted API is simply a machine whose notes say so.

**Prompts** — grouped, one group per job to be done. Each carries the user message, the
system prompt the production agent would use, and optionally the expected output — which is
never sent to the model. It is the rubric a rater grades against.

**Runs** — every prompt of the selected groups against one model on one machine, streamed,
recording TTFT, tok/s, duration and token counts per result. Prompt text, system prompt,
tool definitions and the machine are snapshotted into the run, so editing or deleting any
of them later cannot falsify history. Each answer is rated good / meh / bad with a note;
`meh` usually means the *prompt* needs work rather than the model. Closing the tab stops a
run, and Resume picks up the prompts it never reached.

**Results** — a prompt × model matrix with two pivots, both encoded in the URL so a view
can be bookmarked. *By model* takes the live prompts as rows and fills each cell with that
model's most recent usable result from any run; one model is a complete selection, which is
how you review a single model across everything it has answered. *By run* is the only pivot
that can place two runs of the same model side by side — a quantization swap, a temperature
A/B, a prompt rewrite. Where a row's cells come from runs made under different conditions,
the row header says so, so a configuration difference is never read as a model difference.

**Authoring from an agent** — modelfit is itself an MCP server, so the prompts that matter
can be pushed straight out of the customer's own repository, a run started, and the
measurements read back. The interesting test cases already live in someone's repo, and
retyping them into a web form is how an evaluation dies. See
[docs/mcp-api.md](docs/mcp-api.md).

## Metrics

- **TTFT** — request to first token. Mostly prefill and queueing, so it is what suffers on
  long documents.
- **tok/s** — completion tokens over the generation window *after* the first token. On a
  multi-turn tool run the denominator sums each turn's own window, so time spent waiting on
  a tool is never counted as generation.
- **duration** — wall clock for the whole request.
- **~estimated** — the endpoint returned no usage block, so the token count was estimated
  from response length (~4 characters per token), and tok/s with it.

## Documentation

| | |
| --- | --- |
| [docs/deployment.md](docs/deployment.md) | Docker and compose, configuration, backups, reverse proxy, schema bootstrap |
| [docs/auth.md](docs/auth.md) | Accounts, roles, single sign-on, API tokens, security posture |
| [docs/mcp-api.md](docs/mcp-api.md) | The MCP server: authoring prompts and running evaluations from an agent |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Setup, the checks a PR must pass, schema changes |
| [CLAUDE.md](CLAUDE.md) | Architecture, and the reasoning behind each decision |

Two things before production: endpoint API keys and MCP toolset headers are stored in
Postgres **in plaintext**, and `ENABLE_MOCKS` must stay unset. Both in
[docs/auth.md](docs/auth.md).
