# Security

This app authenticates its users, but is not hardened for exposure to the open
internet — run it on a trusted network or behind a reverse proxy you control.
Endpoint API keys and MCP toolset headers are stored in Postgres in plaintext,
so treat the database and its backups as sensitive.

## Reporting a vulnerability

Please report vulnerabilities **privately**, through GitHub's *Report a
vulnerability* button under this repository's Security tab, which opens a
private advisory visible only to the maintainers. Do not open a public issue for
a vulnerability — a public issue discloses it to everyone running the app before
there is a fix.

Use a normal issue for anything that is a hardening suggestion rather than an
exploitable flaw.

There is no bug bounty and no guaranteed response time.
