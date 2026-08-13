# modelfit

Which model is good enough for a specific job — invoice intake, document extraction, an
agent calling tools against a company's own RAG — and what hardware it takes to run it.
Runs the customer's real work, not a benchmark, against any OpenAI-compatible endpoint:
Ollama, LM Studio and vLLM on your own boxes and hosted APIs in one comparison. Every
measurement is tied to the machine that produced it, which is what turns a tok/s number
into "this fits on a Mac Mini" instead of a guess. A mixed deployment is a normal answer —
most volume on a small local model, hard cases routed out — and finding that line is the
exercise.

Next.js 16 · TypeScript · Tailwind v4 · Drizzle ORM · PostgreSQL · MIT

## What it can test

| | |
| --- | --- |
| **Plain prompt** | One completion, rated against an expected output. |
| **Tool definitions** | Tools offered, the calls the model wanted recorded, nothing executed. |
| **Mocked tools** | Canned tool responses, byte-identical for every model, looping to a turn limit — no ERP or RAG index needed. |
| **Live MCP tools** | Tools discovered from a streamable-HTTP MCP server and really executed, the customer's stack in the loop. |
| **Injection resistance** | Seeded attacks through tool results and tool descriptions, scored for resistance *and* over-defense. |

A failing tool call is fed back to the model as tool output rather than failing the row:
that is what a real agent sees, and how it recovers is itself the measurement.

## Quick start

Node 22 and Docker.

```bash
nvm use 22 && npm install
cp .env.example .env.local   # set BETTER_AUTH_SECRET=$(openssl rand -base64 32)
npm run dev                  # postgres in docker, migrations, app on :3000
npm run db:seed              # optional: a worked suite to edit into shape
```

Open `/login` — the first account created becomes the administrator, and public sign-up
closes behind it. `npm run dev` is idempotent; setting `DATABASE_URL` yourself skips Docker
and points at any Postgres. Every setting is documented in
[`.env.example`](.env.example).

`db:seed` fills one workspace (the oldest, or `SEED_CUSTOMER` by name or id) with a German
invoice workflow, a multi-step invoice agent, a general capability set and the injection
suite. It is additive and remembers deletions per workspace, so re-running it is safe and
so is seeding the next customer.

The rest: `npm run lint`, `npm test` (vitest, no database), `npm run test:integration`
(throwaway Postgres in Docker), `npm run build`, and `db:generate` / `db:migrate` /
`db:reset` for schema work.

## Concepts

**Workspaces** — one per customer engagement, which is why they exist: one customer's
endpoints, API keys, prompts and history stay out of another's. Deleting one refuses while
it still holds anything, so a delete can never take run history with it.

**Machines and models** — a machine is an endpoint (base URL, optional API key) plus
hardware notes. "Discover models" reads `/v1/models`, so a run can only pick a model known
to be served there. One model on two machines stays two columns: throughput belongs to the
hardware, and a hosted API is simply a machine whose notes say so.

**Prompts** — grouped, one group per job to be done. Each carries the user message, the
system prompt the production agent would use, and optionally the expected output, which is
never sent to the model. It is the rubric a rater grades against. A prompt may also select
toolsets and a mode: record the calls the model wanted, or execute them and feed results
back until it answers or hits `max_turns`.

**Runs** — every prompt of the selected groups against one model on one machine, streamed.
Prompt text, system prompt, tool definitions and the machine are snapshotted into the run,
so editing or deleting any of them later cannot falsify history. Each answer is rated
good / meh / bad with a note; `meh` usually means the *prompt* needs work rather than the
model. Closing the tab stops a run, and Resume picks up the prompts it never reached.

**Results** — a prompt × model matrix with two pivots, both in the URL so a view can be
bookmarked. *By model* takes the live prompts as rows and fills each cell with that model's
most recent usable result from any run; one model is a complete selection, which is how you
review a single model across everything it has answered. *By run* is the only pivot that
can place two runs of the same model side by side — a quantization swap, a temperature A/B,
a prompt rewrite. Where a row's cells come from runs made under different conditions, the
row header says so, so a configuration difference is never read as a model difference.

**Metrics** — **TTFT** is request to first token, mostly prefill and queueing, so it is
what suffers on long documents. **tok/s** is completion tokens over the generation window
*after* the first token; on a multi-turn tool run the denominator sums each turn's own
window, so waiting on a tool is never counted as generation. **duration** is wall clock for
the whole request. A **~** prefix means the endpoint returned no usage block, so tokens
were estimated from response length and tok/s with them.

## MCP API

modelfit is itself an MCP server at `POST /api/mcp`, so the prompts that matter can be
pushed straight out of the customer's own repository, a run started and the measurements
read back — the interesting test cases already live in someone's repo.

```bash
claude mcp add --transport http modelfit https://modelfit.example.com/api/mcp \
  --header "x-api-key: amv_…" --header "x-customer: Acme GmbH"
```

Auth is a per-user token from `/account/tokens`, carrying its owner's role. Every call
names a workspace, by the `customer` argument or the `x-customer` header. Machines,
toolsets and workspaces are not writable over MCP: an endpoint with an API key is a
credential, and creating an engagement is a human decision.

## Production

```bash
docker compose up -d --build
```

`POSTGRES_PASSWORD`, `BETTER_AUTH_SECRET` and `BETTER_AUTH_URL` go in a `.env` next to
`docker-compose.yml`; `BETTER_AUTH_URL` is the public origin, and every auth URL including
the OIDC `redirect_uri` is built from it. The app is published on `127.0.0.1:3100`, so put
a reverse proxy in front of it. Migrations apply themselves at container start.

State lives in the `pgdata` volume. The Postgres role and database keep the name `agentval`
from before the rename, deliberately — an existing volume holds a database under that name.
Back up with `docker compose exec -T postgres pg_dump -U agentval agentval > backup.sql`.

## Roles and security

| Role | May |
| --- | --- |
| **Admin** | Everything a member can, plus users, machines and toolset credentials. |
| **Member** | Prompts, system prompts, tools, runs and ratings. |
| **Viewer** | Read-only. |

The split is content versus credentials: a machine's base URL + API key and a toolset's MCP
URL + headers are secrets, so they are admin-only, while the tools inside a toolset are
content. Single sign-on is optional and generic — set `OIDC_ISSUER` and `OIDC_CLIENT_ID`
and a button appears; discovery does the rest.

**Endpoint API keys and MCP toolset headers are stored in Postgres in plaintext**, so treat
the database and its backups as sensitive. Keep `ENABLE_MOCKS` unset in production; it
exposes endpoints that echo whatever is sent to them.

Architecture and the reasoning behind each decision: [CLAUDE.md](CLAUDE.md).
