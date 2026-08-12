'use client';

import { useState, useTransition } from 'react';
import { useRouter } from 'next/navigation';
import { revokeApiToken } from '@/actions/api-tokens';
import { formatIsoDateTime } from '@/lib/format';

export interface TokenRowView {
  id: string;
  name: string;
  prefix: string;
  createdAt: number;
  lastUsedAt: number | null;
  expiresAt: number | null;
  revokedAt: number | null;
}

function state(token: TokenRowView): { label: string; className: string } {
  if (token.revokedAt !== null) {
    return {
      label: 'revoked',
      className: 'bg-zinc-100 text-zinc-500 dark:bg-zinc-800 dark:text-zinc-400',
    };
  }
  if (token.expiresAt !== null && token.expiresAt <= Date.now()) {
    return {
      label: 'expired',
      className: 'bg-amber-100 text-amber-700 dark:bg-amber-950 dark:text-amber-300',
    };
  }
  return {
    label: 'active',
    className: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-400',
  };
}

export function TokenList({ tokens }: { tokens: TokenRowView[] }) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);

  function handleRevoke(token: TokenRowView) {
    if (!window.confirm(`Revoke "${token.name}"? Anything using it stops working immediately.`)) {
      return;
    }
    setError(null);
    startTransition(async () => {
      try {
        await revokeApiToken(token.id);
        router.refresh();
      } catch {
        setError('Failed to revoke the token.');
      }
    });
  }

  return (
    <div className="flex flex-col gap-2">
      {error && <p className="text-sm text-red-600 dark:text-red-400">{error}</p>}
      <div className="overflow-x-auto rounded-lg border border-zinc-200 dark:border-zinc-800">
        <table className="w-full min-w-max text-left text-sm">
          <thead className="border-b border-zinc-200 bg-zinc-50 text-xs font-medium uppercase tracking-wide text-zinc-500 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-400">
            <tr>
              <th className="px-4 py-3">Name</th>
              <th className="px-4 py-3">Prefix</th>
              <th className="px-4 py-3">State</th>
              <th className="px-4 py-3">Created</th>
              <th className="px-4 py-3">Last used</th>
              <th className="px-4 py-3">Expires</th>
              <th className="px-4 py-3">
                <span className="sr-only">Actions</span>
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-zinc-200 dark:divide-zinc-800">
            {tokens.length === 0 && (
              <tr>
                <td colSpan={7} className="px-4 py-6 text-center text-zinc-500 dark:text-zinc-400">
                  No tokens yet.
                </td>
              </tr>
            )}
            {tokens.map((token) => {
              const badge = state(token);
              return (
                <tr key={token.id}>
                  <td className="px-4 py-3 font-medium text-zinc-900 dark:text-zinc-50">
                    {token.name}
                  </td>
                  <td className="px-4 py-3 font-mono text-xs text-zinc-600 dark:text-zinc-400">
                    {token.prefix}…
                  </td>
                  <td className="px-4 py-3">
                    <span
                      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${badge.className}`}
                    >
                      {badge.label}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-zinc-600 dark:text-zinc-400">
                    {formatIsoDateTime(new Date(token.createdAt))}
                  </td>
                  <td className="px-4 py-3 text-zinc-600 dark:text-zinc-400">
                    {token.lastUsedAt === null ? 'never' : formatIsoDateTime(new Date(token.lastUsedAt))}
                  </td>
                  <td className="px-4 py-3 text-zinc-600 dark:text-zinc-400">
                    {token.expiresAt === null ? 'never' : formatIsoDateTime(new Date(token.expiresAt))}
                  </td>
                  <td className="px-4 py-3 text-right">
                    {token.revokedAt === null && (
                      <button
                        type="button"
                        disabled={pending}
                        onClick={() => handleRevoke(token)}
                        className="rounded-md border border-red-300 px-3 py-1.5 text-xs font-medium text-red-600 transition-colors hover:bg-red-50 disabled:opacity-50 dark:border-red-900 dark:text-red-400 dark:hover:bg-red-950"
                      >
                        Revoke
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
