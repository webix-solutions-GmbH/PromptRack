import Link from 'next/link';

/** One chip: a group, or the "All" reset. */
export interface GroupFilterItem {
  key: string;
  label: string;
  count: number | null;
  href: string;
  active: boolean;
}

/**
 * Chips restricting which prompt groups become rows in model mode.
 *
 * Plain links on purpose: the selection lives entirely in the URL, so this needs
 * no client state, works before hydration, and can be middle-clicked. No
 * selection means all groups — the filter keeps a wide matrix readable, it is
 * not a setting to remember.
 */
export function GroupFilter({ items }: { items: GroupFilterItem[] }) {
  if (items.length === 0) return null;

  return (
    <div className="flex flex-wrap items-center gap-2">
      <span className="text-xs font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
        Groups
      </span>
      {items.map((item) => (
        <Link
          key={item.key}
          href={item.href}
          scroll={false}
          aria-current={item.active ? 'true' : undefined}
          className={`rounded-full border px-3 py-1 text-xs transition-colors ${
            item.active
              ? 'border-zinc-900 bg-zinc-900 text-white dark:border-zinc-100 dark:bg-zinc-100 dark:text-zinc-900'
              : 'border-zinc-300 text-zinc-600 hover:border-zinc-500 dark:border-zinc-700 dark:text-zinc-400 dark:hover:border-zinc-500'
          }`}
        >
          {item.label}
          {item.count !== null && <span className="ml-1 opacity-60">{item.count}</span>}
        </Link>
      ))}
    </div>
  );
}
