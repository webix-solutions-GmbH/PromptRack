import { currentScope } from '@/db/scope';
import { getMachine } from '@/db/repo/machines';
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

  const machine = await getMachine(await currentScope(), id);
  if (!machine) {
    return Response.json({ ok: false, error: 'Machine not found.' }, { status: 404 });
  }

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 5_000);
  const startedAt = Date.now();

  try {
    const response = await fetch(`${machine.baseUrl}/models`, {
      method: 'GET',
      headers: machine.apiKey ? { Authorization: `Bearer ${machine.apiKey}` } : undefined,
      signal: controller.signal,
    });
    const latencyMs = Date.now() - startedAt;

    if (!response.ok) {
      const suffix = response.status === 401 || response.status === 403
        ? ' (unauthorized — check the API key)'
        : '';
      return Response.json({
        ok: false,
        status: response.status,
        latencyMs,
        error: `Request failed with status ${response.status}${suffix}`,
      });
    }

    return Response.json({ ok: true, status: response.status, latencyMs });
  } catch (err) {
    return Response.json({ ok: false, error: describeFetchError(err) });
  } finally {
    clearTimeout(timeout);
  }
}
