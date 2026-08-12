/**
 * The mocks (a fake OpenAI endpoint and a fake MCP server) exist to exercise
 * the executor without a real model. They ship in the production image, so they
 * are switched off there unless ENABLE_MOCKS says otherwise — a 404, not a 403,
 * because in production these routes should not appear to exist.
 */
export function mocksEnabled(): boolean {
  return process.env.NODE_ENV !== 'production' || process.env.ENABLE_MOCKS === 'true';
}

export function mockDisabledResponse(): Response {
  return new Response('Not found', { status: 404 });
}
