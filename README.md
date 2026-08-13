# modelfit

modelfit answers the two questions that decide an AI deployment: **which model is good
enough for this customer's actual job**, and **what hardware it takes to run it**. Not a
leaderboard score — the customer's real work, loaded in as prompt suites: invoice processing
end to end, structured data pulled out of scanned documents and business correspondence, an
agent making tool calls against the company's own MCP server or RAG index. Run those tasks
against the candidate models, rate the answers, read the outcome as a matrix.

Models are reached over any OpenAI-compatible endpoint — Ollama, LM Studio and vLLM on your
own boxes, and hosted frontier APIs on exactly the same footing. Every measurement is
recorded against the **machine** that produced it (endpoint plus free-text hardware notes),
so "a 27B model on a DGX Spark handles the invoice pipeline, but contract review has to go
to a frontier API" becomes a claim with evidence under it rather than a guess.

Next.js 16 (App Router) · TypeScript · Tailwind v4 · Drizzle ORM + Postgres (`pg`). MIT.

## An evaluation, end to end

1. **Open a workspace for the engagement.** One customer, one workspace: their endpoints,
   credentials, prompts and history never mix with anyone else's ([Workspaces](#how-it-works)).
2. **Load their real tasks as prompt groups** — one group per job to be done: *Invoice intake*,
   *Contract clause extraction*, *Support triage agent*. A prompt carries the user message, the
   system prompt the production agent would use, and optionally the expected output, which is the
   rubric you rate against. `npm run db:seed` installs a worked suite (a German invoice workflow,
   a multi-step invoice agent, general capabilities, a prompt-injection / instruction-hierarchy
   set) to edit into shape; an agent can also push prompts straight out of the customer's own
   repo over the [MCP API](#mcp-api).
3. **Wire up the agentic tasks.** A prompt can offer the model tools and either record which calls
   it wanted, or really execute them against the customer's MCP server and loop until it answers
   — that is how a RAG or ERP integration gets evaluated instead of imagined.
4. **Register the candidates.** A machine is an endpoint plus hardware notes; "Discover models"
   reads `/v1/models` so you pick a model known to be served there. Put the workstation, the small
   inference box and the hosted API in side by side.
5. **Run and rate.** A run executes every prompt of the selected groups against one machine and
   one model, streaming answers and recording TTFT, tok/s, duration and tokens per result. A
   human then rates each answer good / meh / bad with a note — `meh` usually means the *prompt*
   needs work, not the model.
6. **Read `/results` and recommend.** Rows are the tasks, columns the models: ratings say who is
   good enough, the speed columns and the machine behind each column say what running it costs.
   The deliverable is a model *and* a box.

**A mixed deployment is a normal answer, not a failure to find a winner.** Most engagements end
with the bulk of the volume on a small self-hosted model and a slice of hard cases routed to a
frontier API; modelfit locates that line — which groups the local candidate passes, which it
fails, and how much hardware the passing set needs.

## Security

- **The first account created at `/login` becomes the administrator**, and sign-up closes from
  then on — every further account is created by an admin or provisioned by SSO. Three roles:
  **admin** / **member** / **viewer**, see [Accounts and roles](#accounts-and-roles).
- The MCP API is gated by **per-user API tokens** (`x-api-key`), never a shared secret — see
  [MCP API](#mcp-api).
- **Endpoint API keys and MCP toolset headers are stored in the database in plaintext.**
  Treat the database — and any backup of it — as sensitive.
- `ENABLE_MOCKS` must stay unset in production; it exposes the mock LLM/MCP endpoints
  (`/api/mock-llm`, `/api/mock-mcp`), which accept and echo whatever is sent to them.

## Development

Node 22 (via nvm) is what the toolchain is pinned to; Docker runs the development database.

```bash
nvm use 22
npm install
cp .env.example .env.local     # optional; npm run dev writes DATABASE_URL if missing
npm run dev                    # starts postgres in docker, migrates, serves the app
npm run db:seed                # optional: sample toolsets + prompt groups
```

`.env.local` also needs the two auth variables — `npm run dev` does *not* write these, because
a signing key has to be yours:

```bash
BETTER_AUTH_SECRET=$(openssl rand -base64 32)
BETTER_AUTH_URL=http://localhost:3000
```

The app serves at the root: open `http://localhost:3000/login`, and **the first account you
create becomes the administrator** — see [Accounts and roles](#accounts-and-roles).

`db:seed` fills **one workspace**, the oldest (`Default`) unless `SEED_CUSTOMER` names another
by name or id (`SEED_CUSTOMER="Acme GmbH" npm run db:seed`) — the standard suite is exactly what
you want against a new customer's candidate models, so it has to be repeatable into any
workspace. It is additive and respects deletions *per workspace*: something seeded once and
deleted since stays deleted there without being suppressed in the next workspace. A
`SEED_CUSTOMER` matching nothing exits non-zero and lists what exists, rather than creating a
workspace off a typo.

`npm run dev` runs `scripts/dev-db.mjs` first, which brings up the `docker-compose.dev.yml`
postgres on `127.0.0.1:5433`, waits for it and applies the migrations; it is idempotent, so it
costs under a second once the container is up. `npm run db:reset` drops the volume and starts
over from empty. **Setting `DATABASE_URL` yourself skips the docker step entirely** — point it
at any Postgres (managed, or a shared dev box) and the local container is left alone.

Other scripts: `npm run lint`, `npm test` (vitest, no database needed),
`npm run test:integration` (throwaway Postgres in docker), `npm run build`.

## Production deployment

The app ships as a Docker image built from the multi-stage `Dockerfile` (`node:22-alpine`,
Next.js `output: 'standalone'`). `docker compose up -d --build` brings up two services:
`postgres` (the database) and `modelfit` (the app), which waits for the database to report
healthy before it starts.

- Put `POSTGRES_PASSWORD` in a `.env` file next to `docker-compose.yml`; compose refuses to start
  without it. `DATABASE_URL` is optional and overrides the bundled database with an external one.
  `BETTER_AUTH_SECRET` and `BETTER_AUTH_URL` go in the same `.env`; `BETTER_AUTH_URL` is the
  **public** origin (e.g. `https://your-host.example`), and every auth URL, the OIDC
  `redirect_uri` included, is built from it.
- State lives in the named volume `pgdata` — no bind mount, no uid matching to get right. The
  role and database keep the historical name `agentval`; back up with
  `docker compose exec -T postgres pg_dump -U agentval agentval > backup.sql`.
- The `modelfit` service listens on port 3000 in the container and is published on
  `127.0.0.1:3100` by default. `docker-compose.yml` joins no external network out of the box;
  a commented block shows how to attach it to a reverse-proxy stack's network so the proxy
  reaches it as `modelfit:3000`. If that proxy adds its own HTTP basic auth, an MCP client
  has to send both credentials at once: basic auth in `Authorization` and its API token in
  `x-api-key` (which is why that header is read first).
- The app is built with `basePath: ''` (`src/lib/base-path.ts`), so it serves at the root
  everywhere, dev included. Raw `fetch()` calls to our own API routes must go through `apiPath()`
  from that file; `next/link` and the router add the (currently empty) prefix automatically. Set
  a non-empty `BASE_PATH` there and rebuild if the app ever has to share a hostname with other
  services — the proxy would then forward `/<prefix>*` to `modelfit:3000`.

### Schema bootstrap on start

The database can be completely empty on first start, so the schema is applied when the container
starts rather than when the image is built:

1. `src/db/schema.ts` is the source of truth. `npm run db:generate` writes an incremental SQL
   migration under `drizzle/`, which is committed and copied into the image as-is — the image
   ships the exact SQL that was reviewed. Files under `drizzle/` are never hand-edited; a
   migration is reviewed like any other code change.
2. `docker-entrypoint.sh` refuses to start without `DATABASE_URL`, then runs `scripts/init-db.mjs`
   before the server. It calls drizzle's own `migrate()`, which applies whatever migrations the
   database hasn't seen yet and records them in the ledger table `__drizzle_migrations`.
   Statements are applied verbatim, so a migration that cannot apply cleanly stops the container
   instead of silently no-opping.

This beats shipping drizzle-kit in the runtime image: the bootstrap needs nothing but `pg` and
`drizzle-orm` (for the migrator), both already in the runner image, so it needs no dev
dependencies and no TypeScript. `npm run db:migrate` is the same script against your local
database; `db:init` is `db:generate` followed by it.

## Accounts and roles

Sign-in is email + password, optionally single sign-on. There is no public registration: **the
first account ever created is the administrator**, and the sign-up endpoint is refused from then
on. Every further account is created by an admin under `/admin/users` or provisioned by your
identity provider.

| Role | May |
| --- | --- |
| **Admin** | Everything a member can, plus users, machines and toolset credentials. |
| **Member** | Prompts, system prompts, tools, runs and ratings. |
| **Viewer** | Read-only. |

The split is content versus credentials: a machine's base URL + API key and a toolset's MCP URL +
headers are secrets, so they are admin-only, while the tools *inside* a toolset are content and a
member may edit them. A role change takes effect on the requester's next request — sessions carry
no cached copy. Controls a role cannot use are not rendered, and every server action, route
handler and page re-checks the role server-side regardless.

### Single sign-on (optional)

Set `OIDC_ISSUER` and `OIDC_CLIENT_ID` (plus `OIDC_CLIENT_SECRET` for a confidential client)
and a "Single sign-on" button appears on the sign-in page. Discovery is automatic: the
issuer's `/.well-known/openid-configuration` is read, so no endpoint URLs are configured by
hand. The **redirect URI to register with the provider** is
`${BETTER_AUTH_URL}/api/auth/oauth2/callback/oidc`.

| Provider | `OIDC_ISSUER` |
| --- | --- |
| Entra ID | `https://login.microsoftonline.com/<tenant-id>/v2.0` |
| Keycloak | `https://<host>/realms/<realm>` |
| Authentik | `https://<host>/application/o/<slug>/` |

New SSO users are provisioned with `OIDC_DEFAULT_ROLE` (default `member`); anything
unrecognised there degrades to `viewer`, never to admin. Entra ID does not reliably emit an
`email` claim, so `preferred_username` and `upn` are accepted as fallbacks.

## How it works

**Workspaces** — one per customer engagement, which is the whole reason they exist: one
customer's endpoints, API keys, prompts and history stay out of another's. Machines, system
prompts, toolsets, prompt groups and runs each belong to exactly one; prompts, tools and results
inherit theirs through their parent. A workspace is a *label*, not a tenant: customers never log
in, and every signed-in user can switch into any of them with the picker above the sidebar nav.
Which one you are in lives on your user row, so it survives a sign-out and cannot be forged from
the browser; everything predating workspaces is in `Default`. Deleting one is admin-only and
refuses while it still holds anything — the foreign keys are `ON DELETE RESTRICT` precisely so a
delete can never take run history with it. **Archiving** is the soft path: it leaves the
switcher and keeps everything.

**Machines** — an OpenAI-compatible endpoint (base URL, optional API key) plus free-text hardware
notes (CPU/RAM/GPU). Those notes are what turn a tok/s number into a hardware recommendation, so
write down what the box actually is; a hosted API is simply a machine whose notes say so. "Test
connection" pings the endpoint, "Discover models" reads `/v1/models` to record what it can serve.

**Models** — kept per machine in `machine_models`, so a run picks a model known to exist on the
selected machine; models only ever seen in a run, or typed in by hand, are remembered too. One
model on two machines stays two columns in the matrix — throughput belongs to the hardware.

**Prompts** — organised into groups, one group per job to be done. Each has a title, the user
message, an optional expected output and an optional system prompt; reusable system prompts are
either appended to or overridden by the prompt's own text. The expected output is never sent to
the model — it is the rubric a rater (or a judging agent) grades against.

**Tool tests** — a prompt may select any number of toolsets and pick a mode: offer the tools and
only record which calls the model wanted, or execute the calls and feed the results back until it
answers or hits its turn limit. A toolset is **manual** (tools written in the UI answering with a
canned response — deterministic and byte-identical for every model compared) or **MCP** (tools
discovered from a streamable-HTTP MCP server and really executed against it, which is how the
customer's own RAG or ERP gets into the test). A failing tool call is fed back to the model as
tool output rather than failing the result: that is what a real agent sees, and worth measuring.

**Runs** — every prompt of the selected group(s) against one machine and one model, streaming
responses and recording per-result metrics. Prompt text, system prompt, tool definitions and the
machine are snapshotted into the run, so editing or deleting any of them later cannot falsify
history. Every result can be rated good / meh / bad with a note. Closing the tab stops a run and
Resume picks up the remaining prompts; finished runs can be archived out of the lists rather than
deleted.

**Results** — a prompt × column matrix, cells holding the response with its rating and speed.
Two pivots, both selected in the URL so a view can be bookmarked:

- *By model* (the default, e.g. `/results?mode=models&model=3|google/gemma-3-27b-it`) — rows are
  your live prompts, one column per model × machine, each cell that model's most recent usable
  result whatever run produced it. **One model is a complete selection**: a single column is how
  you review one model on its own. Since a row's cells can come from different runs, its header
  names whatever was not held constant — system prompt, tools, temperature, prompt edited since.
- *By run* (e.g. `/results?mode=runs&runs=1,5`) — 2–4 hand-picked runs side by side, the only
  pivot that can put two runs of the *same* model next to each other (a quantization swap, a
  temperature A/B, a prompt rewrite). Rows match by prompt id; results whose prompt was deleted
  meanwhile fall back to identical prompt text. A prompt only one run covered shows `—` in the
  other column.

The old `/compare` URL redirects here, query and all.

## MCP API

The app is also an **MCP server**, so an agent (Claude Code, for instance) can push the system
prompts and prompts of the customer's own project straight in, start a run and read the
measurements back — the interesting test cases already live in someone's repo, and retyping them
into a web form is how an evaluation dies.

- Endpoint: `POST /api/mcp` (streamable HTTP, stateless).
- Auth: a **per-user API token**, created under `/account/tokens` and sent as the `x-api-key`
  header (or `Authorization: Bearer <token>`). Tokens are stored as a SHA-256 hash and shown
  exactly once, at creation. `x-api-key` is checked before `Authorization` on purpose: if a
  reverse proxy in front of the app still wants HTTP basic auth, both credentials have to fit
  in one request.
- A token **acts as the user who created it and carries their role**, so a viewer's token is
  refused every tool that writes — as `isError` tool content, which is what the model reads.
- **Every call names a customer workspace** — pass `customer` (name or id) as a tool argument, or
  send an `X-Customer` header on the connection so it applies to all of them; an explicit
  argument wins over the header. `list_customers` is the only tool needing neither, because it is
  how you find one. This is a **breaking change** for callers written before workspaces existed:
  a call naming no workspace is refused, with the list of workspaces in the message, because a
  write with no defined destination is worse than an error.

Register it with Claude Code — production and dev:

```bash
claude mcp add --transport http modelfit https://your-host.example/api/mcp \
  --header "x-api-key: amv_…" \
  --header "x-customer: Acme GmbH"
  # add --header "Authorization: Basic $(printf 'user:password' | base64)" too
  # if a reverse proxy in front of the app demands its own basic auth

claude mcp add --transport http modelfit-dev http://localhost:3000/api/mcp \
  --header "x-api-key: amv_…" \
  --header "x-customer: Default"
```

Tools, all named for what they do to the app's own concepts:

| | |
| --- | --- |
| Authoring | `list_prompt_groups`, `create_prompt_group`, `list_system_prompts`, `create_system_prompt`, `update_system_prompt`, `list_prompts`, `get_prompt`, `create_prompt`, `update_prompt`, `delete_prompt` |
| Workspaces | `list_customers` |
| Reference | `list_toolsets`, `list_machines` |
| Running | `create_run`, `execute_run` |
| Results | `list_runs`, `get_run`, `get_run_result` |

- `create_prompt` covers tool tests too (`tool_mode`, `tool_choice`, `max_turns`, `toolsets`),
  and refuses the same combinations the prompt editor refuses.
- Anything the app relates by name — group, system prompt, toolset, machine — takes a **name or
  a numeric id**.
- `execute_run` (and `create_run` with `execute: true`) starts the run in the background and
  returns at once, because a run outlives any tool-call timeout. Poll `get_run` for progress; it
  doubles as Resume, since only pending rows are executed.
- Machines, toolsets and their tools are *not* writable over MCP: an endpoint with an API key and
  an MCP server URL are credentials, configured in the UI. Customer workspaces are not writable
  either — creating an engagement is a human decision with billing behind it. Ratings stay manual
  as well: the verdict is the point of the whole exercise.

Mock endpoints (`/api/mock-llm`, `/api/mock-mcp`) exercise the executor without real hardware.
They answer in development and are **404 in a production build** unless `ENABLE_MOCKS=true`.

## Metrics

- **TTFT** — time to first token: milliseconds from sending the request to the first content
  chunk. Mostly prompt processing and queueing, so it is what suffers on long documents.
- **tok/s** — completion tokens divided by the generation time *after* the first token
  (`duration − TTFT`), i.e. decode speed without the prefill. On a multi-turn tool run the
  denominator sums each turn's own generation window, so tool wait time is never counted.
- **duration** — total wall-clock time of the request.
- **~estimated tokens** — a `~` prefix means the endpoint returned no usage block, so the token
  count was estimated from the response length (~4 characters per token) and tok/s is approximate.
