'use client';

import { usePathname, useRouter, useSearchParams } from 'next/navigation';

/**
 * Clickable column header driving `?sort=<key>&dir=asc|desc`. First click uses
 * the column's natural direction (`firstDir`), clicking again flips it. The
 * page itself does the actual sorting server-side, like the filter bar.
 */
export function SortableHeader({
  label,
  sortKey,
  firstDir = 'desc',
}: {
  label: string;
  sortKey: string;
  firstDir?: 'asc' | 'desc';
}) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const activeKey = searchParams.get('sort') ?? 'created';
  const activeDir = searchParams.get('dir') === 'asc' ? 'asc' : 'desc';
  const active = activeKey === sortKey;

  function handleClick() {
    const params = new URLSearchParams(searchParams.toString());
    params.set('sort', sortKey);
    params.set('dir', active ? (activeDir === 'desc' ? 'asc' : 'desc') : firstDir);
    router.push(`${pathname}?${params.toString()}`);
  }

  return (
    <button
      type="button"
      onClick={handleClick}
      className={`inline-flex items-center gap-1 uppercase tracking-wide transition-colors hover:text-zinc-800 dark:hover:text-zinc-200 ${
        active ? 'text-zinc-800 dark:text-zinc-200' : ''
      }`}
    >
      {label}
      <span aria-hidden className={active ? '' : 'invisible'}>
        {active && activeDir === 'asc' ? '▲' : '▼'}
      </span>
    </button>
  );
}
