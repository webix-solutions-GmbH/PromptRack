# Contributing

## Setup

```bash
nvm use 22            # Node 22+
npm install
cp .env.example .env.local
# fill in BETTER_AUTH_SECRET at minimum: openssl rand -base64 32
npm run dev            # brings up a dockerized dev postgres, migrates, serves the app
```

Open `http://localhost:3000/login` and create the first account — it
becomes the administrator.

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

Issues and PRs are welcome. This project is developed against a self-hosted
deployment, so a PR that touches deployment (Docker, compose, migrations)
should describe the setup it was tested on.
