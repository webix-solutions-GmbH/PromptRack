/**
 * The one tool that names no workspace, because it is how a caller learns which
 * ones exist.
 *
 * Customers are deliberately **not** writable over MCP: creating an engagement
 * is a human decision with billing behind it, and the app's existing line —
 * machines and toolsets stay UI-only because they hold credentials — already
 * puts workspace administration on the UI side.
 */

import { countCustomerContent, countPromptsByCustomer, listCustomers } from '@/db/repo/customers';
import { CUSTOMER_ARG_KEY, CUSTOMER_HEADER } from './customer';
import type { McpToolSpec } from './protocol';

const listCustomersTool: McpToolSpec = {
  name: 'list_customers',
  description:
    `List the customer workspaces. Every other tool is scoped to exactly one: pass the chosen name (or id) as "${CUSTOMER_ARG_KEY}" on each call, or set an "${CUSTOMER_HEADER}" header on the connection so it applies to all of them. Archived workspaces are listed too — they still work, they are just hidden from the UI switcher.`,
  // Read-only so a viewer's token can orient itself before being refused a write.
  readOnly: true,
  inputSchema: { type: 'object', properties: {} },
  handler: async () => {
    const rows = await listCustomers();
    const promptCounts = await countPromptsByCustomer();
    const counts = await Promise.all(rows.map((row) => countCustomerContent(row.id)));

    return {
      customers: rows.map((row, index) => ({
        id: row.id,
        name: row.name,
        description: row.description,
        archived: row.archivedAt !== null,
        counts: {
          prompt_groups: counts[index].promptGroups,
          prompts: promptCounts.get(row.id) ?? 0,
          machines: counts[index].machines,
          runs: counts[index].runs,
        },
        created_at: row.createdAt.getTime(),
      })),
    };
  },
};

export const CUSTOMER_TOOLS: readonly McpToolSpec[] = [listCustomersTool];
