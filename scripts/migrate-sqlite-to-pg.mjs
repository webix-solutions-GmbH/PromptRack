#!/usr/bin/env node
/**
 * One-time copy of the retired SQLite database into Postgres.
 *
 * Usage:
 *   node scripts/migrate-sqlite-to-pg.mjs --sqlite <path> [--url <DATABASE_URL>] [--truncate]
 *
 * The SQLite side is read with Node's built-in `node:sqlite`, so nothing has to
 * be installed to run this against an old `data/app.db` — and the app itself no
 * longer depends on a SQLite driver at all.
 *
 * Text columns are copied verbatim, never parsed and re-serialized: the seeded
 * prompt-injection suite carries an invisible Unicode Tags payload (U+E0000+)
 * that a JSON round trip could silently normalise away. Step 8 asserts it
 * survived, code point by code point.
 */
import { DatabaseSync } from 'node:sqlite';
import { Client } from 'pg';

// Copy order is FK-safe: a table only ever references ones already copied.
const TABLES = [
  {
    name: 'machines',
    cols: ['id', 'name', 'base_url', 'api_key', 'cpu', 'ram', 'gpu', 'notes', 'created_at', 'updated_at'],
    ts: ['created_at', 'updated_at'],
    bool: [],
    serial: true,
  },
  {
    name: 'machine_models',
    cols: ['id', 'machine_id', 'model_id', 'currently_loaded', 'first_seen_at', 'last_seen_at', 'source'],
    ts: ['first_seen_at', 'last_seen_at'],
    bool: ['currently_loaded'],
    serial: true,
  },
  {
    name: 'system_prompts',
    cols: ['id', 'name', 'content', 'created_at', 'updated_at'],
    ts: ['created_at', 'updated_at'],
    bool: [],
    serial: true,
  },
  {
    name: 'toolsets',
    cols: ['id', 'name', 'description', 'kind', 'mcp_url', 'mcp_headers', 'created_at', 'updated_at'],
    ts: ['created_at', 'updated_at'],
    bool: [],
    serial: true,
  },
  {
    name: 'tools',
    cols: [
      'id', 'toolset_id', 'name', 'description', 'parameters_json', 'mock_response',
      'enabled', 'source', 'first_seen_at', 'last_seen_at',
    ],
    ts: ['first_seen_at', 'last_seen_at'],
    bool: ['enabled'],
    serial: true,
  },
  {
    name: 'prompt_groups',
    cols: ['id', 'name', 'description', 'sort_order', 'created_at'],
    ts: ['created_at'],
    bool: [],
    serial: true,
  },
  {
    name: 'prompts',
    cols: [
      'id', 'group_id', 'title', 'content', 'expected_output', 'system_prompt_id',
      'system_prompt_mode', 'custom_system_text', 'tool_mode', 'tool_choice',
      'max_turns', 'sort_order', 'created_at', 'updated_at',
    ],
    ts: ['created_at', 'updated_at'],
    bool: [],
    serial: true,
  },
  {
    name: 'prompt_toolsets',
    cols: ['prompt_id', 'toolset_id', 'sort_order'],
    ts: [],
    bool: [],
    serial: false,
  },
  {
    name: 'runs',
    cols: [
      'id', 'machine_id', 'machine_snapshot', 'model_id', 'params', 'comment',
      'group_names', 'llm_info', 'status', 'archived_at', 'created_at',
      'started_at', 'finished_at',
    ],
    ts: ['archived_at', 'created_at', 'started_at', 'finished_at'],
    bool: [],
    serial: true,
  },
  {
    name: 'run_results',
    cols: [
      'id', 'run_id', 'prompt_id', 'sort_order', 'group_name', 'prompt_title',
      'prompt_text', 'expected_output', 'system_prompt_text', 'tools_snapshot',
      'tool_mode', 'tool_choice', 'max_turns', 'status', 'response_text',
      'transcript_json', 'turns_json', 'turn_count', 'tool_call_count',
      'stopped_reason', 'error', 'duration_ms', 'ttft_ms', 'prompt_tokens',
      'completion_tokens', 'tokens_per_sec', 'tokens_estimated', 'rating',
      'rating_note', 'started_at', 'finished_at',
    ],
    ts: ['started_at', 'finished_at'],
    bool: ['tokens_estimated'],
    serial: true,
  },
  {
    name: '__app_seeds',
    cols: ['kind', 'scope', 'name', 'seeded_at'],
    ts: ['seeded_at'],
    bool: [],
    serial: false,
  },
];

const BATCH = 500;
/** Epoch millis for 2001-09-09. A smaller value means seconds, i.e. a bug. */
const MIN_TIMESTAMP_MS = 1_000_000_000_000;

const INJECTION_TITLE = 'Injection 06: invisible Unicode instructions (ASCII smuggling)';

main().catch((err) => {
  console.error(`[migrate] ${err.message}`);
  process.exit(1);
});

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const sqlitePath = args.sqlite;
  if (!sqlitePath) {
    console.error(
      'usage: node scripts/migrate-sqlite-to-pg.mjs --sqlite <path> [--url <DATABASE_URL>] [--truncate]',
    );
    process.exit(1);
  }
  const connectionString =
    args.url ?? process.env.DATABASE_URL ?? 'postgres://agentval:dev@127.0.0.1:5433/agentval';

  const sqlite = new DatabaseSync(sqlitePath, { readOnly: true });
  const client = new Client({ connectionString });
  await client.connect();

  try {
    await assertEmptyTarget(client, args.truncate);

    await client.query('BEGIN');
    for (const table of TABLES) {
      const rows = sqlite.prepare(`SELECT * FROM ${table.name}`).all();
      await copyTable(client, table, rows);
      console.log(`[migrate] ${table.name}: ${rows.length} row(s)`);
    }
    for (const table of TABLES.filter((t) => t.serial)) {
      await client.query(
        `SELECT setval(pg_get_serial_sequence($1, 'id'),
                       COALESCE((SELECT MAX(id) FROM ${table.name}), 0) + 1, false)`,
        [table.name],
      );
    }
    await client.query('COMMIT');
  } catch (err) {
    await client.query('ROLLBACK').catch(() => {});
    sqlite.close();
    await client.end();
    throw err;
  }

  const ok = await verifyCounts(sqlite, client);
  const payloadOk = await verifyInjectionPayload(sqlite, client);

  sqlite.close();
  await client.end();

  if (!ok || !payloadOk) process.exit(1);
  console.log(`[migrate] done — ${sqlitePath} → ${redact(connectionString)}`);
}

function parseArgs(argv) {
  const out = { truncate: false };
  for (let i = 0; i < argv.length; i += 1) {
    if (argv[i] === '--sqlite') out.sqlite = argv[++i];
    else if (argv[i] === '--url') out.url = argv[++i];
    else if (argv[i] === '--truncate') out.truncate = true;
    else throw new Error(`unknown argument "${argv[i]}"`);
  }
  return out;
}

/**
 * Importing on top of existing rows would duplicate ids and corrupt the FKs, so
 * a non-empty target is refused unless the caller says otherwise.
 */
async function assertEmptyTarget(client, truncate) {
  const counts = [];
  for (const table of TABLES) {
    const { rows } = await client.query(`SELECT count(*)::int AS c FROM ${table.name}`);
    if (rows[0].c > 0) counts.push(`${table.name}=${rows[0].c}`);
  }
  if (counts.length === 0) return;

  if (!truncate) {
    throw new Error(
      `target database is not empty (${counts.join(', ')}). ` +
        'Re-run with --truncate to replace its contents.',
    );
  }
  await client.query(
    `TRUNCATE ${TABLES.map((t) => t.name).join(', ')} RESTART IDENTITY CASCADE`,
  );
  console.log('[migrate] target truncated');
}

async function copyTable(client, table, rows) {
  if (rows.length === 0) return;

  const colList = table.cols.map((c) => `"${c}"`).join(', ');
  for (let offset = 0; offset < rows.length; offset += BATCH) {
    const batch = rows.slice(offset, offset + BATCH);
    const params = [];
    const tuples = batch.map((row) => {
      const placeholders = table.cols.map((col) => {
        params.push(convert(table, col, row[col]));
        return `$${params.length}`;
      });
      return `(${placeholders.join(', ')})`;
    });
    await client.query(
      `INSERT INTO ${table.name} (${colList}) VALUES ${tuples.join(', ')}`,
      params,
    );
  }
}

function convert(table, col, value) {
  if (table.ts.includes(col)) {
    if (value === null || value === undefined) return null;
    const ms = Number(value);
    if (!Number.isFinite(ms) || ms < MIN_TIMESTAMP_MS) {
      throw new Error(
        `${table.name}.${col} = ${value} is not epoch milliseconds (expected > ${MIN_TIMESTAMP_MS})`,
      );
    }
    return new Date(ms);
  }
  if (table.bool.includes(col)) {
    if (value === null || value === undefined) return null;
    return value !== 0;
  }
  return value ?? null;
}

async function verifyCounts(sqlite, client) {
  console.log('');
  console.log('table                sqlite  postgres  ok');
  let allOk = true;
  for (const table of TABLES) {
    const from = sqlite.prepare(`SELECT count(*) AS c FROM ${table.name}`).get().c;
    const { rows } = await client.query(`SELECT count(*)::int AS c FROM ${table.name}`);
    const to = rows[0].c;
    const ok = Number(from) === to;
    if (!ok) allOk = false;
    console.log(
      `${table.name.padEnd(20)} ${String(from).padStart(6)}  ${String(to).padStart(8)}  ${ok ? 'yes' : 'NO'}`,
    );
  }
  console.log('');
  if (!allOk) console.error('[migrate] row counts do not match');
  return allOk;
}

/**
 * The ASCII-smuggling prompt is the one row whose content cannot be eyeballed:
 * its payload lives in the Unicode Tags block and has no glyphs anywhere. Compare
 * the code points directly on both sides.
 */
async function verifyInjectionPayload(sqlite, client) {
  const before = sqlite
    .prepare('SELECT content FROM prompts WHERE title = ?')
    .get(INJECTION_TITLE);
  if (!before) {
    console.log('[migrate] injection payload check skipped (prompt not present)');
    return true;
  }

  const { rows } = await client.query('SELECT content FROM prompts WHERE title = $1', [
    INJECTION_TITLE,
  ]);
  if (rows.length !== 1) {
    console.error(`[migrate] injection payload check: expected 1 row, got ${rows.length}`);
    return false;
  }

  const a = [...before.content].map((c) => c.codePointAt(0));
  const b = [...rows[0].content].map((c) => c.codePointAt(0));
  const same = a.length === b.length && a.every((cp, i) => cp === b[i]);
  const tagChars = b.filter((cp) => cp >= 0xe0000).length;

  if (!same) {
    console.error(
      `[migrate] injection payload MISMATCH (sqlite ${a.length} code points, postgres ${b.length})`,
    );
    return false;
  }
  console.log(
    `[migrate] injection payload intact — ${b.length} code points, ${tagChars} in the Unicode Tags block`,
  );
  return true;
}

function redact(url) {
  try {
    const u = new URL(url);
    if (u.password) u.password = '***';
    return u.toString();
  } catch {
    return '(unparsable DATABASE_URL)';
  }
}
