# Contributing to modelfit

modelfit evaluates whether a given model is good enough for a specific
customer's job — invoice agents, document and data extraction, MCP tool calls
over a company's own RAG — and what hardware that job takes. It is not a
benchmark suite, so changes are welcome that make a real workload easier to
express and its result easier to judge.

## Setup

```bash
nvm use 22            # Node 22+
npm install
cp .env.example .env.local   # optional; npm run dev writes DATABASE_URL if it is missing
# fill in BETTER_AUTH_SECRET at minimum: openssl rand -base64 32
npm run dev            # brings up a dockerized dev postgres, migrates, serves the app
```

Open `http://localhost:3000/login` and create the first account — it
becomes the administrator. `npm run db:seed` then fills one workspace with
sample toolsets and prompt groups if you want something to run.

`CLAUDE.md` is the architecture document: it records the decisions and the
reasoning behind them, and is worth reading before a non-trivial change.
`AGENTS.md` is the standing warning that this Next.js version differs from what
most tooling assumes.

## Before opening a PR

```bash
npm test                 # vitest — pure suite, no database
npm run test:integration # spins up a throwaway postgres in docker
npx tsc --noEmit
npm run lint
```

All four must pass.

## Schema changes

Edit `src/db/schema.ts`, then run `npm run db:generate` (drizzle-kit) to write
the migration under `drizzle/`. The generated SQL **must be committed** —
`drizzle-kit generate` can prompt interactively when it suspects a rename, so
run it in a real terminal when renaming a table or column.

## Notes for reviewers

Issues and PRs are welcome; the default branch is `master`. This project is
developed against a self-hosted deployment, so a PR that touches deployment
(Docker, compose, migrations) should describe the setup it was tested on.
