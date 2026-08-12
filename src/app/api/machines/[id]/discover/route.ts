import { revalidatePath } from 'next/cache';
import { currentScope } from '@/db/scope';
import { getMachine, syncDiscoveredModels } from '@/db/repo/machines';
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

  const scope = await currentScope();
  const machine = await getMachine(scope, id);
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

  await syncDiscoveredModels(scope, id, discoveredIds);

  revalidatePath(`/machines/${id}`);
  revalidatePath('/machines');

  return Response.json({ ok: true, discovered: discoveredIds.length, models: discoveredIds });
}
