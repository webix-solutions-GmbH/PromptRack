"""Who is making this request, and what they are allowed to do.

The modules here split along one line: *what a role means* is pure and lives
in :mod:`app.auth.policy`; *who the caller is* needs the database and lives in
:mod:`app.auth.users` / :mod:`app.auth.sessions` / :mod:`app.auth.tokens`; and
:mod:`app.auth.guards` is the single place that puts the two together and
refuses a request. :mod:`app.auth.router` is the HTTP surface — sign-up, login,
logout, ``me``, and switching workspace; :mod:`app.auth.oidc` adds an optional
second way to establish who the caller is, upstream of the same guards.

Two rules keep a second authorization system from growing beside the first:

* **Roles are asked, never re-decided.** Every guard, every ``can_write``
  boolean the frontend hides a button behind, and (later) the MCP read-only
  gate call the same two predicates in :mod:`app.auth.policy`.
* **The auth tables are not workspace data.** ``users``, ``sessions`` and
  ``api_tokens`` are what a :class:`~app.scope.Scope` is derived *from*, so they
  cannot be read through a scoped repository — which is why their queries live
  here rather than in :mod:`app.repos`.
"""
