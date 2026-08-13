/**
 * MCP tools for authoring the evaluation: prompt groups, system prompts and
 * prompts (including tool tests).
 *
 * These are the write side of the app's own UI, reachable from an agent — the
 * point being that another project's real system prompts and prompts can be
 * pushed in from that project's repository instead of retyped here.
 *
 * Validation deliberately mirrors the prompt editor rather than trusting the
 * caller: a tool test with no tools, or two toolsets defining the same tool
 * name, is refused here as well, so a prompt written over MCP can never be one
 * that `createRun` would later reject.
 */

import { revalidatePath } from 'next/cache';
import type { Prompt } from '@/db/schema';
import type { Scope } from '@/db/scope';
import {
  createGroup,
  createPrompt as createPromptRow,
  deletePrompt as deletePromptRow,
  getPrompt as getPromptRow,
  listGroups,
  listPromptToolsetViews,
  listPrompts as listPromptRows,
  listToolsetLinks,
  promptCountsByGroup,
  replaceToolsetLinks,
  updatePrompt as updatePromptRow,
} from '@/db/repo/prompts';
import {
  createSystemPrompt as createSystemPromptRow,
  listSystemPrompts as listSystemPromptRows,
  updateSystemPrompt as updateSystemPromptRow,
} from '@/db/repo/system-prompts';
import { listTools, listToolsets as listToolsetRows } from '@/db/repo/toolsets';
import { resolveEffectiveSystemPrompt, type SystemPromptMode } from '@/lib/system-prompt';
import {
  collectToolNameCollisions,
  normalizeMaxTurns,
  type ToolChoice,
  type ToolMode,
} from '@/lib/tools';
import {
  McpToolError,
  hasKey,
  optionalBoolean,
  optionalEnum,
  optionalInteger,
  optionalRowRef,
  optionalRowRefList,
  optionalString,
  optionalText,
  requireInteger,
  requireRowRef,
  requireString,
  requireText,
  resolveRowRef,
  truncate,
  type RowRef,
  type ToolArgs,
} from './args';
import { CUSTOMER_ARG, resolveMcpScope } from './customer';
import type { McpCallContext, McpToolSpec } from './protocol';

const TOOL_MODES = ['none', 'definitions', 'execute'] as const;
const TOOL_CHOICES = ['auto', 'required', 'none'] as const;
const SYSTEM_PROMPT_MODES = ['append', 'override'] as const;

/** Keeps the UI in step with a write that happened outside it. */
function revalidateAuthoring() {
  revalidatePath('/prompts');
  revalidatePath('/system-prompts');
  revalidatePath('/');
}

// ---------------------------------------------------------------------------
// Shared lookups
// ---------------------------------------------------------------------------

async function allGroups(scope: Scope) {
  return listGroups(scope, 'sort-id');
}

async function allSystemPrompts(scope: Scope) {
  return listSystemPromptRows(scope, 'name');
}

async function allToolsets(scope: Scope) {
  return listToolsetRows(scope);
}

async function resolveGroup(scope: Scope, ref: RowRef) {
  return resolveRowRef(ref, await allGroups(scope), 'prompt group');
}

async function resolveSystemPrompt(scope: Scope, ref: RowRef) {
  return resolveRowRef(ref, await allSystemPrompts(scope), 'system prompt');
}

/** Resolves toolset refs and reports which of their tools are usable. */
async function resolveToolsets(scope: Scope, refs: RowRef[]) {
  const rows = await allToolsets(scope);
  const resolved = refs.map((ref) => resolveRowRef(ref, rows, 'toolset'));

  const toolRows = await listTools(scope, { toolsetIds: resolved.map((row) => row.id) });

  return resolved.map((toolset) => ({
    ...toolset,
    tools: toolRows.filter((tool) => tool.toolsetId === toolset.id),
  }));
}

/**
 * The tool configuration a prompt would send, checked the way the editor checks
 * it. Returns nothing; throws with the fix when the combination is unusable.
 */
async function assertToolConfig(scope: Scope, toolMode: ToolMode, toolsetRefs: RowRef[]) {
  if (toolMode === 'none') return;

  if (toolsetRefs.length === 0) {
    throw new McpToolError(
      `tool_mode "${toolMode}" needs at least one toolset. Pass "toolsets", or use tool_mode "none".`,
    );
  }

  const resolved = await resolveToolsets(scope, toolsetRefs);
  const offered = resolved.flatMap((toolset) =>
    toolset.tools.filter((tool) => tool.enabled).map((tool) => ({ name: tool.name })),
  );

  if (offered.length === 0) {
    throw new McpToolError(
      `The selected toolsets (${resolved
        .map((toolset) => toolset.name)
        .join(', ')}) contain no enabled tools.`,
    );
  }

  const collisions = collectToolNameCollisions(offered);
  if (collisions.length > 0) {
    throw new McpToolError(
      `Two selected toolsets both define: ${collisions.join(', ')}. Tool names must be unique within one prompt.`,
    );
  }
}

// ---------------------------------------------------------------------------
// Prompt views
// ---------------------------------------------------------------------------

interface PromptViewOptions {
  includeContent: boolean;
  maxContentChars: number;
}

/**
 * One prompt as an MCP client sees it — including the *resolved* system prompt,
 * so a client can check what a run would actually send without reimplementing
 * append/override itself.
 */
async function promptViews(scope: Scope, rows: Prompt[], options: PromptViewOptions) {
  if (rows.length === 0) return [];

  const groups = await allGroups(scope);
  const groupById = new Map(groups.map((group) => [group.id, group]));
  const systemPromptRows = await allSystemPrompts(scope);
  const systemPromptById = new Map(systemPromptRows.map((row) => [row.id, row]));

  const links = await listPromptToolsetViews(
    scope,
    rows.map((row) => row.id),
  );

  return rows.map((row) => {
    const base = row.systemPromptId
      ? (systemPromptById.get(row.systemPromptId)?.content ?? null)
      : null;
    const effective = resolveEffectiveSystemPrompt({
      mode: row.systemPromptMode as SystemPromptMode,
      baseContent: base,
      customText: row.customSystemText,
    });
    const content = options.includeContent
      ? truncate(row.content, options.maxContentChars)
      : { text: null, truncated: false };

    return {
      id: row.id,
      title: row.title,
      group: {
        id: row.groupId,
        name: groupById.get(row.groupId)?.name ?? null,
      },
      content: content.text,
      content_truncated: content.truncated,
      expected_output: row.expectedOutput,
      system_prompt: row.systemPromptId
        ? {
            id: row.systemPromptId,
            name: systemPromptById.get(row.systemPromptId)?.name ?? null,
          }
        : null,
      system_prompt_mode: row.systemPromptMode,
      custom_system_text: row.customSystemText,
      effective_system_prompt: effective,
      tool_mode: row.toolMode,
      tool_choice: row.toolChoice,
      max_turns: row.maxTurns,
      toolsets: links
        .filter((link) => link.promptId === row.id)
        .map((link) => ({ id: link.toolsetId, name: link.name, kind: link.kind })),
      created_at: row.createdAt.getTime(),
      updated_at: row.updatedAt.getTime(),
    };
  });
}

async function promptViewById(scope: Scope, id: number) {
  const row = await getPromptRow(scope, id);
  if (!row) {
    throw new McpToolError(`No prompt with id ${id}.`);
  }
  const [view] = await promptViews(scope, [row], { includeContent: true, maxContentChars: 0 });
  return view;
}

// ---------------------------------------------------------------------------
// Tools
// ---------------------------------------------------------------------------

const listPromptGroups: McpToolSpec = {
  name: 'list_prompt_groups',
  description:
    'List every prompt group with its prompt count. Groups are what a run selects, so a set of prompts that should be run together belongs in one group.',
  readOnly: true,
  inputSchema: { type: 'object', properties: { customer: CUSTOMER_ARG } },
  handler: async (args: ToolArgs, ctx: McpCallContext) => {
    const scope = await resolveMcpScope(args, ctx.source);
    const groups = await allGroups(scope);
    const counts = await promptCountsByGroup(scope);

    return {
      groups: groups.map((group) => ({
        id: group.id,
        name: group.name,
        description: group.description,
        prompt_count: counts.get(group.id) ?? 0,
        created_at: group.createdAt.getTime(),
      })),
    };
  },
};

const createPromptGroup: McpToolSpec = {
  name: 'create_prompt_group',
  description:
    'Create a prompt group. If a group with the same name already exists it is returned unchanged (created: false), so pushing the same set of prompts twice does not produce duplicate groups.',
  inputSchema: {
    type: 'object',
    properties: {
      customer: CUSTOMER_ARG,
      name: { type: 'string', description: 'Group name, e.g. "Odoo helpdesk replies".' },
      description: {
        type: 'string',
        description: 'What this group of prompts is testing, and for which app.',
      },
    },
    required: ['name'],
  },
  handler: async (args: ToolArgs, ctx: McpCallContext) => {
    const scope = await resolveMcpScope(args, ctx.source);
    const name = requireString(args, 'name');
    const description = optionalString(args, 'description');

    const existing = (await allGroups(scope)).find(
      (group) => group.name.trim().toLowerCase() === name.toLowerCase(),
    );
    if (existing) {
      return {
        created: false,
        group: {
          id: existing.id,
          name: existing.name,
          description: existing.description,
        },
      };
    }

    const row = await createGroup(scope, { name, description, now: new Date() });

    revalidateAuthoring();
    return { created: true, group: { id: row.id, name, description } };
  },
};

const listSystemPrompts: McpToolSpec = {
  name: 'list_system_prompts',
  description:
    'List the reusable system prompts with their full content. A prompt may reference one of these as its base and then append to or override it.',
  readOnly: true,
  inputSchema: { type: 'object', properties: { customer: CUSTOMER_ARG } },
  handler: async (args: ToolArgs, ctx: McpCallContext) => {
    const rows = await allSystemPrompts(await resolveMcpScope(args, ctx.source));
    return {
      system_prompts: rows.map((row) => ({
        id: row.id,
        name: row.name,
        content: row.content,
        created_at: row.createdAt.getTime(),
        updated_at: row.updatedAt.getTime(),
      })),
    };
  },
};

const createSystemPrompt: McpToolSpec = {
  name: 'create_system_prompt',
  description:
    'Create a reusable system prompt — typically the real system prompt of the app whose prompts you are pushing. Fails if the name is taken; use update_system_prompt to change an existing one.',
  inputSchema: {
    type: 'object',
    properties: {
      customer: CUSTOMER_ARG,
      name: { type: 'string', description: 'Short label, e.g. "Helpdesk agent (prod)".' },
      content: { type: 'string', description: 'The system prompt itself, verbatim.' },
    },
    required: ['name', 'content'],
  },
  handler: async (args: ToolArgs, ctx: McpCallContext) => {
    const scope = await resolveMcpScope(args, ctx.source);
    const name = requireString(args, 'name');
    const content = requireText(args, 'content');

    const existing = (await allSystemPrompts(scope)).find(
      (row) => row.name.trim().toLowerCase() === name.toLowerCase(),
    );
    if (existing) {
      throw new McpToolError(
        `A system prompt named "${existing.name}" already exists (id ${existing.id}). Use update_system_prompt to change it.`,
      );
    }

    const row = await createSystemPromptRow(scope, { name, content, now: new Date() });

    revalidateAuthoring();
    return { system_prompt: { id: row.id, name, content } };
  },
};

const updateSystemPrompt: McpToolSpec = {
  name: 'update_system_prompt',
  description:
    'Change a system prompt in place. Past runs are unaffected: each run froze the resolved system prompt it actually sent.',
  inputSchema: {
    type: 'object',
    properties: {
      customer: CUSTOMER_ARG,
      system_prompt: {
        type: ['string', 'integer'],
        description: 'Name or id of the system prompt to change.',
      },
      name: { type: 'string', description: 'New name.' },
      content: { type: 'string', description: 'New content.' },
    },
    required: ['system_prompt'],
  },
  handler: async (args: ToolArgs, ctx: McpCallContext) => {
    const scope = await resolveMcpScope(args, ctx.source);
    const target = await resolveSystemPrompt(scope, requireRowRef(args, 'system_prompt'));
    const name = hasKey(args, 'name') ? requireString(args, 'name') : target.name;
    const content = hasKey(args, 'content') ? requireText(args, 'content') : target.content;

    await updateSystemPromptRow(scope, target.id, { name, content, now: new Date() });

    revalidateAuthoring();
    return { system_prompt: { id: target.id, name, content } };
  },
};

const listToolsets: McpToolSpec = {
  name: 'list_toolsets',
  description:
    'List the toolsets a prompt can offer to the model, with their tool names. `manual` toolsets answer with a canned response (deterministic); `mcp` toolsets are really executed against an MCP server. Toolsets and their tools are authored in the web UI, not over this API.',
  readOnly: true,
  inputSchema: { type: 'object', properties: { customer: CUSTOMER_ARG } },
  handler: async (args: ToolArgs, ctx: McpCallContext) => {
    const scope = await resolveMcpScope(args, ctx.source);
    const rows = await allToolsets(scope);
    const toolRows = await listTools(scope);

    return {
      toolsets: rows.map((toolset) => ({
        id: toolset.id,
        name: toolset.name,
        kind: toolset.kind,
        description: toolset.description,
        mcp_url: toolset.mcpUrl,
        tools: toolRows
          .filter((tool) => tool.toolsetId === toolset.id)
          .map((tool) => ({
            name: tool.name,
            description: tool.description,
            enabled: tool.enabled,
          })),
      })),
    };
  },
};

const listPrompts: McpToolSpec = {
  name: 'list_prompts',
  description:
    'List prompts, optionally narrowed to one group. Includes each prompt\'s resolved effective system prompt and tool configuration.',
  readOnly: true,
  inputSchema: {
    type: 'object',
    properties: {
      customer: CUSTOMER_ARG,
      group: {
        type: ['string', 'integer'],
        description: 'Name or id of a prompt group. Omit for all prompts.',
      },
      include_content: {
        type: 'boolean',
        description: 'Include the prompt text itself. Default true.',
      },
      max_content_chars: {
        type: 'integer',
        description: 'Truncate prompt text to this many characters. 0 (default) means no limit.',
      },
    },
  },
  handler: async (args: ToolArgs, ctx: McpCallContext) => {
    const scope = await resolveMcpScope(args, ctx.source);
    const groupRef = optionalRowRef(args, 'group');
    const group = groupRef ? await resolveGroup(scope, groupRef) : null;

    const rows = await listPromptRows(scope, group ? { groupId: group.id } : {});

    const views = await promptViews(scope, rows, {
      includeContent: optionalBoolean(args, 'include_content', true),
      maxContentChars: optionalInteger(args, 'max_content_chars') ?? 0,
    });

    return { count: views.length, prompts: views };
  },
};

const getPrompt: McpToolSpec = {
  name: 'get_prompt',
  description: 'Fetch one prompt in full, including its resolved effective system prompt.',
  readOnly: true,
  inputSchema: {
    type: 'object',
    properties: {
      customer: CUSTOMER_ARG,
      prompt_id: { type: 'integer', description: 'Prompt id.' },
    },
    required: ['prompt_id'],
  },
  handler: async (args: ToolArgs, ctx: McpCallContext) => ({
    prompt: await promptViewById(
      await resolveMcpScope(args, ctx.source),
      requireInteger(args, 'prompt_id'),
    ),
  }),
};

const createPrompt: McpToolSpec = {
  name: 'create_prompt',
  description:
    'Create a test prompt in a group. For an ordinary one-shot test leave the tool fields out. For a tool/API test set tool_mode to "definitions" (record what the model wanted to call, execute nothing) or "execute" (really run the calls and loop) and name the toolsets to offer.',
  inputSchema: {
    type: 'object',
    properties: {
      customer: CUSTOMER_ARG,
      group: {
        type: ['string', 'integer'],
        description: 'Name or id of the prompt group. Create it first with create_prompt_group.',
      },
      title: { type: 'string', description: 'Short label shown in lists and comparisons.' },
      content: { type: 'string', description: 'The user message sent to the model.' },
      expected_output: {
        type: 'string',
        description: 'What a good answer looks like. Shown next to the result when rating; never sent to the model.',
      },
      system_prompt: {
        type: ['string', 'integer'],
        description: 'Name or id of a reusable system prompt to use as the base.',
      },
      system_prompt_mode: {
        type: 'string',
        enum: ['append', 'override'],
        description:
          '"append" (default) sends the base system prompt plus custom_system_text; "override" sends custom_system_text alone.',
      },
      custom_system_text: {
        type: 'string',
        description: 'Per-prompt system text. Used alone if no base system prompt is given.',
      },
      tool_mode: {
        type: 'string',
        enum: ['none', 'definitions', 'execute'],
        description: 'Default "none".',
      },
      tool_choice: {
        type: 'string',
        enum: ['auto', 'required', 'none'],
        description: 'Omit to leave tool_choice out of the request entirely.',
      },
      max_turns: {
        type: 'integer',
        description: 'Turn budget for tool_mode "execute". Default 6, maximum 20.',
      },
      toolsets: {
        type: 'array',
        items: { type: ['string', 'integer'] },
        description:
          'Names or ids of toolsets to offer. Several may be combined, but their tool names must not collide.',
      },
    },
    required: ['group', 'title', 'content'],
  },
  handler: async (args: ToolArgs, ctx: McpCallContext) => {
    const scope = await resolveMcpScope(args, ctx.source);
    const group = await resolveGroup(scope, requireRowRef(args, 'group'));
    const title = requireString(args, 'title');
    const content = requireText(args, 'content');
    const expectedOutput = optionalText(args, 'expected_output');
    const systemPromptRef = optionalRowRef(args, 'system_prompt');
    const systemPrompt = systemPromptRef
      ? await resolveSystemPrompt(scope, systemPromptRef)
      : null;
    const systemPromptMode =
      optionalEnum(args, 'system_prompt_mode', SYSTEM_PROMPT_MODES) ?? 'append';
    const customSystemText = optionalText(args, 'custom_system_text');
    const toolMode = optionalEnum(args, 'tool_mode', TOOL_MODES) ?? 'none';
    const toolChoice = optionalEnum(args, 'tool_choice', TOOL_CHOICES);
    const maxTurns = normalizeMaxTurns(optionalInteger(args, 'max_turns'));
    const toolsetRefs = optionalRowRefList(args, 'toolsets') ?? [];

    await assertToolConfig(scope, toolMode, toolsetRefs);
    const resolvedToolsets =
      toolsetRefs.length > 0 ? await resolveToolsets(scope, toolsetRefs) : [];

    const now = new Date();
    const row = await createPromptRow(scope, {
      groupId: group.id,
      title,
      content,
      expectedOutput,
      systemPromptId: systemPrompt?.id ?? null,
      systemPromptMode,
      customSystemText,
      toolMode: toolMode as ToolMode,
      toolChoice: toolChoice as ToolChoice | null,
      maxTurns,
      sortOrder: 0,
      createdAt: now,
      updatedAt: now,
    });

    await replaceToolsetLinks(
      scope,
      row.id,
      resolvedToolsets.map((toolset) => toolset.id),
    );

    revalidateAuthoring();
    return { prompt: await promptViewById(scope, row.id) };
  },
};

const updatePrompt: McpToolSpec = {
  name: 'update_prompt',
  description:
    'Change fields of an existing prompt. Only the fields you pass are touched; pass null to clear an optional one. Past runs keep the text they froze, so editing is safe.',
  inputSchema: {
    type: 'object',
    properties: {
      customer: CUSTOMER_ARG,
      prompt_id: { type: 'integer', description: 'Prompt id.' },
      group: { type: ['string', 'integer'], description: 'Move the prompt to this group.' },
      title: { type: 'string' },
      content: { type: 'string' },
      expected_output: { type: ['string', 'null'] },
      system_prompt: {
        type: ['string', 'integer', 'null'],
        description: 'Base system prompt, or null to detach it.',
      },
      system_prompt_mode: { type: 'string', enum: ['append', 'override'] },
      custom_system_text: { type: ['string', 'null'] },
      tool_mode: { type: 'string', enum: ['none', 'definitions', 'execute'] },
      tool_choice: { type: ['string', 'null'], enum: ['auto', 'required', 'none', null] },
      max_turns: { type: 'integer' },
      toolsets: {
        type: 'array',
        items: { type: ['string', 'integer'] },
        description: 'Replaces the prompt\'s toolsets. Pass [] to offer none.',
      },
    },
    required: ['prompt_id'],
  },
  handler: async (args: ToolArgs, ctx: McpCallContext) => {
    const scope = await resolveMcpScope(args, ctx.source);
    const id = requireInteger(args, 'prompt_id');
    const existing = await getPromptRow(scope, id);
    if (!existing) {
      throw new McpToolError(`No prompt with id ${id}.`);
    }

    const values: Partial<Prompt> = { updatedAt: new Date() };

    if (hasKey(args, 'group')) {
      values.groupId = (await resolveGroup(scope, requireRowRef(args, 'group'))).id;
    }
    if (hasKey(args, 'title')) values.title = requireString(args, 'title');
    if (hasKey(args, 'content')) values.content = requireText(args, 'content');
    if (hasKey(args, 'expected_output')) {
      values.expectedOutput = optionalText(args, 'expected_output');
    }
    if (hasKey(args, 'system_prompt')) {
      const ref = optionalRowRef(args, 'system_prompt');
      values.systemPromptId = ref ? (await resolveSystemPrompt(scope, ref)).id : null;
    }
    if (hasKey(args, 'system_prompt_mode')) {
      values.systemPromptMode =
        optionalEnum(args, 'system_prompt_mode', SYSTEM_PROMPT_MODES) ?? 'append';
    }
    if (hasKey(args, 'custom_system_text')) {
      values.customSystemText = optionalText(args, 'custom_system_text');
    }
    if (hasKey(args, 'tool_mode')) {
      values.toolMode = (optionalEnum(args, 'tool_mode', TOOL_MODES) ?? 'none') as ToolMode;
    }
    if (hasKey(args, 'tool_choice')) {
      values.toolChoice = optionalEnum(args, 'tool_choice', TOOL_CHOICES) as ToolChoice | null;
    }
    if (hasKey(args, 'max_turns')) {
      values.maxTurns = normalizeMaxTurns(optionalInteger(args, 'max_turns'));
    }

    // The tool configuration has to be checked as it will be *after* the patch:
    // switching mode without naming toolsets keeps the ones already linked.
    const toolsetRefs = optionalRowRefList(args, 'toolsets');
    const effectiveMode = (values.toolMode ?? existing.toolMode) as ToolMode;

    let effectiveRefs: RowRef[];
    if (toolsetRefs !== null) {
      effectiveRefs = toolsetRefs;
    } else {
      const links = await listToolsetLinks(scope, [id]);
      effectiveRefs = links.map((link) => ({ kind: 'id' as const, id: link.toolsetId }));
    }

    await assertToolConfig(scope, effectiveMode, effectiveRefs);

    await updatePromptRow(scope, id, values);

    if (toolsetRefs !== null) {
      const resolved = toolsetRefs.length > 0 ? await resolveToolsets(scope, toolsetRefs) : [];
      await replaceToolsetLinks(
        scope,
        id,
        resolved.map((toolset) => toolset.id),
      );
    }

    revalidateAuthoring();
    return { prompt: await promptViewById(scope, id) };
  },
};

const deletePrompt: McpToolSpec = {
  name: 'delete_prompt',
  description:
    'Delete a prompt. Results of past runs survive — they hold their own snapshot of the prompt text — but the prompt stops appearing in new runs and in compare-by-model.',
  destructive: true,
  inputSchema: {
    type: 'object',
    properties: {
      customer: CUSTOMER_ARG,
      prompt_id: { type: 'integer', description: 'Prompt id.' },
    },
    required: ['prompt_id'],
  },
  handler: async (args: ToolArgs, ctx: McpCallContext) => {
    const scope = await resolveMcpScope(args, ctx.source);
    const id = requireInteger(args, 'prompt_id');
    const existing = await getPromptRow(scope, id);
    if (!existing) {
      throw new McpToolError(`No prompt with id ${id}.`);
    }

    await deletePromptRow(scope, id);
    revalidateAuthoring();
    return { deleted: { id: existing.id, title: existing.title } };
  },
};

export const AUTHORING_TOOLS: readonly McpToolSpec[] = [
  listPromptGroups,
  createPromptGroup,
  listSystemPrompts,
  createSystemPrompt,
  updateSystemPrompt,
  listToolsets,
  listPrompts,
  getPrompt,
  createPrompt,
  updatePrompt,
  deletePrompt,
];
