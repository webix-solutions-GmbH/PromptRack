# Two stacks, two languages, one repo

`backend/` is Python (FastAPI + async SQLAlchemy 2.0 + Alembic on Postgres);
`frontend/` is TypeScript (Vue 3 + Vite + PrimeVue + Pinia). Check which
directory you are in before assuming a convention from one carries over to
the other — they do not share tooling, package manager, test runner or
formatting rules.

Both are recent major versions with API surface that may differ from your
training data: SQLAlchemy 2.0's `async` API is a different idiom from 1.x,
Pydantic v2 is not Pydantic v1 with new imports, and PrimeVue 4 / Vue 3's
Composition API are not what an older training snapshot may assume. Read the
neighboring code in the file you are editing before writing something novel —
it already establishes the pattern this codebase wants.

CLAUDE.md is the architecture document: read it before a non-trivial change.
