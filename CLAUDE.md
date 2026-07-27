# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

@AGENTS.md

## Environment

Node 22 is installed via nvm only and is NOT on the default PATH. Prepend to every shell command:

```bash
export PATH="$HOME/.nvm/versions/node/v22.23.1/bin:$PATH"
```

## Commands

```bash
npm run dev                          # dev server — app lives at http://localhost:3000/agent-val (basePath!)
npm test                             # vitest run (all tests)
npx vitest run src/lib/llm.test.ts   # single test file
npx tsc --noEmit                     # typecheck
npm run lint                         # eslint
npm run build                        # production build (also catches route/RSC errors)
npm run db:push                      # drizzle-kit push — applies src/db/schema.ts to data/app.db
```

Git: branch is `master` (not main); remote is Azure DevOps. Commits so far are milestone-sized with imperative messages.

## What this is

Single-user LLM benchmarking web app: define test prompts (grouped, optionally with expected output), run them sequentially against OpenAI-compatible endpoints (Ollama/LM Studio/vLLM) on registered machines, measure TTFT/duration/tokens/tok-s, rate results good/bad manually, compare runs in a matrix.

## Architecture

- **Stack**: Next.js 16 App Router + TypeScript + Tailwind v4, Drizzle ORM + better-sqlite3 (native module). DB file `data/app.db` (gitignored), WAL mode, singleton in `src/db/index.ts` with `foreign_keys = ON` (required for cascades). Schema push workflow, no committed migrations — `src/db/schema.ts` is the single source of truth.
- **Mutations are Server Actions** (`src/actions/*.ts`); **Route Handlers exist only where streaming is needed**: run execution (`/api/runs/[id]/execute`, NDJSON progress stream), model discovery + connection test (`/api/machines/[id]/*`), and the dev mock LLM (`/api/mock-llm/*`).
- **basePath `/agent-val`** (`next.config.ts`, constant in `src/lib/base-path.ts`): `next/link` and the router prefix automatically, but raw client `fetch()` calls to our own API routes MUST go through `apiPath()` from `src/lib/base-path.ts`.

### Snapshot model (the core invariant)

Editing or deleting prompts, system prompts, or machines must never change how a past run displays. `createRun` (`src/actions/runs.ts`) freezes everything into `run_results` rows at creation time: prompt text, title, group name, expected output, and the **already-resolved** effective system prompt. `prompt_id`/`machine_id` FKs are kept (SET NULL on delete) only for cross-run comparison; rendering always uses the snapshots. The compare page matches rows primarily by `prompt_id`, falling back to normalized `prompt_text` equality for deleted prompts (`src/lib/compare.ts`).

### System prompt resolution

A prompt references an optional base system prompt plus a mode: `append` (base + "\n\n" + custom text) or `override` (custom text only); empty/whitespace result → no system message. Pure function `resolveEffectiveSystemPrompt` in `src/lib/system-prompt.ts` — used at run creation (snapshot) and for the live preview in the prompt editor.

### Run execution pipeline

`src/lib/llm.ts` (raw-fetch SSE client, no SDK) → `src/lib/run-executor.ts` (sequential loop) → execute route (NDJSON) → `src/components/runs/run-detail.tsx` (client driver).

- `llm.ts` parses SSE chunks tolerant of provider differences (usage in final empty-choices chunk vs. on last content chunk; chunks split across reads). No usage received → estimate `ceil(chars/4)`, flag `tokensEstimated` (UI shows `~`). TTFT = first non-empty content delta.
- Executor invariants: one execution per run via module-level in-memory `Set` guard (single-process assumption) + 409 from the route; every result row is persisted the moment it finishes; errors mark the row `error` and the loop continues; abort (client disconnect) resets the in-flight row to `pending`; rows stuck in `running` from a crashed process are reclaimed to `pending` at next execution start. Run status `failed` is reserved for "every attempted result died at connection level"; partial errors still end `completed`.
- Execution is tied to the HTTP request lifetime (`request.signal`) — closing the tab stops the run; Resume picks up remaining `pending` rows.
- `tokens_per_sec = completionTokens / ((durationMs - ttftMs) / 1000)` — rate over the generation window, not total duration.

### Machine/model history

`machine_models` records every model ever seen per machine and is never deleted from: discovery upserts (`currently_loaded` flips false for models absent from `/v1/models`), manual adds, and every run (`source: 'run'`). A machine IS an endpoint (`base_url` + optional `api_key` + free-text hardware specs).

### Testing

Unit tests exist only where logic is pure and fiddly: `system-prompt.test.ts`, `llm.test.ts` (SSE fixtures per provider style), `compare.test.ts`. Everything else is verified against the dev server + the mock LLM endpoint: register a machine with base_url `http://localhost:3000/agent-val/api/mock-llm`; user messages containing `TRIGGER_ERROR` → 500, `TRIGGER_SLOW` → 2s TTFT delay.

## Deployment

Docker multi-stage build (`node:22-alpine`, standalone output; better-sqlite3 prebuilds are explicitly traced in `next.config.ts`). Schema bootstrap at container start: build runs `drizzle-kit generate`, `scripts/init-db.mjs` applies unseen SQL files (tracked in `__app_migrations`, `IF NOT EXISTS`-rewritten) — see README for rationale. `docker-compose.yml` joins the **external** network `llm_default`; Caddy (config `/home/baum/llm/caddy/Caddyfile`, separate stack) serves `https://ki01.webix.de/agent-val` via a `handle /agent-val*` block with basic auth, everything else on that host goes to vLLM.

Caddy gotcha: the Caddyfile is bind-mounted as a single file into the caddy container — file-replacing edits (Edit tool, `sed -i`) change the inode, so `caddy reload` still reads the old content ("config is unchanged"). After editing it, `docker restart caddy` is required. Production container actions (`docker compose up`, `docker restart`) must be run by the user.
