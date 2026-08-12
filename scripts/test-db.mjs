#!/usr/bin/env node
/**
 * Scratch Postgres for the integration suite.
 *
 * The container keeps its data in a tmpfs and is removed afterwards, so the
 * tests leave nothing behind and start from a schema built by the same
 * migrations production uses.
 *
 *   node scripts/test-db.mjs up | down | run
 */
import { spawnSync } from 'node:child_process';

const NAME = 'agent-val-test-pg';
const PORT = 55432;
export const TEST_DATABASE_URL = `postgres://agentval:test@127.0.0.1:${PORT}/agentval_test`;

const command = process.argv[2] ?? 'run';

if (command === 'up') {
  up();
} else if (command === 'down') {
  down();
} else if (command === 'run') {
  up();
  let status;
  try {
    status =
      spawnSync(
        'npx',
        ['vitest', 'run', '--config', 'vitest.integration.config.ts', ...process.argv.slice(3)],
        { stdio: 'inherit', env: { ...process.env, DATABASE_URL: TEST_DATABASE_URL } },
      ).status ?? 1;
  } finally {
    down();
  }
  process.exit(status);
} else {
  console.error(`[test-db] unknown command "${command}" (expected up | down | run)`);
  process.exit(1);
}

function running() {
  const res = spawnSync('docker', ['ps', '-q', '-f', `name=^${NAME}$`], { encoding: 'utf8' });
  return res.status === 0 && res.stdout.trim().length > 0;
}

function up() {
  if (running()) {
    console.log(`[test-db] reusing ${NAME}`);
  } else {
    // --rm plus a tmpfs: nothing survives, and initdb is fast enough to pay for
    // itself on every run.
    const res = spawnSync(
      'docker',
      [
        'run', '-d', '--rm', '--name', NAME,
        '-p', `127.0.0.1:${PORT}:5432`,
        '-e', 'POSTGRES_USER=agentval',
        '-e', 'POSTGRES_PASSWORD=test',
        '-e', 'POSTGRES_DB=agentval_test',
        '-e', 'POSTGRES_INITDB_ARGS=--encoding=UTF8 --lc-collate=C --lc-ctype=C',
        '--tmpfs', '/var/lib/postgresql/data:rw',
        'postgres:17-alpine',
      ],
      { stdio: 'inherit' },
    );
    if (res.error) {
      console.error(`[test-db] could not run docker (${res.error.message})`);
      process.exit(1);
    }
    if (res.status !== 0) process.exit(res.status ?? 1);
  }

  const deadline = Date.now() + 60_000;
  while (Date.now() < deadline) {
    const probe = spawnSync('docker', [
      'exec', NAME, 'pg_isready', '-U', 'agentval', '-d', 'agentval_test',
    ]);
    if (probe.status === 0) {
      const migrated = spawnSync(process.execPath, ['scripts/init-db.mjs'], {
        stdio: 'inherit',
        env: { ...process.env, DATABASE_URL: TEST_DATABASE_URL },
      });
      if (migrated.status !== 0) {
        down();
        process.exit(migrated.status ?? 1);
      }
      return;
    }
    Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 500);
  }

  console.error('[test-db] postgres did not become ready within 60s');
  down();
  process.exit(1);
}

function down() {
  spawnSync('docker', ['rm', '-f', NAME], { stdio: 'ignore' });
}
