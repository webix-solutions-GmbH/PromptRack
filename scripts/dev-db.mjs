#!/usr/bin/env node
/**
 * Brings up the development Postgres and applies the migrations, so that
 * `npm run dev` works on a fresh clone with nothing installed but docker.
 *
 * Idempotent and fast when the container is already running. Setting
 * DATABASE_URL to anything other than the local dev instance disables this
 * entirely — that is the escape hatch for a managed database.
 */
import fs from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';

const DEV_DATABASE_URL = 'postgres://agentval:dev@127.0.0.1:5433/agentval';
const COMPOSE = ['compose', '-f', 'docker-compose.dev.yml'];
const root = process.cwd();
const reset = process.argv.includes('--reset');

function docker(args, opts = {}) {
  return spawnSync('docker', args, { cwd: root, ...opts });
}

// 1. Someone is pointing at a database we do not manage — do nothing.
const external = process.env.DATABASE_URL;
if (external && !external.includes('127.0.0.1:5433') && !external.includes('localhost:5433')) {
  console.log('[dev-db] DATABASE_URL is set, skipping local postgres');
  process.exit(0);
}

// 2. Write .env.local so `next dev` (which this script does not control) sees
//    the same connection string.
const envLocal = path.join(root, '.env.local');
if (!fs.existsSync(envLocal)) {
  fs.writeFileSync(envLocal, `DATABASE_URL=${DEV_DATABASE_URL}\n`);
  console.log(`[dev-db] wrote .env.local with DATABASE_URL=${DEV_DATABASE_URL}`);
}

// 3. --reset drops the volume so the next `up` re-runs initdb from scratch.
if (reset) {
  console.log('[dev-db] resetting: dropping the dev database volume');
  const down = docker([...COMPOSE, 'down', '-v'], { stdio: 'inherit' });
  if (down.error) exitNoDocker(down.error);
}

// 4. Start postgres.
const up = docker([...COMPOSE, 'up', '-d', 'postgres'], { stdio: 'inherit' });
if (up.error) exitNoDocker(up.error);
if (up.status !== 0) {
  console.error('[dev-db] `docker compose up` failed');
  process.exit(up.status ?? 1);
}

// 5. Wait for it to accept connections.
const deadline = Date.now() + 60_000;
let ready = false;
while (Date.now() < deadline) {
  const probe = docker([...COMPOSE, 'exec', '-T', 'postgres', 'pg_isready', '-U', 'agentval', '-d', 'agentval']);
  if (probe.status === 0) {
    ready = true;
    break;
  }
  sleep(500);
}
if (!ready) {
  console.error('[dev-db] postgres did not become ready within 60s');
  process.exit(1);
}

// 6. Apply the migrations.
const migrated = spawnSync(process.execPath, ['scripts/init-db.mjs'], {
  cwd: root,
  stdio: 'inherit',
  env: { ...process.env, DATABASE_URL: DEV_DATABASE_URL },
});
if (migrated.status !== 0) {
  process.exit(migrated.status ?? 1);
}

console.log(`[dev-db] ready — ${DEV_DATABASE_URL}`);

function sleep(ms) {
  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms);
}

function exitNoDocker(error) {
  console.error(
    `[dev-db] could not run docker (${error.message}).\n` +
      '        Install docker, or point DATABASE_URL at an existing Postgres to skip this step.',
  );
  process.exit(1);
}
