/**
 * Turn a fetch()/AbortController failure into a short, human-readable message
 * suitable for display in the UI (connection refused, timeout, DNS, etc).
 */
/**
 * Digs the transport error code out of a `fetch` failure.
 *
 * undici reports everything as `TypeError: fetch failed` and hides the real
 * reason in `cause` — which for a host that resolves to both IPv4 and IPv6 is
 * an `AggregateError` wrapping one error per attempted address.
 */
function findErrorCode(err: unknown, depth = 0): string | undefined {
  if (!err || typeof err !== 'object' || depth > 4) return undefined;

  const code = (err as { code?: unknown }).code;
  if (typeof code === 'string') return code;

  const nested = (err as { errors?: unknown }).errors;
  if (Array.isArray(nested)) {
    for (const inner of nested) {
      const found = findErrorCode(inner, depth + 1);
      if (found) return found;
    }
  }

  return findErrorCode((err as { cause?: unknown }).cause, depth + 1);
}

export function describeFetchError(err: unknown): string {
  if (err instanceof Error) {
    if (err.name === 'AbortError') {
      return 'Connection timed out.';
    }

    const code = findErrorCode((err as { cause?: unknown }).cause);
    if (code === 'ECONNREFUSED') return 'Connection refused — is the server running?';
    if (code === 'ENOTFOUND') return 'Host not found — check the base URL.';
    if (code === 'ETIMEDOUT') return 'Connection timed out.';
    if (code === 'ECONNRESET') return 'Connection reset by the remote server.';
    if (code === 'EHOSTUNREACH' || code === 'ENETUNREACH') {
      return 'Host unreachable — check the network and base URL.';
    }

    return err.message;
  }

  return 'Unknown error.';
}
