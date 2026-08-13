import { countCustomerContent, listCustomers } from '@/db/repo/customers';
import { createCustomer } from '@/actions/customers';
import { onPage, requireActor } from '@/lib/auth/guards';
import { canAdminister, canWrite } from '@/lib/auth/policy';
import { activeWorkspace } from '@/lib/workspace';
import { CreateToggle } from '@/components/create-toggle';
import { CustomerRow } from '@/components/customers/customer-row';

export const dynamic = 'force-dynamic';

const inputClass =
  'w-full rounded-md border border-zinc-300 bg-white px-3 py-2 text-sm text-zinc-900 placeholder:text-zinc-400 focus:border-zinc-500 focus:outline-none dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-50 dark:placeholder:text-zinc-500';
const labelClass = 'text-xs font-medium text-zinc-600 dark:text-zinc-400';

export default async function CustomersPage() {
  const actor = await onPage(requireActor);
  const writable = canWrite(actor.role);
  const administers = canAdminister(actor.role);

  const [rows, { customerId }] = await Promise.all([listCustomers(), activeWorkspace()]);
  const counts = await Promise.all(rows.map((row) => countCustomerContent(row.id)));

  return (
    <div className="flex flex-1 flex-col gap-8 p-8">
      <CreateToggle
        label="New workspace"
        title="New workspace"
        className="max-w-3xl"
        canCreate={writable}
        header={
          <div className="flex flex-col gap-2">
            <h1 className="text-2xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
              Workspaces
            </h1>
            <p className="max-w-prose text-sm text-zinc-600 dark:text-zinc-400">
              One workspace per customer engagement. Machines, system prompts, toolsets, prompts and
              runs each belong to exactly one — switch between them in the sidebar.
            </p>
          </div>
        }
      >
        <form action={createCustomer} className="flex flex-col gap-4">
          <div className="flex flex-col gap-1">
            <label className={labelClass} htmlFor="name">
              Name *
            </label>
            <input id="name" name="name" required placeholder="Acme GmbH" className={inputClass} />
          </div>
          <div className="flex flex-col gap-1">
            <label className={labelClass} htmlFor="description">
              Description
            </label>
            <input
              id="description"
              name="description"
              placeholder="Invoice agent evaluation, Q3"
              className={inputClass}
            />
          </div>
          <div>
            <button
              type="submit"
              className="rounded-md bg-zinc-900 px-4 py-2 text-sm font-medium text-zinc-50 transition-colors hover:bg-zinc-700 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-300"
            >
              Create workspace
            </button>
          </div>
        </form>
      </CreateToggle>

      <ul className="flex max-w-3xl flex-col gap-3">
        {rows.map((customer, index) => (
          <CustomerRow
            key={customer.id}
            customer={customer}
            counts={counts[index]}
            // `listCustomers` is ordered by id, so the first row is the oldest —
            // the one the migration assigned every pre-workspace row to.
            isDefault={index === 0}
            isActive={customer.id === customerId}
            canWrite={writable}
            canAdminister={administers}
          />
        ))}
      </ul>
    </div>
  );
}
