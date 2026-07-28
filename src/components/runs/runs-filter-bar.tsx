'use client';

import { useCallback } from 'react';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';

export interface RunsFilterOptions {
  machines: { id: number; name: string }[];
  models: string[];
  groups: string[];
  statuses: string[];
}

const selectClass =
  'rounded-md border border-zinc-300 bg-white px-3 py-2 text-sm text-zinc-900 focus:border-zinc-500 focus:outline-none dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-50';
const labelClass = 'text-xs font-medium text-zinc-600 dark:text-zinc-400';

function FilterSelect({
  id,
  label,
  value,
  onChange,
  children,
}: {
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1">
      <label className={labelClass} htmlFor={id}>
        {label}
      </label>
      <select
        id={id}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className={selectClass}
      >
        <option value="">All</option>
        {children}
      </select>
    </div>
  );
}

export function RunsFilterBar({ options }: { options: RunsFilterOptions }) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const setParam = useCallback(
    (key: string, value: string) => {
      const params = new URLSearchParams(searchParams.toString());
      if (value) {
        params.set(key, value);
      } else {
        params.delete(key);
      }
      const query = params.toString();
      router.push(query.length > 0 ? `${pathname}?${query}` : pathname);
    },
    [pathname, router, searchParams],
  );

  const machineId = searchParams.get('machineId') ?? '';
  const model = searchParams.get('model') ?? '';
  const group = searchParams.get('group') ?? '';
  const status = searchParams.get('status') ?? '';
  // Empty means the default, which hides archived runs.
  const archived = searchParams.get('archived') ?? '';
  const hasFilters = machineId || model || group || status || archived;

  return (
    <div className="flex flex-wrap items-end gap-3">
      <FilterSelect
        id="filter-machine"
        label="Machine"
        value={machineId}
        onChange={(value) => setParam('machineId', value)}
      >
        {options.machines.map((machine) => (
          <option key={machine.id} value={String(machine.id)}>
            {machine.name}
          </option>
        ))}
      </FilterSelect>

      <FilterSelect
        id="filter-model"
        label="Model"
        value={model}
        onChange={(value) => setParam('model', value)}
      >
        {options.models.map((modelId) => (
          <option key={modelId} value={modelId}>
            {modelId}
          </option>
        ))}
      </FilterSelect>

      <FilterSelect
        id="filter-group"
        label="Group"
        value={group}
        onChange={(value) => setParam('group', value)}
      >
        {options.groups.map((groupName) => (
          <option key={groupName} value={groupName}>
            {groupName}
          </option>
        ))}
      </FilterSelect>

      <FilterSelect
        id="filter-status"
        label="Status"
        value={status}
        onChange={(value) => setParam('status', value)}
      >
        {options.statuses.map((statusValue) => (
          <option key={statusValue} value={statusValue}>
            {statusValue}
          </option>
        ))}
      </FilterSelect>

      {/*
        Not a FilterSelect: its "All" option means "no filter", whereas the
        default here is an active choice — archived runs are hidden unless asked
        for.
      */}
      <div className="flex flex-col gap-1">
        <label className={labelClass} htmlFor="filter-archived">
          Archived
        </label>
        <select
          id="filter-archived"
          value={archived}
          onChange={(event) => setParam('archived', event.target.value)}
          className={selectClass}
        >
          <option value="">Hidden</option>
          <option value="only">Only archived</option>
          <option value="all">Include archived</option>
        </select>
      </div>

      {hasFilters && (
        <button
          type="button"
          onClick={() => router.push(pathname)}
          className="pb-2 text-xs font-medium text-zinc-500 underline-offset-2 hover:underline dark:text-zinc-400"
        >
          Clear filters
        </button>
      )}
    </div>
  );
}
