import { sql } from 'drizzle-orm';
import {
  pgTable,
  text,
  integer,
  serial,
  boolean,
  timestamp,
  doublePrecision,
  index,
  primaryKey,
  unique,
  uniqueIndex,
} from 'drizzle-orm/pg-core';

// ---------------------------------------------------------------------------
// customers
// ---------------------------------------------------------------------------
/**
 * A customer workspace.
 *
 * Not a tenant: customers never log in, and every team member can switch into
 * any workspace. It is the label that keeps one engagement's machines, prompts
 * and runs from mixing with another's — which matters most for machines, since
 * each engagement registers its own endpoints with its own API keys.
 *
 * The name is unique case-insensitively because MCP callers name a workspace and
 * `resolveRowRef` refuses an ambiguous name rather than guessing: two workspaces
 * differing only in case would make every by-name call fail.
 */
export const customers = pgTable(
  'customers',
  {
    id: serial('id').primaryKey(),
    name: text('name').notNull(),
    description: text('description'),
    /** Hidden from the switcher without destroying anything it owns. */
    archivedAt: timestamp('archived_at', { withTimezone: true, mode: 'date' }),
    createdAt: timestamp('created_at', { withTimezone: true, mode: 'date' }).notNull(),
    updatedAt: timestamp('updated_at', { withTimezone: true, mode: 'date' }).notNull(),
  },
  (table) => [uniqueIndex('customers_name_lower_idx').on(sql`lower(${table.name})`)],
);

export type Customer = typeof customers.$inferSelect;
export type NewCustomer = typeof customers.$inferInsert;

// ---------------------------------------------------------------------------
// machines
// ---------------------------------------------------------------------------
export const machines = pgTable(
  'machines',
  {
    id: serial('id').primaryKey(),
    /**
     * The workspace this row belongs to. `restrict`, never `cascade`: deleting a
     * workspace must not silently destroy run history — archiving is the soft
     * path, and `deleteCustomer` refuses while content exists.
     */
    customerId: integer('customer_id')
      .notNull()
      .references(() => customers.id, { onDelete: 'restrict' }),
    name: text('name').notNull(),
    baseUrl: text('base_url').notNull(),
    apiKey: text('api_key'),
    cpu: text('cpu'),
    ram: text('ram'),
    gpu: text('gpu'),
    notes: text('notes'),
    createdAt: timestamp('created_at', { withTimezone: true, mode: 'date' }).notNull(),
    updatedAt: timestamp('updated_at', { withTimezone: true, mode: 'date' }).notNull(),
  },
  (table) => [index('machines_customer_id_idx').on(table.customerId)],
);

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
export const systemPrompts = pgTable(
  'system_prompts',
  {
    id: serial('id').primaryKey(),
    customerId: integer('customer_id')
      .notNull()
      .references(() => customers.id, { onDelete: 'restrict' }),
    name: text('name').notNull(),
    content: text('content').notNull(),
    createdAt: timestamp('created_at', { withTimezone: true, mode: 'date' }).notNull(),
    updatedAt: timestamp('updated_at', { withTimezone: true, mode: 'date' }).notNull(),
  },
  // `(customer_id, name)` rather than `(customer_id)` alone: the layer looks
  // these up by name inside a workspace, and a btree on the pair already serves
  // a customer-only predicate as its leftmost prefix. Non-unique on purpose —
  // duplicate names may already exist, and app code produces a better message.
  (table) => [index('system_prompts_customer_name_idx').on(table.customerId, table.name)],
);

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
export const toolsets = pgTable(
  'toolsets',
  {
    id: serial('id').primaryKey(),
    customerId: integer('customer_id')
      .notNull()
      .references(() => customers.id, { onDelete: 'restrict' }),
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
  },
  (table) => [index('toolsets_customer_name_idx').on(table.customerId, table.name)],
);

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
export const promptGroups = pgTable(
  'prompt_groups',
  {
    id: serial('id').primaryKey(),
    customerId: integer('customer_id')
      .notNull()
      .references(() => customers.id, { onDelete: 'restrict' }),
    name: text('name').notNull(),
    description: text('description'),
    sortOrder: integer('sort_order').notNull().default(0),
    createdAt: timestamp('created_at', { withTimezone: true, mode: 'date' }).notNull(),
  },
  (table) => [index('prompt_groups_customer_name_idx').on(table.customerId, table.name)],
);

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
export const runs = pgTable(
  'runs',
  {
    id: serial('id').primaryKey(),
    customerId: integer('customer_id')
      .notNull()
      .references(() => customers.id, { onDelete: 'restrict' }),
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
  },
  (table) => [index('runs_customer_id_idx').on(table.customerId)],
);

export type Run = typeof runs.$inferSelect;
export type NewRun = typeof runs.$inferInsert;

// ---------------------------------------------------------------------------
// run_results
// ---------------------------------------------------------------------------
export const runResults = pgTable(
  'run_results',
  {
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
  },
  (table) => [
    index('run_results_run_id_idx').on(table.runId),
    index('run_results_prompt_id_idx').on(table.promptId),
  ],
);

export type RunResult = typeof runResults.$inferSelect;
export type NewRunResult = typeof runResults.$inferInsert;

// ---------------------------------------------------------------------------
// __app_seeds — ledger owned by scripts/seed-prompts.mjs
//
// Not application data: it records which seeded toolsets/prompt groups have ever
// been inserted, so seeding is additive and respects deletions. It lives here (and
// not only in the seed script) so migration tooling knows it exists and can never
// offer to drop it. `scope` is the group name for a prompt, empty for a toolset.
//
// The ledger is keyed per workspace: seeding the standard suite into a new
// engagement's workspace must not be suppressed by a prompt someone deleted in
// another one. `cascade` and not `restrict` here — this is bookkeeping about a
// workspace, not content, and it must never be what blocks a delete.
// ---------------------------------------------------------------------------
export const appSeeds = pgTable(
  '__app_seeds',
  {
    customerId: integer('customer_id')
      .notNull()
      .references(() => customers.id, { onDelete: 'cascade' }),
    kind: text('kind').notNull(),
    scope: text('scope').notNull(),
    name: text('name').notNull(),
    seededAt: timestamp('seeded_at', { withTimezone: true, mode: 'date' }).notNull(),
  },
  (table) => [primaryKey({ columns: [table.customerId, table.kind, table.scope, table.name] })],
);

// ---------------------------------------------------------------------------
// auth (better-auth core tables — table names are better-auth's defaults)
//
// Global infrastructure rather than workspace data: these tables are what a
// scope is derived *from*, so they are read through src/lib/auth* and never
// through the scoped repositories in src/db/repo.
// ---------------------------------------------------------------------------
export const users = pgTable('user', {
  id: text('id').primaryKey(),
  name: text('name').notNull(),
  email: text('email').notNull().unique(),
  emailVerified: boolean('email_verified').notNull().default(false),
  image: text('image'),
  /** App role. `viewer` is the safe default for anything we did not stamp. */
  role: text('role', { enum: ['admin', 'member', 'viewer'] })
    .notNull()
    .default('viewer'),
  /**
   * The workspace this user is currently in. Nullable and `set null`, so
   * archiving or deleting a workspace logs its users into the fallback rather
   * than breaking their session; `resolveActiveCustomerId` does the falling
   * back and `currentScope()` heals the stored value.
   */
  activeCustomerId: integer('active_customer_id').references(() => customers.id, {
    onDelete: 'set null',
  }),
  // better-auth admin plugin fields:
  banned: boolean('banned').notNull().default(false),
  banReason: text('ban_reason'),
  banExpires: timestamp('ban_expires', { withTimezone: true, mode: 'date' }),
  createdAt: timestamp('created_at', { withTimezone: true, mode: 'date' }).notNull().defaultNow(),
  updatedAt: timestamp('updated_at', { withTimezone: true, mode: 'date' }).notNull().defaultNow(),
});

export const sessions = pgTable(
  'session',
  {
    id: text('id').primaryKey(),
    token: text('token').notNull().unique(),
    userId: text('user_id')
      .notNull()
      .references(() => users.id, { onDelete: 'cascade' }),
    expiresAt: timestamp('expires_at', { withTimezone: true, mode: 'date' }).notNull(),
    ipAddress: text('ip_address'),
    userAgent: text('user_agent'),
    impersonatedBy: text('impersonated_by'),
    createdAt: timestamp('created_at', { withTimezone: true, mode: 'date' }).notNull().defaultNow(),
    updatedAt: timestamp('updated_at', { withTimezone: true, mode: 'date' }).notNull().defaultNow(),
  },
  (table) => [index('session_user_id_idx').on(table.userId)],
);

export const accounts = pgTable(
  'account',
  {
    id: text('id').primaryKey(),
    accountId: text('account_id').notNull(),
    providerId: text('provider_id').notNull(),
    userId: text('user_id')
      .notNull()
      .references(() => users.id, { onDelete: 'cascade' }),
    accessToken: text('access_token'),
    refreshToken: text('refresh_token'),
    idToken: text('id_token'),
    accessTokenExpiresAt: timestamp('access_token_expires_at', {
      withTimezone: true,
      mode: 'date',
    }),
    refreshTokenExpiresAt: timestamp('refresh_token_expires_at', {
      withTimezone: true,
      mode: 'date',
    }),
    scope: text('scope'),
    password: text('password'),
    createdAt: timestamp('created_at', { withTimezone: true, mode: 'date' }).notNull().defaultNow(),
    updatedAt: timestamp('updated_at', { withTimezone: true, mode: 'date' }).notNull().defaultNow(),
  },
  (table) => [index('account_user_id_idx').on(table.userId)],
);

export const verifications = pgTable(
  'verification',
  {
    id: text('id').primaryKey(),
    identifier: text('identifier').notNull(),
    value: text('value').notNull(),
    expiresAt: timestamp('expires_at', { withTimezone: true, mode: 'date' }).notNull(),
    createdAt: timestamp('created_at', { withTimezone: true, mode: 'date' }).notNull().defaultNow(),
    updatedAt: timestamp('updated_at', { withTimezone: true, mode: 'date' }).notNull().defaultNow(),
  },
  (table) => [index('verification_identifier_idx').on(table.identifier)],
);

export type User = typeof users.$inferSelect;
export type NewUser = typeof users.$inferInsert;

// ---------------------------------------------------------------------------
// api_tokens — per-user bearer tokens for /api/mcp (hashed at rest)
// ---------------------------------------------------------------------------
export const apiTokens = pgTable(
  'api_tokens',
  {
    id: text('id').primaryKey(),
    userId: text('user_id')
      .notNull()
      .references(() => users.id, { onDelete: 'cascade' }),
    name: text('name').notNull(),
    /** SHA-256 hex of the raw token. The raw value is shown exactly once. */
    tokenHash: text('token_hash').notNull().unique(),
    /** First 12 chars of the raw token, for recognising it in a list. */
    prefix: text('prefix').notNull(),
    createdAt: timestamp('created_at', { withTimezone: true, mode: 'date' }).notNull().defaultNow(),
    lastUsedAt: timestamp('last_used_at', { withTimezone: true, mode: 'date' }),
    expiresAt: timestamp('expires_at', { withTimezone: true, mode: 'date' }),
    revokedAt: timestamp('revoked_at', { withTimezone: true, mode: 'date' }),
  },
  (table) => [index('api_tokens_user_id_idx').on(table.userId)],
);

export type ApiToken = typeof apiTokens.$inferSelect;
export type NewApiToken = typeof apiTokens.$inferInsert;
