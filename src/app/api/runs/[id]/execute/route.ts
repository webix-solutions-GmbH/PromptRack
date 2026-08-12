import { currentScope } from '@/db/scope';
import { countPendingResults, getRun } from '@/db/repo/runs';
import { executeRun, isRunExecuting } from '@/lib/run-executor';
import type { RunEvent, RunStatus } from '@/lib/run-events';
import { guardRequest } from '@/lib/auth/guards';

export const dynamic = 'force-dynamic';

/**
 * Streams the execution of a run as NDJSON — one `RunEvent` per line.
 *
 * Tradeoff: execution is tied to the lifetime of this HTTP request. There is no
 * job queue or worker, so closing the tab (or navigating away) aborts the
 * in-flight completion. That is deliberate for a single-user local tool: the
 * client gets live progress over one plain connection with no polling, and the
 * cost is handled by rolling the interrupted result back to 'pending' so the
 * "Resume" button finishes the run later. A background runner would survive
 * disconnects but would need its own progress channel and lifecycle handling.
 */
export async function POST(
  request: Request,
  { params }: { params: Promise<{ id: string }> },
) {
  // Before anything else, and above all before the stream is constructed: a
  // refusal has to be plain JSON rather than a truncated NDJSON body.
  const guard = await guardRequest(request, 'write');
  if ('response' in guard) return guard.response;

  const { id: idParam } = await params;
  const runId = Number(idParam);
  if (!Number.isInteger(runId)) {
    return Response.json({ error: 'Invalid run id.' }, { status: 400 });
  }

  const scope = await currentScope();
  const run = await getRun(scope, runId);
  if (!run) {
    return Response.json({ error: 'Run not found.' }, { status: 404 });
  }

  if (await isRunExecuting(runId)) {
    return Response.json({ error: 'This run is already executing.' }, { status: 409 });
  }

  const pending = await countPendingResults(scope, runId);

  const encoder = new TextEncoder();

  if (pending === 0) {
    const event: RunEvent = {
      type: 'runDone',
      runId,
      status: run.status as RunStatus,
      nothingPending: true,
    };
    return new Response(encoder.encode(`${JSON.stringify(event)}\n`), {
      headers: ndjsonHeaders(),
    });
  }

  const stream = new ReadableStream<Uint8Array>({
    async start(controller) {
      let closed = false;

      const emit = (event: RunEvent) => {
        if (closed) return;
        try {
          controller.enqueue(encoder.encode(`${JSON.stringify(event)}\n`));
        } catch {
          // The client is gone; executeRun keeps going until it notices the
          // abort signal, then rolls the current result back to 'pending'.
          closed = true;
        }
      };

      try {
        await executeRun(runId, emit, request.signal);
      } catch (err) {
        emit({
          type: 'runError',
          runId,
          error: err instanceof Error ? err.message : 'Run execution failed.',
        });
      } finally {
        closed = true;
        try {
          controller.close();
        } catch {
          // Already closed by the runtime after the client disconnected.
        }
      }
    },
  });

  return new Response(stream, { headers: ndjsonHeaders() });
}

function ndjsonHeaders(): HeadersInit {
  return {
    'Content-Type': 'application/x-ndjson; charset=utf-8',
    'Cache-Control': 'no-store, no-transform',
    // Disable proxy buffering so events arrive as they are produced.
    'X-Accel-Buffering': 'no',
  };
}
