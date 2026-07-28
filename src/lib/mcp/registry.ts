/**
 * Everything the MCP endpoint exposes.
 *
 * Split by subject rather than by read/write, so the authoring half stays
 * readable next to the prompt editor it mirrors.
 */

import type { McpToolSpec } from './protocol';
import { AUTHORING_TOOLS } from './tools-authoring';
import { RUN_TOOLS } from './tools-runs';

export const MCP_TOOLS: readonly McpToolSpec[] = [...AUTHORING_TOOLS, ...RUN_TOOLS];
