"""The only place a query is written.

No route handler, service or MCP tool builds SQL of its own: every read and
write goes through a function in this package, and every one of those functions
takes a :class:`app.scope.Scope` first (the exception is
:mod:`app.repos.customers`, which is *about* workspaces rather than inside one).
Keeping the queries here is what makes the scope rule checkable by reading one
directory instead of the whole app.

Two conventions hold throughout:

* **Sessions come from the caller.** Repository functions take an
  ``AsyncSession``, ``flush()`` when they need a generated id, and **never
  commit** — the request boundary (or an explicit
  :func:`app.repos.scoped.transaction` block) decides where the unit of work
  ends. That is what lets several writes land atomically without any caller
  having to know how.
* **Children inherit scope through their parent.** ``endpoint_models``,
  ``tools``, ``test_cases``, ``test_case_toolsets``, ``prompt_versions`` and
  ``run_results`` carry no ``customer_id``: reads join their parent, and writes
  express the same thing as the :func:`app.repos.scoped.scope_through_parent`
  subquery.

Import the modules, not their contents::

    from app.repos import endpoints as endpoints_repo
"""
