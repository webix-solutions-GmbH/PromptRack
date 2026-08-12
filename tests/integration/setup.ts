import { afterAll, beforeEach } from 'vitest';
import { pool } from '@/db';

/** Every table the app owns, in one statement so CASCADE resolves the FKs. */
export const ALL_TABLES = [
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
  '__app_seeds',
] as const;

beforeEach(async () => {
  await pool.query(`TRUNCATE ${ALL_TABLES.join(', ')} RESTART IDENTITY CASCADE`);
});

afterAll(async () => {
  await pool.end();
});
