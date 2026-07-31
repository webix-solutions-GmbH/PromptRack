# Agent Model Evaluator

A single-user benchmarking tool for self-hosted LLMs. Point it at one or more
OpenAI-compatible endpoints, keep a library of prompts, run those prompts
against a model, rate the answers, and compare runs side by side.

Stack: Next.js 16 (App Router) · TypeScript · Tailwind v4 · Drizzle ORM +
SQLite (better-sqlite3). All state lives in a single file, `data/app.db`.

## Development

Node 22 (via nvm) is required — better-sqlite3 is a native module and is loaded
against the running Node version.

```bash
nvm use 22
npm install
npm run db:push   # create/update data/app.db from src/db/schema.ts
npm run dev       # http://localhost:3000/agent-val (the app lives under its basePath)
```

Other scripts: `npm run lint`, `npm test` (vitest), `npm run build`.

`npm run db:push` applies `src/db/schema.ts` directly to the local database —
the schema file is the source of truth; there are no checked-in migrations.

## Production deployment

The app ships as a Docker image built from the multi-stage `Dockerfile`
(`node:22-alpine`, Next.js `output: 'standalone'`).

```bash
docker compose up -d --build
```

- The compose service is `agent-val`, listening on port 3000 in the container.
- `./data` is bind-mounted to `/app/data`, so the SQLite file lives on the host
  and survives rebuilds. `user: "1001:1001"` in `docker-compose.yml` must match
  the owner of `./data`.
- It joins the **external** network `llm_default` (created by the LLM stack);
  Caddy serves the app at `https://ki01.webix.de/agent-val` (path-based routing
  via a `handle /agent-val*` block → `agent-val:3000`) and puts HTTP basic auth
  in front of it. The app itself has no authentication.
- The app is built with `basePath: '/agent-val'` (see `src/lib/base-path.ts`),
  so it expects that prefix everywhere — including in dev
  (`http://localhost:3000/agent-val`). Raw `fetch()` calls to our own API routes
  must go through `apiPath()` from `src/lib/base-path.ts`; `next/link` and the
  router add the prefix automatically.
- `127.0.0.1:3100:3000` is published for LAN/debug access from the host only
  (`http://localhost:3100/agent-val`).

### Schema bootstrap on start

The data volume can be completely empty on first start, so the schema is created
when the container starts rather than when the image is built:

1. During the image build, `drizzle-kit generate` turns `src/db/schema.ts` into
   plain SQL under `drizzle/` (generated, not committed).
2. `docker-entrypoint.sh` runs `scripts/init-db.mjs` before the server starts.
   It applies every SQL file the database has not seen yet, tracked in an
   `__app_migrations` table, and rewrites `CREATE TABLE`/`CREATE INDEX` to
   `IF NOT EXISTS` so a database originally created with `drizzle-kit push` can
   be mounted without conflicts.

This was chosen over shipping drizzle-kit in the runtime image: the bootstrap
needs nothing but `better-sqlite3`, which is already part of the standalone
output, so the runner image carries no dev dependencies and no TypeScript.

## How it works

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

- Endpoint: `POST /agent-val/api/mcp` (streamable HTTP, stateless).
- Auth: one API key in the `MCP_API_KEY` environment variable, sent as the
  `x-api-key` header (or `Authorization: Bearer <key>`). **Without the variable
  set the endpoint refuses every request** — these tools write.
- `x-api-key` is checked before `Authorization` on purpose: in production Caddy
  also wants HTTP basic auth for `/agent-val*`, and both credentials have to fit
  in one request.

```bash
# in .env next to docker-compose.yml (gitignored)
MCP_API_KEY=$(openssl rand -hex 24)
```

Register it with Claude Code — production (behind Caddy basic auth) and dev:

```bash
claude mcp add --transport http agent-val https://ki01.webix.de/agent-val/api/mcp \
  --header "x-api-key: $MCP_API_KEY" \
  --header "Authorization: Basic $(printf 'user:password' | base64)"

MCP_API_KEY=dev-key npm run dev
claude mcp add --transport http agent-val-dev http://localhost:3000/agent-val/api/mcp \
  --header "x-api-key: dev-key"
```

Tools, all named for what they do to the app's own concepts:

| | |
| --- | --- |
| Authoring | `list_prompt_groups`, `create_prompt_group`, `list_system_prompts`, `create_system_prompt`, `update_system_prompt`, `list_prompts`, `get_prompt`, `create_prompt`, `update_prompt`, `delete_prompt` |
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
- Ratings stay manual as well — the verdict is the point of the whole exercise.

## Metrics

- **TTFT** — time to first token: milliseconds between sending the request and
  the first content chunk arriving. Mostly prompt processing and queueing.
- **tok/s** — completion tokens divided by the generation time *after* the first
  token (`duration − TTFT`), i.e. decode speed without the prefill.
- **duration** — total wall-clock time of the request.
- **~estimated tokens** — a `~` prefix means the endpoint returned no usage
  block, so the completion token count was estimated from the response length
  (~4 characters per token). Those tok/s numbers are approximate.
