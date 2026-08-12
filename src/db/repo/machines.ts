/** Machines (endpoints) and the models ever seen on them. */

import { and, asc, desc, eq, inArray, sql } from 'drizzle-orm';
import { db } from '@/db';
import {
  machineModels,
  machines,
  type Machine,
  type MachineModel,
  type NewMachine,
} from '../schema';
import { scopeValues, whereScoped, type Scope } from '../scope';
import type { DbHandle } from './scoped';

/** Editable machine fields — everything except the timestamps and the id. */
export type MachineFields = Omit<NewMachine, 'id' | 'createdAt' | 'updatedAt'>;

export function listMachines(scope: Scope, order: 'name' | 'created' = 'name'): Promise<Machine[]> {
  return db
    .select()
    .from(machines)
    .where(whereScoped(scope, machines))
    .orderBy(order === 'created' ? desc(machines.createdAt) : asc(machines.name));
}

export async function getMachine(scope: Scope, id: number): Promise<Machine | null> {
  const [row] = await db
    .select()
    .from(machines)
    .where(whereScoped(scope, machines, eq(machines.id, id)));
  return row ?? null;
}

export async function createMachine(
  scope: Scope,
  values: MachineFields & { createdAt: Date; updatedAt: Date },
): Promise<{ id: number }> {
  const [row] = await db
    .insert(machines)
    .values({ ...values, ...scopeValues(scope) })
    .returning({ id: machines.id });
  return row;
}

export async function updateMachine(
  scope: Scope,
  id: number,
  values: Partial<MachineFields> & { updatedAt: Date },
): Promise<void> {
  await db
    .update(machines)
    .set(values)
    .where(whereScoped(scope, machines, eq(machines.id, id)));
}

export async function deleteMachine(scope: Scope, id: number): Promise<void> {
  await db.delete(machines).where(whereScoped(scope, machines, eq(machines.id, id)));
}

/**
 * Every model ever seen, newest sighting first.
 *
 * `loaded-first` additionally floats the currently loaded ones to the top — the
 * "Currently loaded" optgroup on the new-run page depends on that order.
 */
export async function listMachineModels(
  scope: Scope,
  opts: { machineId?: number; order?: 'last-seen' | 'loaded-first' } = {},
): Promise<MachineModel[]> {
  const scoped = db
    .select({ model: machineModels })
    .from(machineModels)
    .innerJoin(machines, eq(machineModels.machineId, machines.id))
    .where(
      whereScoped(
        scope,
        machines,
        opts.machineId === undefined ? undefined : eq(machineModels.machineId, opts.machineId),
      ),
    );

  const rows =
    opts.order === 'loaded-first'
      ? await scoped.orderBy(desc(machineModels.currentlyLoaded), desc(machineModels.lastSeenAt))
      : await scoped.orderBy(desc(machineModels.lastSeenAt));

  return rows.map((row) => row.model);
}

export async function listLoadedModels(scope: Scope, machineId: number): Promise<MachineModel[]> {
  const rows = await db
    .select({ model: machineModels })
    .from(machineModels)
    .innerJoin(machines, eq(machineModels.machineId, machines.id))
    .where(
      whereScoped(
        scope,
        machines,
        and(eq(machineModels.machineId, machineId), eq(machineModels.currentlyLoaded, true)),
      ),
    );
  return rows.map((row) => row.model);
}

/** How many models each machine has ever shown, and how many are loaded now. */
export async function machineModelCounts(
  scope: Scope,
): Promise<Map<number, { total: number; loaded: number }>> {
  const rows = await db
    .select({
      machineId: machineModels.machineId,
      total: sql<number>`count(*)`.mapWith(Number),
      loaded: sql<number>`count(case when ${machineModels.currentlyLoaded} then 1 end)`.mapWith(
        Number,
      ),
    })
    .from(machineModels)
    .innerJoin(machines, eq(machineModels.machineId, machines.id))
    .where(whereScoped(scope, machines))
    .groupBy(machineModels.machineId);

  return new Map(rows.map((row) => [row.machineId, { total: row.total, loaded: row.loaded }]));
}

/**
 * Records that a model was seen on a machine.
 *
 * A row that already exists only gets its `last_seen_at` bumped: `source` says
 * how the model was *first* learned about, and `currently_loaded` belongs to
 * discovery. Nothing is ever deleted from this table.
 */
export async function touchMachineModel(
  scope: Scope,
  args: { machineId: number; modelId: string; source: 'manual' | 'run'; at: Date },
  handle: DbHandle = db,
): Promise<void> {
  const [existing] = await handle
    .select({ id: machineModels.id })
    .from(machineModels)
    .innerJoin(machines, eq(machineModels.machineId, machines.id))
    .where(
      whereScoped(
        scope,
        machines,
        and(
          eq(machineModels.machineId, args.machineId),
          eq(machineModels.modelId, args.modelId),
        ),
      ),
    );

  if (existing) {
    await handle
      .update(machineModels)
      .set({ lastSeenAt: args.at })
      .where(eq(machineModels.id, existing.id));
    return;
  }

  await handle.insert(machineModels).values({
    machineId: args.machineId,
    modelId: args.modelId,
    source: args.source,
    currentlyLoaded: false,
    firstSeenAt: args.at,
    lastSeenAt: args.at,
  });
}

/**
 * Applies what `/v1/models` just reported for one machine.
 *
 * Discovered models are upserted and flagged loaded; previously seen models that
 * are absent only lose the flag. Rows are never deleted — the history of what
 * has run on a machine is the point of the table.
 */
export async function syncDiscoveredModels(
  scope: Scope,
  machineId: number,
  modelIds: string[],
): Promise<{ discovered: number; retired: number }> {
  const now = new Date();
  const existingRows = await listMachineModels(scope, { machineId });
  const existingByModelId = new Map(existingRows.map((row) => [row.modelId, row]));

  for (const modelId of modelIds) {
    const existing = existingByModelId.get(modelId);
    if (existing) {
      await db
        .update(machineModels)
        .set({ lastSeenAt: now, currentlyLoaded: true })
        .where(eq(machineModels.id, existing.id));
    } else {
      await db.insert(machineModels).values({
        machineId,
        modelId,
        source: 'discovered',
        currentlyLoaded: true,
        firstSeenAt: now,
        lastSeenAt: now,
      });
    }
  }

  const discoveredSet = new Set(modelIds);
  const noLongerLoaded = existingRows.filter(
    (row) => row.currentlyLoaded && !discoveredSet.has(row.modelId),
  );
  if (noLongerLoaded.length > 0) {
    await db
      .update(machineModels)
      .set({ currentlyLoaded: false })
      .where(
        inArray(
          machineModels.id,
          noLongerLoaded.map((row) => row.id),
        ),
      );
  }

  return { discovered: modelIds.length, retired: noLongerLoaded.length };
}
