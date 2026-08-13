# Accounts, roles and security

Sign-in is email + password, optionally single sign-on. There is no public registration:
**the first account ever created is the administrator**, and the sign-up endpoint is refused
from then on. Every further account is created by an admin under `/admin/users` or provisioned
by your identity provider.

## Roles

| Role | May |
| --- | --- |
| **Admin** | Everything a member can, plus users, machines and toolset credentials. |
| **Member** | Prompts, system prompts, tools, runs and ratings. |
| **Viewer** | Read-only. |

The split is **content versus credentials**. A machine's base URL + API key and a toolset's
MCP URL + headers are secrets, so they are admin-only; the tools *inside* a toolset are
content, and a member may edit them.

A role change takes effect on the requester's next request — sessions carry no cached copy.
Controls a role cannot use are not rendered, and every server action, route handler and page
re-checks the role server-side regardless.

## Single sign-on (optional)

Set `OIDC_ISSUER` and `OIDC_CLIENT_ID` (plus `OIDC_CLIENT_SECRET` for a confidential client)
and a "Single sign-on" button appears on the sign-in page. Discovery is automatic: the
issuer's `/.well-known/openid-configuration` is read, so no endpoint URLs are configured by
hand. Leaving `OIDC_ISSUER` empty drops the plugin and the button entirely.

The **redirect URI to register with the provider** is:

```
${BETTER_AUTH_URL}/api/auth/oauth2/callback/oidc
```

| Provider | `OIDC_ISSUER` |
| --- | --- |
| Entra ID | `https://login.microsoftonline.com/<tenant-id>/v2.0` |
| Keycloak | `https://<host>/realms/<realm>` |
| Authentik | `https://<host>/application/o/<slug>/` |

New SSO users are provisioned with `OIDC_DEFAULT_ROLE` (default `member`); anything
unrecognised there degrades to `viewer`, never to admin. Entra ID does not reliably emit an
`email` claim, so `preferred_username` and `upn` are accepted as fallbacks.

## API tokens

Per-user tokens for the [MCP API](mcp-api.md) are created under `/account/tokens`. They are
32 random bytes prefixed `amv_`, stored as a SHA-256 hash and shown exactly once, at creation.
A token **acts as the user who created it and carries their role**, so a viewer's token is
refused every tool that writes.

## Security posture

- **Endpoint API keys and MCP toolset headers are stored in PostgreSQL in plaintext.** Treat
  the database, and any backup of it, as sensitive.
- Keep `ENABLE_MOCKS` unset in production. It exposes `/api/mock-llm` and `/api/mock-mcp`,
  which accept and echo whatever is sent to them. Without it they are 404 in a production
  build — they should not appear to exist.
- The app has app-level authentication but is not hardened for the open internet. Run it on a
  trusted network or behind a reverse proxy you control.
- A judge model reading run results is itself injectable: `get_run_result` returns
  `prompt_text`, which for the seeded injection suite carries live payloads. Grade from
  `expected_output` plus the response, and never let a judge's output pick a tool call.

Vulnerability reports: see [SECURITY.md](../SECURITY.md).
