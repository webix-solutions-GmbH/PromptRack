import { headers } from 'next/headers';
import { onPage, requireActor } from '@/lib/auth/guards';
import { listApiTokens } from '@/lib/auth/tokens';
import { apiPath } from '@/lib/base-path';
import { CreateTokenForm } from '@/components/auth/create-token-form';
import { TokenList, type TokenRowView } from '@/components/auth/token-list';

export const dynamic = 'force-dynamic';

/** The public /api/mcp URL, so the sample curl line is copy-pasteable as is. */
async function mcpUrl(): Promise<string> {
  const requestHeaders = await headers();
  const host = requestHeaders.get('x-forwarded-host') ?? requestHeaders.get('host') ?? 'localhost:3000';
  const proto = requestHeaders.get('x-forwarded-proto') ?? (host.startsWith('localhost') ? 'http' : 'https');
  return `${proto}://${host}${apiPath('/api/mcp')}`;
}

export default async function ApiTokensPage() {
  const actor = await onPage(requireActor);
  const rows = await listApiTokens(actor.userId);

  const tokens: TokenRowView[] = rows.map((token) => ({
    id: token.id,
    name: token.name,
    prefix: token.prefix,
    createdAt: token.createdAt.getTime(),
    lastUsedAt: token.lastUsedAt?.getTime() ?? null,
    expiresAt: token.expiresAt?.getTime() ?? null,
    revokedAt: token.revokedAt?.getTime() ?? null,
  }));

  return (
    <div className="flex flex-1 flex-col gap-8 p-8">
      <div className="flex flex-col gap-2">
        <h1 className="text-2xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
          API tokens
        </h1>
        <p className="max-w-prose text-sm text-zinc-600 dark:text-zinc-400">
          Bearer tokens for this app&apos;s own MCP endpoint, so an agent can author prompts, start
          runs and read the measurements back. A token acts as you and carries your role, so a
          viewer&apos;s token can only call the read-only tools. Tokens are stored hashed and shown
          exactly once.
        </p>
      </div>

      <CreateTokenForm mcpUrl={await mcpUrl()} />

      <TokenList tokens={tokens} />
    </div>
  );
}
