import { mockDisabledResponse, mocksEnabled } from '@/lib/dev-only';

export const dynamic = 'force-dynamic';

/**
 * Model list for the built-in mock endpoint. Point a machine at
 * `http://localhost:3000/api/mock-llm` to exercise runs without real hardware.
 */
export async function GET() {
  if (!mocksEnabled()) return mockDisabledResponse();
  const created = Math.floor(Date.now() / 1000);

  return Response.json({
    object: 'list',
    data: [
      { id: 'mock-fast-7b', object: 'model', created, owned_by: 'mock' },
      { id: 'mock-slow-70b', object: 'model', created, owned_by: 'mock' },
    ],
  });
}
