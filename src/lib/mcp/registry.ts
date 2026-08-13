/**
 * Everything the MCP endpoint exposes.
 *
 * Split by subject rather than by read/write, so the authoring half stays
 * readable next to the prompt editor it mirrors.
 */

import type { McpToolSpec } from './protocol';
import { AUTHORING_TOOLS } from './tools-authoring';
import { CUSTOMER_TOOLS } from './tools-customers';
import { RUN_TOOLS } from './tools-runs';

// Workspaces first: every other tool needs one, so a client reading the list
// top-down meets the way to find them before the things that require them.
export const MCP_TOOLS: readonly McpToolSpec[] = [
  ...CUSTOMER_TOOLS,
  ...AUTHORING_TOOLS,
  ...RUN_TOOLS,
];
