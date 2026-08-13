# Agent Model Evaluator

A benchmarking tool for self-hosted LLMs. Point it at one or more
OpenAI-compatible endpoints, keep a library of prompts, run those prompts
against a model, rate the answers, and compare runs side by side.

Stack: Next.js 16 (App Router) · TypeScript · Tailwind v4 · Drizzle ORM +
Postgres (`pg`). MIT licensed.

## Security

- **The first account created at `/login` becomes the administrator**,
  and sign-up closes from then on — every further account is created by an
  admin or provisioned by SSO. See [Accounts and roles](#accounts-and-roles).
- Three roles: **admin** / **member** / **viewer**.
- The MCP API is gated by **per-user API tokens** (`x-api-key`), never a shared
  secret — see [MCP API](#mcp-api).
- **Endpoint API keys and MCP toolset headers are stored in the database in
  plaintext.** Treat the database — and any backup of it — as sensitive.
- `ENABLE_MOCKS` must stay unset in production; it exposes the mock LLM/MCP
  endpoints (`/api/mock-llm`, `/api/mock-mcp`), which accept and echo whatever
  is sent to them.

## Development

Node 22 (via nvm) is what the toolchain is pinned to. Docker is used for the
development database.

```bash
nvm use 22
npm install
cp .env.example .env.local     # optional; npm run dev writes DATABASE_URL if missing
npm run dev                    # starts postgres in docker, migrates, serves the app
npm run db:seed                # optional: sample toolsets + prompt groups
```

`db:seed` fills **one workspace**, the oldest (`Default`) unless `SEED_CUSTOMER`
names another by name or id — the standard suite is exactly what you want to run
against a new customer's candidate models, so it has to be repeatable into any
workspace:

```bash
SEED_CUSTOMER="Acme GmbH" npm run db:seed
```

It is additive and respects deletions *per workspace*: something seeded once and
deleted since stays deleted there, without suppressing it in the next workspace.
A `SEED_CUSTOMER` that matches nothing exits non-zero and lists what exists,
rather than creating a workspace off a typo.

`.env.local` also needs the two auth variables (`npm run dev` does *not* write
these — a signing key has to be yours):

```bash
BETTER_AUTH_SECRET=$(openssl rand -base64 32)
BETTER_AUTH_URL=http://localhost:3000
```

The app serves at the root: open
`http://localhost:3000/login` and **the first account you create
becomes the administrator** — see [Accounts and roles](#accounts-and-roles).

`npm run dev` runs `scripts/dev-db.mjs` first, which brings up the
`docker-compose.dev.yml` postgres on `127.0.0.1:5433`, waits for it and applies
the migrations. It is idempotent, so it costs under a second once the container
is up. `npm run db:reset` drops the volume and starts over from an empty
database.

**Setting `DATABASE_URL` yourself skips the docker step entirely** — point it at
any Postgres (a managed one, a shared dev box) and `npm run dev` will leave the
local container alone.

Other scripts: `npm run lint`, `npm test` (vitest, no database needed),
`npm run test:integration` (spins up a throwaway Postgres in docker),
`npm run build`.

## Production deployment

The app ships as a Docker image built from the multi-stage `Dockerfile`
(`node:22-alpine`, Next.js `output: 'standalone'`).

```bash
docker compose up -d --build
```

Compose brings up two services: `postgres` (the database) and `agent-val` (the
app), which waits for the database to report healthy before it starts.

- Put `POSTGRES_PASSWORD` in a `.env` file next to `docker-compose.yml`; compose
  refuses to start without it. `DATABASE_URL` is optional and overrides the
  bundled database with an external one.
- `BETTER_AUTH_SECRET` and `BETTER_AUTH_URL` go in the same `.env`.
  `BETTER_AUTH_URL` is the **public** origin
  (e.g. `https://your-host.example`); every auth URL, the OIDC
  `redirect_uri` included, is built from it.
- State lives in the named volume `pgdata` — there is no bind mount and no uid
  matching to get right. Back it up with
  `docker compose exec -T postgres pg_dump -U agentval agentval > backup.sql`.
- The compose service is `agent-val`, listening on port 3000 in the container,
  published on `127.0.0.1:3100` by default (`http://localhost:3100`
  from the host). `docker-compose.yml` joins no external network out of the
  box; a commented block shows how to attach it to a reverse-proxy stack's
  network so the proxy can reach it as `agent-val:3000`. If the proxy adds its
  own HTTP basic auth in front of the app, an MCP client has to send both
  credentials at once: basic auth in `Authorization` and its API token in
  `x-api-key` (which is why that header is read first).
- The app is built with `basePath: ''` (see `src/lib/base-path.ts`), so it
  serves at the root everywhere — including in dev (`http://localhost:3000`).
  Raw `fetch()` calls to our own API routes must go through `apiPath()` from
  `src/lib/base-path.ts`; `next/link` and the router add the (currently empty)
  prefix automatically. Set a non-empty `BASE_PATH` there and rebuild if the
  app ever needs to share a hostname with other services behind a reverse
  proxy — a proxy would then forward `/<prefix>*` to `agent-val:3000`.

### Schema bootstrap on start

The database can be completely empty on first start, so the schema is applied
when the container starts rather than when the image is built:

1. `src/db/schema.ts` is the source of truth. `npm run db:generate` writes an
   incremental SQL migration under `drizzle/`, which is committed and copied
   into the image as-is — the image ships the exact SQL that was reviewed.
   Files under `drizzle/` are never hand-edited; a migration is reviewed like
   any other code change.
2. `docker-entrypoint.sh` refuses to start without `DATABASE_URL`, then runs
   `scripts/init-db.mjs` before the server. It calls drizzle's own `migrate()`,
   which applies whatever migrations the database hasn't seen yet and records
   them in drizzle's ledger table `__drizzle_migrations`. Statements are applied
   verbatim, so a migration that cannot apply cleanly stops the container
   instead of silently no-opping.

This was chosen over shipping drizzle-kit in the runtime image: the bootstrap
needs nothing but `pg` and the `drizzle-orm` package (for the migrator), both
carried in the runner image, so it needs no dev dependencies and no TypeScript.

`npm run db:migrate` is the same script against your local database; `db:init`
is `db:generate` followed by it.

## Accounts and roles

Sign-in is email + password, optionally single sign-on. There is no public
registration: **the first account ever created is the administrator**, and the
sign-up endpoint is refused from then on. Every further account is created by an
admin under `/admin/users` or provisioned by your identity provider.

| Role | May |
| --- | --- |
| **Admin** | Everything a member can, plus users, machines and toolset credentials. |
| **Member** | Prompts, system prompts, tools, runs and ratings. |
| **Viewer** | Read-only. |

The split is content versus credentials: a machine's base URL + API key and a
toolset's MCP URL + headers are secrets, so they are admin-only, while the tools
*inside* a toolset are content and a member may edit them. A role change takes
effect on the requester's next request — sessions carry no cached copy of it.

Controls a role cannot use are not rendered, and every server action, route
handler and page re-checks the role server-side regardless.

### Single sign-on (optional)

Set `OIDC_ISSUER` and `OIDC_CLIENT_ID` (plus `OIDC_CLIENT_SECRET` for a
confidential client) and a "Single sign-on" button appears on the sign-in page.
Discovery is automatic: the issuer's `/.well-known/openid-configuration` is
read, so no endpoint URLs are configured by hand.

The **redirect URI to register with the provider** is:

```
${BETTER_AUTH_URL}/api/auth/oauth2/callback/oidc
```

Issuer URLs, by provider:

| Provider | `OIDC_ISSUER` |
| --- | --- |
| Entra ID | `https://login.microsoftonline.com/<tenant-id>/v2.0` |
| Keycloak | `https://<host>/realms/<realm>` |
| Authentik | `https://<host>/application/o/<slug>/` |

New SSO users are provisioned with `OIDC_DEFAULT_ROLE` (default `member`);
anything unrecognised there degrades to `viewer`, never to admin. Entra ID does
not reliably emit an `email` claim, so `preferred_username` and `upn` are
accepted as fallbacks.

## How it works

**Workspaces** — one per customer engagement. Machines, system prompts,
toolsets, prompt groups and runs each belong to exactly one; prompts, tools and
results inherit theirs through their parent. A workspace is a *label*, not a
tenant: customers never log in, and every signed-in user can switch into any of
them with the picker above the sidebar nav. Which one you are in lives on your
user row, so it survives a sign-out and cannot be forged from the browser.
Everything that existed before workspaces landed in one called `Default`.

Deleting a workspace is admin-only and refuses while it still holds anything —
the foreign keys are `ON DELETE RESTRICT` precisely so a delete can never take
run history with it. **Archiving** is the soft path: the workspace disappears
from the switcher and keeps everything.

**Machines** — an OpenAI-compatible endpoint (base URL, optional API key) plus
free-text hardware notes (CPU/RAM/GPU). "Test connection" pings the endpoint and
"Discover models" reads `/v1/models` to record what the machine can serve.

**Models** — kept per machine in `machine_models`, so a run can pick a model that
is known to exist on the selected machine. Models that were only seen in a run,
or entered by hand, are remembered too.

**Prompts** — organised into groups. Each prompt has a title, the user message,
an optional expected output, and an optional system prompt; reusable system
prompts can either be appended to or overridden by the prompt's own text.

**Runs** — a run executes every prompt of the selected group(s) against one
machine and one model, streaming the responses and recording per-result metrics.
The machine is snapshotted into the run, so deleting a machine later does not
falsify history. Every result can be rated 👍/👎 with a note.

**Results** — a prompt × column matrix, cells holding the response with its
rating and speed. Two pivots, both selected in the URL so a view can be
bookmarked:

- *By model* (the default, e.g. `/results?mode=models&model=3|google/gemma-3-27b-it`)
  — rows are your live prompts, one column per model × machine, each cell that
  model's most recent usable result whatever run produced it. **One model is a
  complete selection**: a single column is how you review one model on its own.
- *By run* (e.g. `/results?mode=runs&runs=1,5`) — 2–4 hand-picked runs side by
  side, the only pivot that can put two runs of the *same* model next to each
  other. Rows are matched by prompt id; results whose prompt was deleted
  meanwhile fall back to matching on identical prompt text. A prompt only one of
  the runs covered shows `—` in the other column.

The old `/compare` URL redirects here, query and all.

## MCP API

The app is also an **MCP server**, so an agent (Claude Code, for instance) can
push the system prompts and prompts of another project straight in, start a run,
and read the measurements back — instead of copying someone else's prompts into
the web UI by hand.

- Endpoint: `POST /api/mcp` (streamable HTTP, stateless).
- Auth: a **per-user API token**, created under `/account/tokens` and
  sent as the `x-api-key` header (or `Authorization: Bearer <token>`). Tokens
  are stored as a SHA-256 hash and shown exactly once, at creation.
- A token **acts as the user who created it and carries their role**, so a
  viewer's token is refused every tool that writes — with the refusal as
  `isError` tool content, which is what the calling model reads.
- `x-api-key` is checked before `Authorization` on purpose: if a reverse proxy
  in front of the app still wants HTTP basic auth, both credentials have to
  fit in one request.
- **Every call names a customer workspace** — pass `customer` (name or id) as a
  tool argument, or send an `X-Customer` header on the connection so it applies
  to all of them. An explicit argument wins over the header. `list_customers` is
  the only tool that needs neither, because it is how you find one. This is a
  **breaking change** for callers written before workspaces existed: a call that
  names no workspace is refused, with the list of workspaces in the message,
  because a write with no defined destination is worse than an error.

Register it with Claude Code — production and dev:

```bash
claude mcp add --transport http agent-val https://your-host.example/api/mcp \
  --header "x-api-key: amv_…" \
  --header "x-customer: Acme GmbH"
  # add --header "Authorization: Basic $(printf 'user:password' | base64)" too
  # if a reverse proxy in front of the app demands its own basic auth

claude mcp add --transport http agent-val-dev http://localhost:3000/api/mcp \
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

Notes:

- `create_prompt` covers tool tests too (`tool_mode`, `tool_choice`, `max_turns`,
  `toolsets`), and refuses the same combinations the prompt editor refuses.
- Anything the app relates by name — group, system prompt, toolset, machine —
  can be referenced by **name or numeric id**.
- `execute_run` (and `create_run` with `execute: true`) starts the run in the
  background and returns at once, because a run outlives any tool-call timeout.
  Poll `get_run` for progress; it also doubles as Resume, since only pending rows
  are executed.
- Machines, toolsets and their tools are *not* writable over MCP: an endpoint
  with an API key and an MCP server URL are credentials, configured in the UI.
  Customer workspaces are not writable either — creating an engagement is a human
  decision with billing behind it.
- Ratings stay manual as well — the verdict is the point of the whole exercise.

Mock endpoints (`/api/mock-llm`, `/api/mock-mcp`) exist for exercising the
executor without real hardware. They answer in development and are **404 in a
production build** unless `ENABLE_MOCKS=true` is set.

## Metrics

- **TTFT** — time to first token: milliseconds between sending the request and
  the first content chunk arriving. Mostly prompt processing and queueing.
- **tok/s** — completion tokens divided by the generation time *after* the first
  token (`duration − TTFT`), i.e. decode speed without the prefill.
- **duration** — total wall-clock time of the request.
- **~estimated tokens** — a `~` prefix means the endpoint returned no usage
  block, so the completion token count was estimated from the response length
  (~4 characters per token). Those tok/s numbers are approximate.
