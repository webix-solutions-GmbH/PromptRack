import Link from 'next/link';
import { desc } from 'drizzle-orm';
import { db } from '@/db';
import { machineModels, machines } from '@/db/schema';
import { formatDateTime } from '@/lib/format';
import { createMachine } from '@/actions/machines';
import { CreateToggle } from '@/components/create-toggle';

export const dynamic = 'force-dynamic';

const inputClass =
  'w-full rounded-md border border-zinc-300 bg-white px-3 py-2 text-sm text-zinc-900 placeholder:text-zinc-400 focus:border-zinc-500 focus:outline-none dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-50 dark:placeholder:text-zinc-500';
const labelClass = 'text-xs font-medium text-zinc-600 dark:text-zinc-400';

async function getMachinesWithCounts() {
  const rows = await db.select().from(machines).orderBy(desc(machines.createdAt));
  const models = await db.select().from(machineModels);

  return rows.map((machine) => {
    const forMachine = models.filter((m) => m.machineId === machine.id);
    return {
      machine,
      total: forMachine.length,
      loaded: forMachine.filter((m) => m.currentlyLoaded).length,
    };
  });
}

export default async function MachinesPage() {
  const rows = await getMachinesWithCounts();

  return (
    <div className="flex flex-1 flex-col gap-8 p-8">
      <CreateToggle
        label="New machine"
        title="New machine"
        className="max-w-2xl"
        header={
          <div className="flex flex-col gap-2">
            <h1 className="text-2xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
              Machines
            </h1>
            <p className="max-w-prose text-sm text-zinc-600 dark:text-zinc-400">
              OpenAI-compatible endpoints (Ollama, LM Studio, vLLM, ...) that host models to
              benchmark.
            </p>
          </div>
        }
      >
        <form action={createMachine} className="flex flex-col gap-4">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div className="flex flex-col gap-1">
              <label className={labelClass} htmlFor="name">
                Name *
              </label>
              <input
                id="name"
                name="name"
                required
                placeholder="vllm-box"
                className={inputClass}
              />
            </div>
            <div className="flex flex-col gap-1">
              <label className={labelClass} htmlFor="baseUrl">
                Base URL *
              </label>
              <input
                id="baseUrl"
                name="baseUrl"
                required
                placeholder="http://vllm:8000/v1"
                className={inputClass}
              />
            </div>
          </div>

          <div className="flex flex-col gap-1">
            <label className={labelClass} htmlFor="apiKey">
              API key
            </label>
            <input
              id="apiKey"
              name="apiKey"
              type="password"
              placeholder="optional"
              className={inputClass}
            />
          </div>

          <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
            <div className="flex flex-col gap-1">
              <label className={labelClass} htmlFor="cpu">
                CPU
              </label>
              <input id="cpu" name="cpu" className={inputClass} />
            </div>
            <div className="flex flex-col gap-1">
              <label className={labelClass} htmlFor="ram">
                RAM
              </label>
              <input id="ram" name="ram" className={inputClass} />
            </div>
            <div className="flex flex-col gap-1">
              <label className={labelClass} htmlFor="gpu">
                GPU
              </label>
              <input id="gpu" name="gpu" className={inputClass} />
            </div>
          </div>

          <div className="flex flex-col gap-1">
            <label className={labelClass} htmlFor="notes">
              Notes
            </label>
            <textarea id="notes" name="notes" rows={3} className={inputClass} />
          </div>

          <div>
            <button
              type="submit"
              className="rounded-md bg-zinc-900 px-4 py-2 text-sm font-medium text-zinc-50 transition-colors hover:bg-zinc-700 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-300"
            >
              Create machine
            </button>
          </div>
        </form>
      </CreateToggle>

      <div className="overflow-x-auto rounded-lg border border-zinc-200 dark:border-zinc-800">
        <table className="w-full min-w-max text-left text-sm">
          <thead className="border-b border-zinc-200 bg-zinc-50 text-xs font-medium uppercase tracking-wide text-zinc-500 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-400">
            <tr>
              <th className="px-4 py-3">Name</th>
              <th className="px-4 py-3">Base URL</th>
              <th className="px-4 py-3">GPU</th>
              <th className="px-4 py-3">Models</th>
              <th className="px-4 py-3">Created</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-zinc-200 dark:divide-zinc-800">
            {rows.length === 0 && (
              <tr>
                <td
                  colSpan={5}
                  className="px-4 py-6 text-center text-zinc-500 dark:text-zinc-400"
                >
                  No machines yet — add one with “New machine”.
                </td>
              </tr>
            )}
            {rows.map(({ machine, total, loaded }) => (
              <tr
                key={machine.id}
                className="transition-colors hover:bg-zinc-50 dark:hover:bg-zinc-900"
              >
                <td className="px-4 py-3 font-medium text-zinc-900 dark:text-zinc-50">
                  <Link
                    href={`/machines/${machine.id}`}
                    className="hover:underline"
                  >
                    {machine.name}
                  </Link>
                </td>
                <td className="px-4 py-3 font-mono text-xs text-zinc-600 dark:text-zinc-400">
                  {machine.baseUrl}
                </td>
                <td className="px-4 py-3 text-zinc-600 dark:text-zinc-400">
                  {machine.gpu ?? '—'}
                </td>
                <td className="px-4 py-3 text-zinc-600 dark:text-zinc-400">
                  {loaded}/{total} loaded
                </td>
                <td className="px-4 py-3 text-zinc-600 dark:text-zinc-400">
                  {formatDateTime(machine.createdAt)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
