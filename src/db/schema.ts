import {
  sqliteTable,
  text,
  integer,
  real,
  unique,
} from 'drizzle-orm/sqlite-core';

// ---------------------------------------------------------------------------
// machines
// ---------------------------------------------------------------------------
export const machines = sqliteTable('machines', {
  id: integer('id').primaryKey({ autoIncrement: true }),
  name: text('name').notNull(),
  baseUrl: text('base_url').notNull(),
  apiKey: text('api_key'),
  cpu: text('cpu'),
  ram: text('ram'),
  gpu: text('gpu'),
  notes: text('notes'),
  createdAt: integer('created_at', { mode: 'number' }).notNull(),
  updatedAt: integer('updated_at', { mode: 'number' }).notNull(),
});

export type Machine = typeof machines.$inferSelect;
export type NewMachine = typeof machines.$inferInsert;

// ---------------------------------------------------------------------------
// machine_models
// ---------------------------------------------------------------------------
export const machineModels = sqliteTable(
  'machine_models',
  {
    id: integer('id').primaryKey({ autoIncrement: true }),
    machineId: integer('machine_id')
      .notNull()
      .references(() => machines.id, { onDelete: 'cascade' }),
    modelId: text('model_id').notNull(),
    currentlyLoaded: integer('currently_loaded', { mode: 'boolean' })
      .notNull()
      .default(false),
    firstSeenAt: integer('first_seen_at', { mode: 'number' }).notNull(),
    lastSeenAt: integer('last_seen_at', { mode: 'number' }).notNull(),
    source: text('source', { enum: ['discovered', 'manual', 'run'] }).notNull(),
  },
  (table) => [unique().on(table.machineId, table.modelId)],
);

export type MachineModel = typeof machineModels.$inferSelect;
export type NewMachineModel = typeof machineModels.$inferInsert;

// ---------------------------------------------------------------------------
// system_prompts
// ---------------------------------------------------------------------------
export const systemPrompts = sqliteTable('system_prompts', {
  id: integer('id').primaryKey({ autoIncrement: true }),
  name: text('name').notNull(),
  content: text('content').notNull(),
  createdAt: integer('created_at', { mode: 'number' }).notNull(),
  updatedAt: integer('updated_at', { mode: 'number' }).notNull(),
});

export type SystemPrompt = typeof systemPrompts.$inferSelect;
export type NewSystemPrompt = typeof systemPrompts.$inferInsert;

// ---------------------------------------------------------------------------
// prompt_groups
// ---------------------------------------------------------------------------
export const promptGroups = sqliteTable('prompt_groups', {
  id: integer('id').primaryKey({ autoIncrement: true }),
  name: text('name').notNull(),
  description: text('description'),
  sortOrder: integer('sort_order').notNull().default(0),
  createdAt: integer('created_at', { mode: 'number' }).notNull(),
});

export type PromptGroup = typeof promptGroups.$inferSelect;
export type NewPromptGroup = typeof promptGroups.$inferInsert;

// ---------------------------------------------------------------------------
// prompts
// ---------------------------------------------------------------------------
export const prompts = sqliteTable('prompts', {
  id: integer('id').primaryKey({ autoIncrement: true }),
  groupId: integer('group_id')
    .notNull()
    .references(() => promptGroups.id, { onDelete: 'cascade' }),
  title: text('title').notNull(),
  content: text('content').notNull(),
  expectedOutput: text('expected_output'),
  systemPromptId: integer('system_prompt_id').references(() => systemPrompts.id, {
    onDelete: 'set null',
  }),
  systemPromptMode: text('system_prompt_mode').notNull().default('append'),
  customSystemText: text('custom_system_text'),
  sortOrder: integer('sort_order').notNull().default(0),
  createdAt: integer('created_at', { mode: 'number' }).notNull(),
  updatedAt: integer('updated_at', { mode: 'number' }).notNull(),
});

export type Prompt = typeof prompts.$inferSelect;
export type NewPrompt = typeof prompts.$inferInsert;

// ---------------------------------------------------------------------------
// runs
// ---------------------------------------------------------------------------
export const runs = sqliteTable('runs', {
  id: integer('id').primaryKey({ autoIncrement: true }),
  machineId: integer('machine_id').references(() => machines.id, {
    onDelete: 'set null',
  }),
  machineSnapshot: text('machine_snapshot').notNull(),
  modelId: text('model_id').notNull(),
  params: text('params'),
  comment: text('comment'),
  groupNames: text('group_names').notNull(),
  /** JSON snapshot of endpoint/server/model metadata probed at creation time. */
  llmInfo: text('llm_info'),
  status: text('status').notNull().default('pending'),
  createdAt: integer('created_at', { mode: 'number' }).notNull(),
  startedAt: integer('started_at', { mode: 'number' }),
  finishedAt: integer('finished_at', { mode: 'number' }),
});

export type Run = typeof runs.$inferSelect;
export type NewRun = typeof runs.$inferInsert;

// ---------------------------------------------------------------------------
// run_results
// ---------------------------------------------------------------------------
export const runResults = sqliteTable('run_results', {
  id: integer('id').primaryKey({ autoIncrement: true }),
  runId: integer('run_id')
    .notNull()
    .references(() => runs.id, { onDelete: 'cascade' }),
  promptId: integer('prompt_id').references(() => prompts.id, {
    onDelete: 'set null',
  }),
  sortOrder: integer('sort_order').notNull().default(0),
  groupName: text('group_name').notNull(),
  promptTitle: text('prompt_title').notNull(),
  promptText: text('prompt_text').notNull(),
  expectedOutput: text('expected_output'),
  systemPromptText: text('system_prompt_text'),
  status: text('status').notNull().default('pending'),
  responseText: text('response_text'),
  error: text('error'),
  durationMs: integer('duration_ms'),
  ttftMs: integer('ttft_ms'),
  promptTokens: integer('prompt_tokens'),
  completionTokens: integer('completion_tokens'),
  tokensPerSec: real('tokens_per_sec'),
  tokensEstimated: integer('tokens_estimated', { mode: 'boolean' })
    .notNull()
    .default(false),
  rating: text('rating'),
  ratingNote: text('rating_note'),
  startedAt: integer('started_at', { mode: 'number' }),
  finishedAt: integer('finished_at', { mode: 'number' }),
});

export type RunResult = typeof runResults.$inferSelect;
export type NewRunResult = typeof runResults.$inferInsert;
