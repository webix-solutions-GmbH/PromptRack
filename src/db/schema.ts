import {
  pgTable,
  text,
  integer,
  serial,
  boolean,
  timestamp,
  doublePrecision,
  primaryKey,
  unique,
} from 'drizzle-orm/pg-core';

// ---------------------------------------------------------------------------
// machines
// ---------------------------------------------------------------------------
export const machines = pgTable('machines', {
  id: serial('id').primaryKey(),
  name: text('name').notNull(),
  baseUrl: text('base_url').notNull(),
  apiKey: text('api_key'),
  cpu: text('cpu'),
  ram: text('ram'),
  gpu: text('gpu'),
  notes: text('notes'),
  createdAt: timestamp('created_at', { withTimezone: true, mode: 'date' }).notNull(),
  updatedAt: timestamp('updated_at', { withTimezone: true, mode: 'date' }).notNull(),
});

export type Machine = typeof machines.$inferSelect;
export type NewMachine = typeof machines.$inferInsert;

// ---------------------------------------------------------------------------
// machine_models
// ---------------------------------------------------------------------------
export const machineModels = pgTable(
  'machine_models',
  {
    id: serial('id').primaryKey(),
    machineId: integer('machine_id')
      .notNull()
      .references(() => machines.id, { onDelete: 'cascade' }),
    modelId: text('model_id').notNull(),
    currentlyLoaded: boolean('currently_loaded').notNull().default(false),
    firstSeenAt: timestamp('first_seen_at', { withTimezone: true, mode: 'date' }).notNull(),
    lastSeenAt: timestamp('last_seen_at', { withTimezone: true, mode: 'date' }).notNull(),
    source: text('source', { enum: ['discovered', 'manual', 'run'] }).notNull(),
  },
  (table) => [unique().on(table.machineId, table.modelId)],
);

export type MachineModel = typeof machineModels.$inferSelect;
export type NewMachineModel = typeof machineModels.$inferInsert;

// ---------------------------------------------------------------------------
// system_prompts
// ---------------------------------------------------------------------------
export const systemPrompts = pgTable('system_prompts', {
  id: serial('id').primaryKey(),
  name: text('name').notNull(),
  content: text('content').notNull(),
  createdAt: timestamp('created_at', { withTimezone: true, mode: 'date' }).notNull(),
  updatedAt: timestamp('updated_at', { withTimezone: true, mode: 'date' }).notNull(),
});

export type SystemPrompt = typeof systemPrompts.$inferSelect;
export type NewSystemPrompt = typeof systemPrompts.$inferInsert;

// ---------------------------------------------------------------------------
// toolsets
// ---------------------------------------------------------------------------
/**
 * A named bundle of tools a prompt can be run with. `manual` toolsets are
 * authored here and answer from `tools.mockResponse`; `mcp` toolsets import
 * their tools from an MCP server over HTTP and execute against it.
 */
export const toolsets = pgTable('toolsets', {
  id: serial('id').primaryKey(),
  name: text('name').notNull(),
  description: text('description'),
  kind: text('kind', { enum: ['manual', 'mcp'] })
    .notNull()
    .default('manual'),
  mcpUrl: text('mcp_url'),
  /** JSON object of extra request headers (auth), sent with every MCP call. */
  mcpHeaders: text('mcp_headers'),
  createdAt: timestamp('created_at', { withTimezone: true, mode: 'date' }).notNull(),
  updatedAt: timestamp('updated_at', { withTimezone: true, mode: 'date' }).notNull(),
});

export type Toolset = typeof toolsets.$inferSelect;
export type NewToolset = typeof toolsets.$inferInsert;

// ---------------------------------------------------------------------------
// tools
// ---------------------------------------------------------------------------
/**
 * One callable function. Like `machine_models`, MCP-discovered rows are
 * upserted and never deleted — a tool that disappears from `tools/list` only
 * flips `enabled` false, so past runs can still explain what they sent.
 */
export const tools = pgTable(
  'tools',
  {
    id: serial('id').primaryKey(),
    toolsetId: integer('toolset_id')
      .notNull()
      .references(() => toolsets.id, { onDelete: 'cascade' }),
    name: text('name').notNull(),
    description: text('description'),
    /** JSON Schema for the function's arguments. */
    parametersJson: text('parameters_json').notNull().default('{}'),
    /** Canned output returned instead of calling anything (manual toolsets). */
    mockResponse: text('mock_response'),
    enabled: boolean('enabled').notNull().default(true),
    source: text('source', { enum: ['manual', 'mcp'] })
      .notNull()
      .default('manual'),
    firstSeenAt: timestamp('first_seen_at', { withTimezone: true, mode: 'date' }).notNull(),
    lastSeenAt: timestamp('last_seen_at', { withTimezone: true, mode: 'date' }).notNull(),
  },
  (table) => [unique().on(table.toolsetId, table.name)],
);

export type Tool = typeof tools.$inferSelect;
export type NewTool = typeof tools.$inferInsert;

// ---------------------------------------------------------------------------
// prompt_groups
// ---------------------------------------------------------------------------
export const promptGroups = pgTable('prompt_groups', {
  id: serial('id').primaryKey(),
  name: text('name').notNull(),
  description: text('description'),
  sortOrder: integer('sort_order').notNull().default(0),
  createdAt: timestamp('created_at', { withTimezone: true, mode: 'date' }).notNull(),
});

export type PromptGroup = typeof promptGroups.$inferSelect;
export type NewPromptGroup = typeof promptGroups.$inferInsert;

// ---------------------------------------------------------------------------
// prompts
// ---------------------------------------------------------------------------
export const prompts = pgTable('prompts', {
  id: serial('id').primaryKey(),
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
  /**
   * 'none' keeps the classic one-shot behaviour, 'definitions' sends the tool
   * definitions and records what the model wanted to call without executing,
   * 'execute' runs the full loop.
   */
  toolMode: text('tool_mode', { enum: ['none', 'definitions', 'execute'] })
    .notNull()
    .default('none'),
  /** Null leaves `tool_choice` out of the request entirely. */
  toolChoice: text('tool_choice', { enum: ['auto', 'required', 'none'] }),
  maxTurns: integer('max_turns').notNull().default(6),
  sortOrder: integer('sort_order').notNull().default(0),
  createdAt: timestamp('created_at', { withTimezone: true, mode: 'date' }).notNull(),
  updatedAt: timestamp('updated_at', { withTimezone: true, mode: 'date' }).notNull(),
});

export type Prompt = typeof prompts.$inferSelect;
export type NewPrompt = typeof prompts.$inferInsert;

// ---------------------------------------------------------------------------
// prompt_toolsets
// ---------------------------------------------------------------------------
/** Which toolsets a prompt pulls in. A prompt may combine several sources. */
export const promptToolsets = pgTable(
  'prompt_toolsets',
  {
    promptId: integer('prompt_id')
      .notNull()
      .references(() => prompts.id, { onDelete: 'cascade' }),
    toolsetId: integer('toolset_id')
      .notNull()
      .references(() => toolsets.id, { onDelete: 'cascade' }),
    sortOrder: integer('sort_order').notNull().default(0),
  },
  (table) => [primaryKey({ columns: [table.promptId, table.toolsetId] })],
);

export type PromptToolset = typeof promptToolsets.$inferSelect;
export type NewPromptToolset = typeof promptToolsets.$inferInsert;

// ---------------------------------------------------------------------------
// runs
// ---------------------------------------------------------------------------
export const runs = pgTable('runs', {
  id: serial('id').primaryKey(),
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
  /**
   * When the run was archived, or null. Deliberately *not* a `status` value:
   * status is the execution state machine (pending → running → completed /
   * failed) that Resume depends on, so a half-finished run has to stay
   * `pending` while archived. The UI still presents archiving as a state.
   */
  archivedAt: timestamp('archived_at', { withTimezone: true, mode: 'date' }),
  createdAt: timestamp('created_at', { withTimezone: true, mode: 'date' }).notNull(),
  startedAt: timestamp('started_at', { withTimezone: true, mode: 'date' }),
  finishedAt: timestamp('finished_at', { withTimezone: true, mode: 'date' }),
});

export type Run = typeof runs.$inferSelect;
export type NewRun = typeof runs.$inferInsert;

// ---------------------------------------------------------------------------
// run_results
// ---------------------------------------------------------------------------
export const runResults = pgTable('run_results', {
  id: serial('id').primaryKey(),
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
  /**
   * Tool configuration frozen at run creation. `toolsSnapshot` is the exact
   * JSON array of definitions sent to the model — editing or deleting a
   * toolset afterwards can never rewrite what a past run asked for.
   */
  toolsSnapshot: text('tools_snapshot'),
  toolMode: text('tool_mode', { enum: ['none', 'definitions', 'execute'] })
    .notNull()
    .default('none'),
  toolChoice: text('tool_choice', { enum: ['auto', 'required', 'none'] }),
  maxTurns: integer('max_turns').notNull().default(6),
  status: text('status').notNull().default('pending'),
  /** Final assistant text — unchanged meaning, tool runs or not. */
  responseText: text('response_text'),
  /** Full message array of a tool run (assistant, tool_calls, tool results). */
  transcriptJson: text('transcript_json'),
  /** Per-turn metrics array; the columns below are its aggregates. */
  turnsJson: text('turns_json'),
  turnCount: integer('turn_count'),
  toolCallCount: integer('tool_call_count'),
  stoppedReason: text('stopped_reason', {
    enum: ['stop', 'max_turns', 'definitions_only'],
  }),
  error: text('error'),
  durationMs: integer('duration_ms'),
  ttftMs: integer('ttft_ms'),
  promptTokens: integer('prompt_tokens'),
  completionTokens: integer('completion_tokens'),
  tokensPerSec: doublePrecision('tokens_per_sec'),
  tokensEstimated: boolean('tokens_estimated').notNull().default(false),
  /** Manual verdict: `good`, `meh` (not wrong but not good enough) or `bad`. */
  rating: text('rating', { enum: ['good', 'meh', 'bad'] }),
  ratingNote: text('rating_note'),
  startedAt: timestamp('started_at', { withTimezone: true, mode: 'date' }),
  finishedAt: timestamp('finished_at', { withTimezone: true, mode: 'date' }),
});

export type RunResult = typeof runResults.$inferSelect;
export type NewRunResult = typeof runResults.$inferInsert;

// ---------------------------------------------------------------------------
// __app_seeds — ledger owned by scripts/seed-prompts.mjs
//
// Not application data: it records which seeded toolsets/prompt groups have ever
// been inserted, so seeding is additive and respects deletions. It lives here (and
// not only in the seed script) so migration tooling knows it exists and can never
// offer to drop it. `scope` is the group name for a prompt, empty for a toolset.
// ---------------------------------------------------------------------------
export const appSeeds = pgTable(
  '__app_seeds',
  {
    kind: text('kind').notNull(),
    scope: text('scope').notNull(),
    name: text('name').notNull(),
    seededAt: timestamp('seeded_at', { withTimezone: true, mode: 'date' }).notNull(),
  },
  (table) => [primaryKey({ columns: [table.kind, table.scope, table.name] })],
);
