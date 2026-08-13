/**
 * Which customer workspace one MCP call runs in.
 *
 * The MCP server is stateless by design — no session id is issued, so there is
 * nowhere to "switch workspace" between calls and the workspace has to arrive
 * with each request. Three ways, in precedence order:
 *
 *   1. an explicit `customer` argument on the call,
 *   2. an `X-Customer` header on the connection (set once in the client's
 *      `mcp.json` and applied to every call),
 *   3. the token's own default workspace.
 *
 * Nothing is guessed. With none of the three present the call is refused with
 * the list of workspaces, because a write with no defined destination is worse
 * than an error the calling model can act on.
 */

import { scopeFromCustomerId, type Scope } from '@/db/scope';
import { listCustomerOptions } from '@/db/repo/customers';
import {
  McpToolError,
  optionalRowRef,
  parseRowRef,
  resolveRowRef,
  type RowRef,
  type ToolArgs,
} from './args';

export const CUSTOMER_HEADER = 'x-customer';
export const CUSTOMER_ARG_KEY = 'customer';

export interface McpScopeSource {
  /** `X-Customer: acme` — set once on the connection, applies to every call. */
  header: RowRef | null;
  /**
   * The token's own workspace.
   *
   * Always null today: `api_tokens` carries no customer column, and giving a
   * token a home workspace is a separate decision from making the surface
   * workspace-aware. The precedence chain is written out anyway so adding the
   * column later changes one line and no call site.
   */
  tokenDefault: number | null;
}

/** Reads `X-Customer` off a request. An empty header counts as absent. */
export function scopeSourceFromHeaders(headers: Headers): McpScopeSource {
  const raw = headers.get(CUSTOMER_HEADER)?.trim();
  return {
    header: raw ? parseRowRef(raw, `The "${CUSTOMER_HEADER}" header`) : null,
    tokenDefault: null,
  };
}

/** The precedence chain, as a pure function of the call and the connection. */
export function pickCustomerRef(args: ToolArgs, source: McpScopeSource): RowRef | null {
  const explicit = optionalRowRef(args, CUSTOMER_ARG_KEY);
  if (explicit) return explicit;
  if (source.header) return source.header;
  if (source.tokenDefault !== null) return { kind: 'id', id: source.tokenDefault };
  return null;
}

/**
 * Turns a ref into a workspace id, or refuses with something actionable.
 *
 * Split out from {@link resolveMcpScope} so the whole decision is testable
 * without a database — only the workspace list comes from one.
 */
export function resolveCustomerRef(
  ref: RowRef | null,
  rows: readonly { id: number; name: string }[],
): number {
  if (ref === null) {
    const known = rows.map((row) => `${row.name} (${row.id})`).join(', ');
    throw new McpToolError(
      `Every call is scoped to one customer workspace. Pass "${CUSTOMER_ARG_KEY}" (name or id) with this call, or send an "${CUSTOMER_HEADER}" header on the connection.` +
        (known ? ` Known workspaces: ${known}.` : ''),
    );
  }
  return resolveRowRef(ref, rows, 'customer workspace').id;
}

/** The workspace scope for one tool call. */
export async function resolveMcpScope(
  args: ToolArgs,
  source: McpScopeSource,
): Promise<Scope> {
  const rows = await listCustomerOptions();
  return scopeFromCustomerId(resolveCustomerRef(pickCustomerRef(args, source), rows));
}

/**
 * The `customer` property every tool advertises.
 *
 * Deliberately not in `required`: the header can supply it, which JSON Schema
 * cannot express — so the runtime refusal carries the explanation instead.
 */
export const CUSTOMER_ARG = {
  type: ['string', 'integer'],
  description:
    'Name or id of the customer workspace this call applies to. Required unless the connection sends an X-Customer header. list_customers shows what exists.',
} as const;
