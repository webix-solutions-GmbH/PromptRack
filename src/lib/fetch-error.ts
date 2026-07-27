/**
 * Turn a fetch()/AbortController failure into a short, human-readable message
 * suitable for display in the UI (connection refused, timeout, DNS, etc).
 */
export function describeFetchError(err: unknown): string {
  if (err instanceof Error) {
    if (err.name === 'AbortError') {
      return 'Connection timed out.';
    }

    const cause = (err as { cause?: unknown }).cause;
    if (cause && typeof cause === 'object' && 'code' in cause) {
      const code = (cause as { code?: unknown }).code;
      if (code === 'ECONNREFUSED') return 'Connection refused — is the server running?';
      if (code === 'ENOTFOUND') return 'Host not found — check the base URL.';
      if (code === 'ETIMEDOUT') return 'Connection timed out.';
      if (code === 'ECONNRESET') return 'Connection reset by the remote server.';
    }

    return err.message;
  }

  return 'Unknown error.';
}
