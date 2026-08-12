import { revalidatePath } from 'next/cache';
import { eq } from 'drizzle-orm';
import { db } from '@/db';
import { machineModels, machines } from '@/db/schema';
import { describeFetchError } from '@/lib/fetch-error';

export const dynamic = 'force-dynamic';

export async function POST(
  _request: Request,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id: idParam } = await params;
  const id = Number(idParam);
  if (!Number.isInteger(id)) {
    return Response.json({ ok: false, error: 'Invalid machine id.' }, { status: 400 });
  }

  const [machine] = await db.select().from(machines).where(eq(machines.id, id));
  if (!machine) {
    return Response.json({ ok: false, error: 'Machine not found.' }, { status: 404 });
  }

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 10_000);

  let response: Response;
  try {
    response = await fetch(`${machine.baseUrl}/models`, {
      headers: machine.apiKey ? { Authorization: `Bearer ${machine.apiKey}` } : undefined,
      signal: controller.signal,
    });
  } catch (err) {
    clearTimeout(timeout);
    return Response.json({ ok: false, error: describeFetchError(err) });
  }
  clearTimeout(timeout);

  if (!response.ok) {
    const suffix = response.status === 401 || response.status === 403
      ? ' (unauthorized — check the API key)'
      : '';
    return Response.json({
      ok: false,
      error: `Request failed with status ${response.status}${suffix}`,
    });
  }

  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    return Response.json({ ok: false, error: 'Invalid JSON response from server.' });
  }

  const list = (payload as { data?: unknown } | null)?.data;
  if (!Array.isArray(list)) {
    return Response.json({
      ok: false,
      error: 'Unexpected response shape (expected {"data": [{"id": ...}, ...]}).',
    });
  }

  const discoveredIds = list
    .map((item) => (item && typeof item === 'object' ? (item as { id?: unknown }).id : undefined))
    .filter((value): value is string => typeof value === 'string' && value.length > 0);

  const now = new Date();
  const existingRows = await db
    .select()
    .from(machineModels)
    .where(eq(machineModels.machineId, id));
  const existingByModelId = new Map(existingRows.map((row) => [row.modelId, row]));

  for (const modelId of discoveredIds) {
    const existing = existingByModelId.get(modelId);
    if (existing) {
      await db
        .update(machineModels)
        .set({ lastSeenAt: now, currentlyLoaded: true })
        .where(eq(machineModels.id, existing.id));
    } else {
      await db.insert(machineModels).values({
        machineId: id,
        modelId,
        source: 'discovered',
        currentlyLoaded: true,
        firstSeenAt: now,
        lastSeenAt: now,
      });
    }
  }

  // Anything previously seen for this machine but absent from this response
  // is no longer loaded — flip the flag but never delete the row (history).
  const discoveredSet = new Set(discoveredIds);
  const noLongerLoaded = existingRows.filter(
    (row) => row.currentlyLoaded && !discoveredSet.has(row.modelId),
  );
  for (const row of noLongerLoaded) {
    await db
      .update(machineModels)
      .set({ currentlyLoaded: false })
      .where(eq(machineModels.id, row.id));
  }

  revalidatePath(`/machines/${id}`);
  revalidatePath('/machines');

  return Response.json({ ok: true, discovered: discoveredIds.length, models: discoveredIds });
}
