# GitHub Prep — Implementation Plan

*Historical implementation plan, kept as a record of how the app was built. It describes the app under its former name and may not match the current code.*

Date: 2026-08-12
Source spec: `docs/superpowers/specs/2026-08-12-platform-evolution-design.md` (row "GitHub")
Runs: **after Phase 1** (migrations/`db:init`). Independent of Phases 2–5.
Scope: make this repo publishable as an MIT open-source project. **Not in scope:** creating the
GitHub remote, pushing, or any publish step — the user does that by hand afterwards.

Repo root for every path below: `<repo root>`
(all paths in this plan are relative to it unless absolute).

Every shell command in this plan must be prefixed with:

```bash
export PATH="$HOME/.nvm/versions/node/v22.23.1/bin:$PATH"
```

---

## Risks & open questions (read first, resolve before/while executing)

1. **Phase-1 dependency.** This plan assumes Phase 1 landed: `drizzle/` is committed
   (un-ignored in `.gitignore`/`.dockerignore`), `scripts/init-db.mjs` uses drizzle's `migrate()`,
   and `npm run db:init` exists in `package.json`. **Task 1 verifies this.** If it has not landed,
   stop and report — the README quickstart would document a command that does not exist.
2. **`.gitignore` currently contains `.env*`, which would silently ignore the new `.env.example`.**
   Task 4 must add the `!.env.example` negation, and the verification step must prove the file is
   trackable (`git check-ignore -v .env.example` → no match). This is the single most likely
   silent failure in this plan.
3. **Git history is public once pushed.** History was audited: 19 commits, no `.env`, no `data/`,
   no `*.db`, no key material ever added (`git log --diff-filter=A --name-only`). But commits carry
   two author identities, and the current remote is Azure DevOps.
   *Decision needed from the user:* publish history as-is (recommended — nothing sensitive) or start
   the public repo from a squashed initial commit. **Do not rewrite history without explicit
   approval.** Task 12 only reports.
4. **`docs/superpowers/` becomes public.** It contains the roadmap spec, which names the production host twice.
   Recommendation: keep the directory (an honest roadmap is an asset for an OSS repo) and scrub the
   two hostname mentions (Task 10). Alternative if the user prefers: add `docs/superpowers/` to
   `.gitignore` and `git rm -r --cached` it. Ask before choosing; default to scrub-and-keep.
5. **The app has zero authentication today** (auth is Phase 4). The README must say so loudly —
   publishing a self-hostable app that silently ships an unauthenticated write API (`/api/mcp` is
   key-gated, but the whole UI and every server action are not) is the real risk of going public.
   Task 8 makes this a top-of-README warning.
6. **`basePath` is a hardcoded constant** (`/agent-val`). Making it env-configurable would help
   outside users but is a behavioural code change (build-time `NEXT_PUBLIC_*` inlining, affects every
   `apiPath()` caller). It is **out of scope** here — Task 8 documents how to change it by editing
   one constant and rebuilding. Optional Task 8b exists but must not be executed without explicit
   user approval.
7. **README will be touched again in Phases 2 and 4** (Postgres `DATABASE_URL`, auth env). Write it
   for the state of the code as it is *after Phase 1* — do not document Postgres or auth as if they
   exist. Leave a short "Roadmap" section pointing at the spec instead.
8. **Seed data contains invented German company names** (`Müller Bürotechnik GmbH`,
   `Nordlicht Handels GmbH & Co. KG`, …) in `scripts/seed-prompts.mjs`. These are fictional example
   invoices, not customer data. Task 12 re-confirms by inspection; no change expected.
9. Copyright holder for the licence is given (see LICENSE). Year **2026**. Do not ask.

---

## Task list

### Task 1 — Verify the Phase-1 preconditions

**Files:** none (read-only).

Run:

```bash
cd <repo root>
node -e "const p=require('./package.json');console.log(Object.keys(p.scripts))"
ls drizzle/ 2>/dev/null | head
git check-ignore -v drizzle/ ; echo "check-ignore exit: $?"
grep -n "migrate\|__app_migrations\|IF NOT EXISTS" scripts/init-db.mjs | head
```

**Expected:** `db:init` is present in the scripts list; `drizzle/` exists and contains `.sql` files
plus `meta/_journal.json`; `git check-ignore drizzle/` exits non-zero (not ignored);
`scripts/init-db.mjs` calls drizzle's `migrate()` and no longer rewrites statements to
`IF NOT EXISTS`.

**If any expectation fails:** stop and report — Phase 1 has not landed and this plan's README/CLAUDE
edits would document a non-existent flow.

---

### Task 2 — Add the MIT licence

**Create:** `LICENSE` (repo root, no extension).

Standard MIT text, first line exactly:

```
MIT License

Copyright (c) 2026 the copyright holder
```

followed by the verbatim MIT body ("Permission is hereby granted, free of charge, …" through
"… DEALINGS IN THE SOFTWARE."). No modifications, no added clauses.

**Verify:**

```bash
head -3 LICENSE && wc -l LICENSE
```
Expected: the three lines above and 21 lines total.

---

### Task 3 — Declare the licence in `package.json`

**Edit:** `package.json`.

Add, keeping the existing keys and `"private": true` (the package is never published to npm; leaving
`private` on prevents an accidental `npm publish`):

```json
  "description": "Benchmarking tool for self-hosted LLMs: prompt library, tool/MCP tests, timed runs against OpenAI-compatible endpoints, manual ratings, comparison matrix.",
  "license": "MIT",
  "keywords": ["llm", "benchmark", "evaluation", "mcp", "ollama", "vllm", "openai-compatible"],
```

Do **not** add a `repository` field — the GitHub URL is not known yet and a wrong one is worse than
none. Note this back to the user so they add it when they create the remote.

**Verify:**

```bash
node -e "const p=require('./package.json');console.log(p.license,p.private,p.description.length)"
npm test --silent >/dev/null && echo "package.json still parses + suite runs"
```
Expected: `MIT true <number>`, and the test suite runs (JSON is valid).

---

### Task 4 — `.env.example` + un-ignore it

**Create:** `.env.example`.
**Edit:** `.gitignore` (negation), `.dockerignore` (leave `.env*` exclusion as-is — the example file
has no business in the image).

Every variable the code actually reads today, found via
`grep -rn "process\.env" src scripts *.ts`:

| Variable | Read in | Meaning |
|---|---|---|
| `MCP_API_KEY` | `src/lib/mcp/auth.ts` | key for `POST /agent-val/api/mcp`; unset ⇒ the endpoint refuses every request (503) |
| `DATA_DIR` | `scripts/init-db.mjs`, `scripts/seed-prompts.mjs` | overrides the `data/` location for the scripts (defaults to `<APP_ROOT>/data`) |
| `APP_ROOT` | same two scripts | project root the scripts resolve from (defaults to `process.cwd()`) |
| `NODE_ENV` | `src/db/index.ts`, Next | dev keeps a global DB singleton across HMR |
| `PORT`, `HOSTNAME` | Next standalone server (set in `Dockerfile`) | listen address of the production server |
| `NEXT_TELEMETRY_DISABLED` | Next | set to `1` in the Docker build |

`.env.example` content (comments matter — they are the documentation):

```dotenv
# Copy to .env and fill in. `.env` is gitignored.
#
# Next.js loads .env / .env.local automatically for `next dev`, `next build` and the
# standalone production server. The plain node scripts (scripts/*.mjs) do NOT — pass the
# variable inline or use `node --env-file=.env scripts/…`. Docker Compose reads a `.env`
# sitting next to docker-compose.yml for ${VAR} substitution.

# --- MCP API ---------------------------------------------------------------
# API key for POST /agent-val/api/mcp (sent as the `x-api-key` header, or as
# `Authorization: Bearer <key>`). LEAVE UNSET TO KEEP THE ENDPOINT DISABLED — these
# tools write to the database. Generate one with: openssl rand -hex 24
MCP_API_KEY=

# --- Storage ---------------------------------------------------------------
# Where the SQLite database lives. Defaults to ./data next to the project root.
# DATA_DIR=./data
# Project root the bootstrap/seed scripts resolve paths from. Defaults to the cwd.
# APP_ROOT=.

# --- Server ----------------------------------------------------------------
# Only relevant for the production (standalone) server; the Docker image sets both.
# PORT=3000
# HOSTNAME=0.0.0.0
# NEXT_TELEMETRY_DISABLED=1

# --- Docker Compose only ---------------------------------------------------
# uid:gid the container runs as; must own ./data on the host (see docker-compose.yml).
# APP_UID=1000
# APP_GID=1000
# Host port the app is published on.
# HOST_PORT=3100
```

`.gitignore` — change the env block from:

```
# env files (can opt-in for committing if needed)
.env*
```

to:

```
# env files (can opt-in for committing if needed)
.env*
!.env.example
```

**Verify:**

```bash
git check-ignore -v .env.example ; echo "exit=$?  (want exit=1, no output)"
git status --short .env.example    # want: ?? .env.example
grep -c "^[A-Z_]*=" .env.example   # want: 1 (only MCP_API_KEY is uncommented)
# cross-check nothing was missed:
grep -rn "process\.env\." src scripts *.ts | grep -o "process\.env\.[A-Z_]*" | sort -u
```
Expected: `.env.example` is untracked-but-not-ignored, and every variable the grep prints appears in
`.env.example` (`process.env[API_KEY_ENV]` in `auth.ts` is the indexed form of `MCP_API_KEY` — the
constant is defined two lines above it).

---

### Task 5 — Scrub internal hostnames from source comments

**Edit three files.** Content-only changes; no behaviour changes.

**5a. `next.config.ts`** — replace the comment on the `basePath` line:

```ts
  // The app is served under a sub-path so it can share a hostname with other
  // services behind a reverse proxy. Change it in src/lib/base-path.ts (build-time).
  basePath: BASE_PATH,
```

**5b. `src/lib/base-path.ts`** — replace the doc comment (keep the exports byte-identical):

```ts
/**
 * The app is served under a sub-path (`/agent-val`) rather than at the root, so it can
 * sit behind a reverse proxy alongside other services on the same hostname without an
 * extra DNS record. It is a build-time constant: change it here and rebuild.
 *
 * next/link and the router prefix this automatically; raw fetch() calls to our own API
 * routes do NOT, so they must go through apiPath().
 */
```

**5c. `src/lib/mcp/auth.ts`** — the header comment names Caddy; genericize while keeping the
*reason* (which is the load-bearing part):

```ts
/**
 * API-key auth for the MCP endpoint.
 *
 * A reverse proxy in front of this app may add its own HTTP basic auth, which occupies the
 * `Authorization` header — so the key is read from `X-Api-Key` *first* and only falls back
 * to `Authorization: Bearer`. That way a client behind such a proxy can send both
 * credentials in one request without either overwriting the other.
 */
```

**Verify:**

```bash
grep -rnI "the production host" src next.config.ts ; echo "exit=$? (want 1 / no matches)"
npx tsc --noEmit && npx vitest run src/lib/mcp --silent
```
Expected: no matches; typecheck clean; MCP tests pass.

---

### Task 6 — Genericize `docker-compose.yml`

**Edit:** `docker-compose.yml`. Replace the whole file with:

```yaml
services:
  agent-val:
    build: .
    container_name: agent-val
    restart: unless-stopped
    # Must match the owner of ./data on the host, otherwise SQLite cannot write to the
    # bind mount. Set APP_UID/APP_GID in .env, or delete this line to run as root.
    user: "${APP_UID:-1000}:${APP_GID:-1000}"
    volumes:
      - ./data:/app/data
    environment:
      # API key for the MCP endpoint (/agent-val/api/mcp). Put it in a .env file next to
      # this one; unset means the endpoint refuses every request. See .env.example.
      - MCP_API_KEY=${MCP_API_KEY:-}
    ports:
      # Published on localhost only. Change to "0.0.0.0:3100:3000" to expose it on the LAN
      # — but read the security note in the README first: the app has no authentication.
      - "127.0.0.1:${HOST_PORT:-3100}:3000"

    # If you front the app with a reverse proxy that runs in its own compose stack, join
    # that stack's network here and reach the app as `agent-val:3000`:
    #
    # networks:
    #   - proxy
    #
    # networks:
    #   proxy:
    #     external: true
    #     name: <the network your proxy stack created>
```

Notes for the implementor: the external LLM network join is removed entirely, not renamed — an
external network that does not exist makes `docker compose up` fail outright, which is a terrible
first experience for a cloned repo. The commented block preserves the recipe.
The default uid changes from `1001` to `1000` (the common first-user id on Debian/Ubuntu); the
user's own production deployment must set `APP_UID=1001`/`APP_GID=1001` in its `.env` — **call this
out explicitly in the final report**, it is a live-deployment-affecting change.

**Verify:**

```bash
docker compose config >/dev/null && echo "compose file valid"
grep -c "the external LLM network" docker-compose.yml   # want: 0
```
Expected: compose parses (if the `docker` CLI is unavailable, fall back to
`node -e "require('node:fs').readFileSync('docker-compose.yml','utf8')"` plus a YAML lint of your
choice, and say so in the report).

---

### Task 7 — Split internal deployment detail out of `CLAUDE.md`

**Edit:** `CLAUDE.md`. **Create:** `CLAUDE.local.md` (gitignored). **Edit:** `.gitignore`.

`CLAUDE.md` stays accurate but carries nothing internal. Three edits:

**7a.** Line ~66 (MCP-is-HTTP-only bullet): replace
"…so real integrations run as their own containers on `the external LLM network`…" with
"…so real integrations run as their own containers on whatever network the proxy stack uses…".

**7b.** Line ~109 (MCP auth bullet): replace
"in production Caddy also demands basic auth for `/agent-val*`" with
"a reverse proxy in front of the app may also demand HTTP basic auth".

**7c.** The whole `## Deployment` section (currently two paragraphs, one naming
`the internal Caddy config path` and `https://prod.example.internal/agent-val`) becomes:

```markdown
## Deployment

Docker multi-stage build (`node:22-alpine`, standalone output; better-sqlite3 prebuilds are
explicitly traced in `next.config.ts`). Schema bootstrap at container start: the build runs
`drizzle-kit generate`, `docker-entrypoint.sh` runs `scripts/init-db.mjs`, which applies the
committed migrations in `drizzle/` via drizzle's own migrator before the server opens the
database — see README for the rationale.

`docker-compose.yml` publishes the app on localhost only and joins no external network by
default; the commented block shows how to attach it to a reverse-proxy stack's network. The
app is served under `basePath: '/agent-val'`, so the proxy routes by path prefix.

Deployment-specific details (real hostnames, proxy config paths, host uids) live in
`CLAUDE.local.md`, which is gitignored.
```

Then add to `CLAUDE.md`, right after the `@AGENTS.md` line:

```markdown
@CLAUDE.local.md
```

(An import of a file that does not exist in a fresh clone is harmless.)

**7d.** `CLAUDE.local.md` (new, **must be gitignored before it is written**) receives the removed
internal detail verbatim, so nothing is lost for the user's own machine:

```markdown
# Local deployment notes (not published)

Production: Caddy (config `the internal Caddy config path`, separate stack) serves
`https://prod.example.internal/agent-val` via a `handle /agent-val*` block with basic auth; everything
else on that host goes to vLLM. `docker-compose.yml` must join the external network
`the external LLM network` in that deployment, and `APP_UID=1001` / `APP_GID=1001` belong in the server's
`.env` (the compose default is 1000:1000).

Caddy gotcha: the Caddyfile is bind-mounted as a single file into the caddy container —
file-replacing edits (Edit tool, `sed -i`) change the inode, so `caddy reload` still reads the
old content ("config is unchanged"). After editing it, `docker restart caddy` is required.
Production container actions (`docker compose up`, `docker restart`) must be run by the user.

Git: the original remote is Azure DevOps (`origin`); branch is `master`.
```

**7e.** `.gitignore` — add near the env block:

```
# local, unpublished agent notes
CLAUDE.local.md
```

**7f.** While in `CLAUDE.md`: the `## Commands` block still advertises `db:push` and the
`__app_migrations` TTY workaround. Phase 1 replaced that flow — update the block to list
`npm run db:init` (generate + migrate) and drop the `db:push`/`__app_migrations` paragraph, keeping
`db:seed`. Also update the Architecture bullet that says "Schema push workflow, no committed
migrations" → "committed incremental migrations under `drizzle/`, applied by drizzle's migrator;
`src/db/schema.ts` is the source of truth". *If Phase 1's plan already did these edits, verify and
skip.*

**Verify:**

```bash
grep -rnI "the production host\|the internal host path\|the external LLM network" CLAUDE.md AGENTS.md ; echo "exit=$? (want 1)"
git check-ignore -v CLAUDE.local.md   # want: a match (it IS ignored)
git status --short                    # CLAUDE.local.md must NOT appear
```

`AGENTS.md` needs no change (verified clean: 4 lines, Next.js-version note only).

---

### Task 8 — README rewrite

**Edit:** `README.md` (full rewrite, reusing the existing prose where it is already generic).

Structure, in order:

1. **Title + one-paragraph pitch.** "Benchmarking tool for self-hosted LLMs…" — keep the existing
   opening two sentences, drop "single-user" only if Phase 4 has landed (it has not; keep it).
   Add the licence line: "MIT licensed."
2. **Security notice** — a blockquote immediately under the pitch, before anything else:
   > **No authentication.** This app has no login: anyone who can reach it can read every prompt and
   > result, register endpoints, and start runs. Run it on localhost, on a trusted LAN, or behind a
   > reverse proxy that enforces authentication — never exposed to the internet. Endpoint API keys
   > and MCP server headers are stored in plaintext in the database. App-level auth is on the
   > roadmap (see `docs/superpowers/specs/`).
3. **Screenshots** — omit (none exist). Do not invent image links.
4. **Features** — 6–8 bullets, one line each: prompt library with groups + expected output;
   reusable system prompts (append/override); tool tests (definition-only or really executed via
   MCP); timed sequential runs with TTFT / tok-s / duration / token counts; manual good / meh / bad
   ratings with notes; results matrix pivoted by model or by run; snapshotting so editing a prompt
   never rewrites history; the app is itself an MCP server.
5. **Quickstart** — the block below, verbatim, and it must be the flow Task 13 actually executes:

   ````markdown
   ```bash
   git clone <this repo> && cd agent-model-evaluator
   nvm use 22            # Node 22 required: better-sqlite3 is a native module
   npm install
   npm run db:init       # create/upgrade data/app.db from the committed migrations
   npm run db:seed       # optional: example toolsets + prompt groups
   npm run dev           # → http://localhost:3000/agent-val   (note the base path!)
   ```
   ````

   Follow it with: "**Try it without a GPU.** Add a machine with base URL
   `http://localhost:3000/agent-val/api/mock-llm` — a built-in mock endpoint that streams
   OpenAI-compatible SSE, including tool calls. `http://localhost:3000/agent-val/api/mock-mcp`
   is a matching mock MCP server for tool tests. Both are development fixtures; do not expose them."
   Then: "Then register a real endpoint (Ollama, LM Studio, vLLM, llama.cpp — anything
   OpenAI-compatible), *Discover models*, create a prompt group, and start a run."
6. **Configuration** — table of the env vars, pointing at `.env.example` as the authority; a
   sentence on the base path: "The app is served under `/agent-val` (`src/lib/base-path.ts` +
   `next.config.ts`). It is a build-time constant — change it in that one file and rebuild if you
   want a different prefix or the site root."
7. **Deployment (Docker)** — rewrite of the current section with all internal infra removed:
   `docker compose up -d --build`; `./data` bind mount holds the SQLite file, so set
   `APP_UID`/`APP_GID` to the owner of `./data`; published on `127.0.0.1:3100` by default; the
   commented compose block for joining a reverse proxy's network; a note that path-prefix routing
   must forward `/agent-val*` to `agent-val:3000`. **No hostnames, no Caddyfile paths.** Keep the
   *reasoning* paragraph about schema bootstrap but update it to the post-Phase-1 truth: migrations
   are committed under `drizzle/`, generated at build time only as a check, and applied at container
   start by `scripts/init-db.mjs` using drizzle's migrator; the runner image therefore needs no
   drizzle-kit and no TypeScript.
8. **How it works** — keep the existing Machines / Models / Prompts / Runs / Results prose verbatim
   (it is already generic); update the ratings mention from "👍/👎" to good / meh / bad, which is
   what `src/lib/rating.ts` actually implements.
9. **MCP API** — keep the existing section and tool table, with these edits: drop the `prod.example.internal`
   example (use `https://your-host.example/agent-val/api/mcp`); drop the "Caddi basic auth" bullet
   or generalize it to "a reverse proxy's basic auth"; keep the `x-api-key`-before-`Authorization`
   rationale; keep the `openssl rand -hex 24` snippet and point at `.env.example`.
10. **Metrics** — keep verbatim (already generic and accurate).
11. **Development** — `npm test` (vitest), `npx tsc --noEmit`, `npm run lint`, `npm run build`;
    one sentence that tests cover the pure logic (SSE parsing, tool-loop metrics, system-prompt
    resolution, compare matching) and the rest is exercised against the dev server plus the mocks.
12. **Roadmap** — three lines: Postgres + multi-user auth + customer workspaces are planned; link
    `docs/superpowers/specs/2026-08-12-platform-evolution-design.md`.
13. **License** — "MIT — see [LICENSE](LICENSE)."

**Verify:**

```bash
grep -nI "the production host\|the internal host path\|the external LLM network\|Caddy\|db:push" README.md ; echo "exit=$? (want 1)"
grep -c "db:init" README.md   # want: >=1
```
Every command that appears in the README must be one that exists in `package.json` — cross-check:
```bash
node -e "console.log(Object.keys(require('./package.json').scripts).join(' '))"
grep -o "npm run [a-z:]*" README.md | sort -u
```
Expected: the second list is a subset of the first.

---

### Task 8b — OPTIONAL, DO NOT EXECUTE WITHOUT EXPLICIT USER APPROVAL

Make the base path configurable at build time
(`export const BASE_PATH = process.env.NEXT_PUBLIC_BASE_PATH ?? '/agent-val';`). It is a one-line
change but it inlines into every client bundle and silently breaks every `apiPath()` call if the
variable differs between build and runtime. Flagged in the risks section; the default plan keeps the
constant. If approved, add `NEXT_PUBLIC_BASE_PATH` to `.env.example` and re-run the whole
verification phase including `npm run build`.

---

### Task 9 — Community files (minimal)

**Create:** `CONTRIBUTING.md` (~20 lines) and `SECURITY.md` (~10 lines).

`CONTRIBUTING.md`: Node 22 + nvm requirement; `npm install`, `npm run db:init`, `npm run dev`;
run `npm test`, `npx tsc --noEmit`, `npm run lint` before opening a PR; schema changes go through
`npm run db:init` and the generated migration **must be committed**; issues welcome, the project is
developed against a self-hosted deployment so PRs touching deployment need a description of the
setup they were tested on.

`SECURITY.md`: the app currently has no authentication (point at the README notice); report
vulnerabilities by opening an issue or by email to the maintainer — **use a placeholder
`security@<your-domain>` and tell the user in the final report to fill it in**; no bug bounty; no
support commitment.

**Verify:** `ls CONTRIBUTING.md SECURITY.md` and `grep -c "db:push" CONTRIBUTING.md` → 0.

---

### Task 10 — Scrub the spec document

**Edit:** `docs/superpowers/specs/2026-08-12-platform-evolution-design.md` (two lines only; leave
everything else byte-identical — this is a historical record).

- Line ~18: "the production host's production history (runs, ratings, prompts) survives" → "the existing production
  history (runs, ratings, prompts) survives".
- Line ~25: "Scrub internal hostnames (`prod.example.internal`)" → "Scrub internal hostnames".

Also add this plan and any sibling phase plans under `docs/superpowers/plans/` to git (they are
currently untracked) **only if the user chose "keep docs public"** in risk item 4. Delete the empty
`docs/notes.md` (0 bytes, untracked) or leave it untracked — do not commit an empty file.

**Verify:** `grep -rnI "the production host" docs/` → no matches.

---

### Task 11 — `.dockerignore` sanity

**Edit:** `.dockerignore` — only if Phase 1 has not already removed the `drizzle` line. After
Phase 1 the committed migrations **must** reach the image (the Dockerfile copies `drizzle/` from the
builder, which regenerates it — but the builder must also see the committed journal to produce
incremental diffs rather than a fresh `0000_*.sql`). Remove the `drizzle` line if present.
Add `LICENSE`, `CONTRIBUTING.md`, `SECURITY.md`, `docs` to the ignore list next to the existing
`README.md` entry (build context hygiene only).

**Verify:**

```bash
grep -n "drizzle" .dockerignore ; echo "exit=$? (want 1)"
docker build -t agent-val:prep-check . && echo "image builds"
```
If Docker is unavailable in this environment, skip the build, say so explicitly in the report, and
note it as a residual risk for the user to check.

---

### Task 12 — Final scrub sweep + history report

**Files:** none (read-only). Run and paste the output into the final report:

```bash
cd <repo root>
# 1. no internal identifiers anywhere in the working tree
grep -rnIE "the production host|the internal host path|the external LLM network|dev\.azure\.com" . \
  --exclude-dir=node_modules --exclude-dir=.next --exclude-dir=.git \
  --exclude=CLAUDE.local.md
echo "sweep exit=$? (want 1 = clean)"

# 2. no credential-shaped strings
grep -rnIE "sk-[A-Za-z0-9]{16,}|api[_-]?key\s*[:=]\s*[\"'][^\"']{12,}" src scripts *.ts *.yml

# 3. nothing sensitive was ever committed
git log --pretty=format: --diff-filter=A --name-only | sort -u | grep -E "\.env|\.db$|data/"
echo "history exit=$? (want 1 = nothing)"

# 4. author identities that will be public
git log --format='%an <%ae>' | sort -u
```

Expected: sweeps 1–3 find nothing; sweep 4 prints two identities, one of which is a `@the internal company domain`
address — **report it and let the user decide** (risk item 3). Do not rewrite history.

Also eyeball `scripts/seed-prompts.mjs` around lines 520–1050: confirm the company names are the
invented example vendors (`Müller Bürotechnik`, `NetParts`, `TechSupply`, `Nordlicht Handels`,
`Weiss Analytics`) and contain no real customer, address, or contact data. Report the confirmation.

---

### Task 13 — Fresh-clone quickstart rehearsal (the real test of the README)

**Files:** none in the repo; work in the scratchpad.

```bash
export PATH="$HOME/.nvm/versions/node/v22.23.1/bin:$PATH"
SCRATCH=/tmp/clone-check
rm -rf "$SCRATCH" && mkdir -p "$SCRATCH"
git clone <repo root> "$SCRATCH/app"
cd "$SCRATCH/app" && npm ci
npm run db:init
npm run db:seed
ls -l data/app.db
PORT=3111 npm run dev &   # or: npx next dev -p 3111
sleep 12
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:3111/agent-val
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:3111/agent-val/prompts
kill %1
```

**Expected:** the clone contains `drizzle/`, `LICENSE`, `.env.example` (proves Task 4's negation
worked) and does **not** contain `data/`, `.env`, or `CLAUDE.local.md`; `db:init` creates
`data/app.db` from scratch with no TTY prompt; both curls return `200`.

**This is the acceptance test for the whole plan** — a README quickstart that has not been executed
from a clean clone is a guess. If any step fails, fix the README (or the script) and re-run, do not
"explain" the failure.

---

## Phase verification

Run all of these from the repo root; every one must pass before reporting done:

```bash
export PATH="$HOME/.nvm/versions/node/v22.23.1/bin:$PATH"
cd <repo root>

npm test              # vitest run — expect the full suite green (~210 tests), 0 failures
npx tsc --noEmit      # expect no output
npm run lint          # expect no errors
npm run build         # expect a successful production build (catches route/RSC breakage)
```

Phase-specific checks (all must hold):

1. `grep -rnIE "the production host|the internal host path|the external LLM network" . --exclude-dir=node_modules --exclude-dir=.next --exclude-dir=.git --exclude=CLAUDE.local.md` → **no matches**.
2. `git check-ignore .env.example` → exit 1 (**not** ignored); `git check-ignore CLAUDE.local.md` → exit 0 (ignored).
3. `head -3 LICENSE` → `MIT License` / blank / `Copyright (c) 2026 the copyright holder`.
4. `node -e "console.log(require('./package.json').license)"` → `MIT`.
5. Every `npm run <script>` mentioned in `README.md` / `CONTRIBUTING.md` exists in `package.json`.
6. `docker compose config` parses; `grep -c the external LLM network docker-compose.yml` → 0.
7. Task 13's fresh-clone rehearsal returned HTTP 200 on `/agent-val` and `/agent-val/prompts`.
8. `git status --short` shows only intended changes; no `data/`, no `.env`, no `CLAUDE.local.md`.

Do **not** commit unless the user asks; if committing, one commit, imperative message, e.g.
`Prepare the repository for public release`, and list the new files in the body.

## Report back to the user

Include: (a) the two decisions still open — git-history identities (risk 3) and whether
`docs/superpowers/` stays public (risk 4); (b) the compose uid default change 1001 → 1000 and the
`APP_UID`/`APP_GID` entries the production `.env` now needs; (c) the `security@<your-domain>`
placeholder in `SECURITY.md` awaiting a real address; (d) the missing `repository` field in
`package.json`, to be added once the GitHub remote exists; (e) whether the Docker build was actually
run or skipped.
