import { notFound } from 'next/navigation';
import { currentScope } from '@/db/scope';
import { getMachine, listMachineModels } from '@/db/repo/machines';
import { formatDateTime } from '@/lib/format';
import { addManualModel, updateMachine } from '@/actions/machines';
import { onPage, requireActor } from '@/lib/auth/guards';
import { canAdminister, canWrite } from '@/lib/auth/policy';
import { TestConnectionButton } from '@/components/machines/test-connection-button';
import { DiscoverModelsButton } from '@/components/machines/discover-models-button';
import { DeleteMachineButton } from '@/components/machines/delete-machine-button';

export const dynamic = 'force-dynamic';

const inputClass =
  'w-full rounded-md border border-zinc-300 bg-white px-3 py-2 text-sm text-zinc-900 placeholder:text-zinc-400 focus:border-zinc-500 focus:outline-none dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-50 dark:placeholder:text-zinc-500';
const labelClass = 'text-xs font-medium text-zinc-600 dark:text-zinc-400';

export default async function MachineDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id: idParam } = await params;
  const id = Number(idParam);
  if (!Number.isInteger(id)) {
    notFound();
  }

  const actor = await onPage(requireActor);
  const isAdmin = canAdminister(actor.role);
  const scope = await currentScope();
  const machine = await getMachine(scope, id);
  if (!machine) {
    notFound();
  }

  const models = await listMachineModels(scope, { machineId: id });

  const boundUpdateMachine = updateMachine.bind(null, id);
  const boundAddManualModel = addManualModel.bind(null, id);

  return (
    <div className="flex flex-1 flex-col gap-8 p-8">
      <div className="flex flex-col gap-2">
        <h1 className="text-2xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
          {machine.name}
        </h1>
        <p className="max-w-prose font-mono text-sm text-zinc-600 dark:text-zinc-400">
          {machine.baseUrl}
        </p>
      </div>

      <div className="flex flex-wrap gap-4">
        {/* Test probes the endpoint with its stored credentials, so it is
            admin-only; discovery only reads model ids and every user needs it. */}
        {isAdmin && <TestConnectionButton machineId={machine.id} />}
        {canWrite(actor.role) && <DiscoverModelsButton machineId={machine.id} />}
      </div>

      {isAdmin && (
        <section className="flex max-w-2xl flex-col gap-4 rounded-lg border border-zinc-200 p-6 dark:border-zinc-800">
          <h2 className="text-lg font-semibold text-zinc-900 dark:text-zinc-50">
            Details
          </h2>
          <form action={boundUpdateMachine} className="flex flex-col gap-4">
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <div className="flex flex-col gap-1">
                <label className={labelClass} htmlFor="name">
                  Name *
                </label>
                <input
                  id="name"
                  name="name"
                  required
                  defaultValue={machine.name}
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
                  defaultValue={machine.baseUrl}
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
                defaultValue={machine.apiKey ?? ''}
                placeholder="optional"
                className={inputClass}
              />
            </div>

            <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
              <div className="flex flex-col gap-1">
                <label className={labelClass} htmlFor="cpu">
                  CPU
                </label>
                <input
                  id="cpu"
                  name="cpu"
                  defaultValue={machine.cpu ?? ''}
                  className={inputClass}
                />
              </div>
              <div className="flex flex-col gap-1">
                <label className={labelClass} htmlFor="ram">
                  RAM
                </label>
                <input
                  id="ram"
                  name="ram"
                  defaultValue={machine.ram ?? ''}
                  className={inputClass}
                />
              </div>
              <div className="flex flex-col gap-1">
                <label className={labelClass} htmlFor="gpu">
                  GPU
                </label>
                <input
                  id="gpu"
                  name="gpu"
                  defaultValue={machine.gpu ?? ''}
                  className={inputClass}
                />
              </div>
            </div>

            <div className="flex flex-col gap-1">
              <label className={labelClass} htmlFor="notes">
                Notes
              </label>
              <textarea
                id="notes"
                name="notes"
                rows={3}
                defaultValue={machine.notes ?? ''}
                className={inputClass}
              />
            </div>

            <div className="flex flex-wrap items-center gap-2 text-xs text-zinc-500 dark:text-zinc-400">
              <span>Created {formatDateTime(machine.createdAt)}</span>
              <span aria-hidden>·</span>
              <span>Updated {formatDateTime(machine.updatedAt)}</span>
            </div>

            <div className="flex items-center gap-3">
              <button
                type="submit"
                className="rounded-md bg-zinc-900 px-4 py-2 text-sm font-medium text-zinc-50 transition-colors hover:bg-zinc-700 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-300"
              >
                Save changes
              </button>
            </div>
          </form>

          <div className="border-t border-zinc-200 pt-4 dark:border-zinc-800">
            <DeleteMachineButton id={machine.id} name={machine.name} />
          </div>
        </section>
      )}

      <section className="flex flex-col gap-4">
        <h2 className="text-lg font-semibold text-zinc-900 dark:text-zinc-50">
          Models
        </h2>

        <div className="overflow-x-auto rounded-lg border border-zinc-200 dark:border-zinc-800">
          <table className="w-full min-w-max text-left text-sm">
            <thead className="border-b border-zinc-200 bg-zinc-50 text-xs font-medium uppercase tracking-wide text-zinc-500 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-400">
              <tr>
                <th className="px-4 py-3">Model ID</th>
                <th className="px-4 py-3">Status</th>
                <th className="px-4 py-3">Source</th>
                <th className="px-4 py-3">First seen</th>
                <th className="px-4 py-3">Last seen</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-200 dark:divide-zinc-800">
              {models.length === 0 && (
                <tr>
                  <td
                    colSpan={5}
                    className="px-4 py-6 text-center text-zinc-500 dark:text-zinc-400"
                  >
                    No models yet — discover or add one manually below.
                  </td>
                </tr>
              )}
              {models.map((model) => (
                <tr key={model.id}>
                  <td className="px-4 py-3 font-mono text-xs text-zinc-900 dark:text-zinc-50">
                    {model.modelId}
                  </td>
                  <td className="px-4 py-3">
                    {model.currentlyLoaded ? (
                      <span className="inline-flex items-center rounded-full bg-emerald-100 px-2 py-0.5 text-xs font-medium text-emerald-700 dark:bg-emerald-950 dark:text-emerald-400">
                        loaded
                      </span>
                    ) : (
                      <span className="inline-flex items-center rounded-full bg-zinc-100 px-2 py-0.5 text-xs font-medium text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400">
                        not loaded
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-zinc-600 dark:text-zinc-400">
                    {model.source}
                  </td>
                  <td className="px-4 py-3 text-zinc-600 dark:text-zinc-400">
                    {formatDateTime(model.firstSeenAt)}
                  </td>
                  <td className="px-4 py-3 text-zinc-600 dark:text-zinc-400">
                    {formatDateTime(model.lastSeenAt)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {isAdmin && (
          <form action={boundAddManualModel} className="flex max-w-md items-end gap-3">
            <div className="flex flex-1 flex-col gap-1">
              <label className={labelClass} htmlFor="modelId">
                Add model manually
              </label>
              <input
                id="modelId"
                name="modelId"
                required
                placeholder="llama-3.1-8b-instruct"
                className={inputClass}
              />
            </div>
            <button
              type="submit"
              className="rounded-md border border-zinc-300 px-3 py-2 text-sm font-medium text-zinc-700 transition-colors hover:bg-zinc-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800"
            >
              Add
            </button>
          </form>
        )}
      </section>
    </div>
  );
}
