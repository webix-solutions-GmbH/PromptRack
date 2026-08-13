import { afterAll, beforeEach } from 'vitest';
import { pool } from '@/db';
import { scopeFromCustomerId, type Scope } from '@/db/scope';

/** Every table the app owns, in one statement so CASCADE resolves the FKs. */
export const ALL_TABLES = [
  'customers',
  'machines',
  'machine_models',
  'system_prompts',
  'toolsets',
  'tools',
  'prompt_groups',
  'prompts',
  'prompt_toolsets',
  'runs',
  'run_results',
] as const;

let defaultId = 0;

/**
 * Creates a workspace and hands back the scope every repository call needs.
 *
 * `scopeFromCustomerId` is the same constructor the background executor uses, so
 * a test scope is indistinguishable from a real one — there is no test-only way
 * to make a `Scope`.
 */
export async function createWorkspace(name: string): Promise<{ id: number; scope: Scope }> {
  const { rows } = await pool.query(
    `INSERT INTO customers (name, description, created_at, updated_at)
     VALUES ($1, NULL, now(), now()) RETURNING id`,
    [name],
  );
  const id: number = rows[0].id;
  return { id, scope: scopeFromCustomerId(id) };
}

/** The workspace every test starts with — the migration's `Default` in miniature. */
export function defaultCustomerId(): number {
  return defaultId;
}

export function defaultScope(): Scope {
  return scopeFromCustomerId(defaultId);
}

beforeEach(async () => {
  await pool.query(`TRUNCATE ${ALL_TABLES.join(', ')} RESTART IDENTITY CASCADE`);
  // Production can never be without a workspace (the migration creates one and
  // `deleteCustomer` refuses the last), so neither is a fixture.
  const { rows } = await pool.query(
    `INSERT INTO customers (name, description, created_at, updated_at)
     VALUES ('Default', 'Fixture workspace.', now(), now()) RETURNING id`,
  );
  defaultId = rows[0].id;
});

afterAll(async () => {
  await pool.end();
});
