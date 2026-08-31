"""Integration test harness: a real Postgres, the committed migrations.

Everything under `tests/integration/**` needs a database — the wired-up half
of the invariants whose pure half already lives in the fast suite (e.g.
`app.scope`'s predicates are checked as compiled SQL text in
`tests/test_scope.py`; that a scoped query really cannot see another
workspace's row is this suite's job instead).

Bring-up happens once per test *process*, at import time, strictly before
`app.db` (or anything that imports it) is ever imported — `app.db` builds its
async engine from `DATABASE_URL` the moment it is first imported, so the
environment variable has to be right before that happens:

1. `TEST_DATABASE_URL` set -> use it verbatim, manage no container. The
   escape hatch for a database the caller already runs (CI, a developer who
   prefers a long-lived instance).
2. Otherwise -> start (or reuse) a throwaway `postgres:17-alpine` container
   with its data in a tmpfs on port 55432, and remove it once the whole test
   session finishes.

Either way the committed migrations are applied with `alembic upgrade head`
run as a subprocess rather than through alembic's Python API in-process:
`alembic/env.py` drives its async engine with `asyncio.run()`, which cannot
nest inside whatever loop pytest-asyncio may already be running, so a
subprocess sidesteps the question entirely — the same reason `scripts/dev.sh`
shells out to `uv run alembic upgrade head` instead of importing alembic.

Every table is truncated before each test.
"""

from __future__ import annotations

import os
import subprocess
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

BACKEND_DIR = Path(__file__).resolve().parents[2]
CONTAINER_NAME = "promptrack-test-pg"
PORT = 55432
DEFAULT_TEST_DATABASE_URL = f"postgres://promptrack:test@127.0.0.1:{PORT}/promptrack_test"


def _docker(*args: str, **kwargs: Any) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["docker", *args], text=True, **kwargs)


def _container_running() -> bool:
    result = _docker("ps", "-q", "-f", f"name=^{CONTAINER_NAME}$", capture_output=True)
    return result.returncode == 0 and result.stdout.strip() != ""


def _start_container() -> None:
    if _container_running():
        return
    # --rm plus a tmpfs: nothing survives, and initdb is fast enough to pay
    # for itself on every run.
    result = _docker(
        "run",
        "-d",
        "--rm",
        "--name",
        CONTAINER_NAME,
        "-p",
        f"127.0.0.1:{PORT}:5432",
        "-e",
        "POSTGRES_USER=promptrack",
        "-e",
        "POSTGRES_PASSWORD=test",
        "-e",
        "POSTGRES_DB=promptrack_test",
        # UTF8/C: prompt content can carry Unicode Tags (U+E0000+).
        "-e",
        "POSTGRES_INITDB_ARGS=--encoding=UTF8 --lc-collate=C --lc-ctype=C",
        "--tmpfs",
        "/var/lib/postgresql/data:rw",
        "postgres:17-alpine",
    )
    if result.returncode != 0:
        raise RuntimeError(
            "could not start the throwaway postgres container — is docker running? "
            f"(docker run exited {result.returncode})"
        )


def _wait_ready(deadline_s: float = 60) -> None:
    deadline = time.monotonic() + deadline_s
    while time.monotonic() < deadline:
        probe = _docker(
            "exec",
            CONTAINER_NAME,
            "pg_isready",
            "-U",
            "promptrack",
            "-d",
            "promptrack_test",
            capture_output=True,
        )
        if probe.returncode == 0:
            return
        time.sleep(0.5)
    raise RuntimeError("postgres did not become ready within 60s")


def _stop_container() -> None:
    _docker("rm", "-f", CONTAINER_NAME, capture_output=True)


def _migrate(url: str) -> None:
    result = subprocess.run(
        ["uv", "run", "alembic", "upgrade", "head"],
        cwd=BACKEND_DIR,
        env={**os.environ, "DATABASE_URL": url},
    )
    if result.returncode != 0:
        raise RuntimeError("alembic upgrade head failed against the test database")


# ---------------------------------------------------------------------------
# Module-level bring-up. Runs exactly once, the moment pytest imports this
# file (pytest caches conftest modules for the session) — and strictly before
# the `from app...` imports below, which is what keeps `app.db`'s
# module-level engine pointed at the test database rather than dev.
# ---------------------------------------------------------------------------

_external_url = os.environ.get("TEST_DATABASE_URL")
_managed_container = _external_url is None
TEST_DATABASE_URL = _external_url or DEFAULT_TEST_DATABASE_URL

if _managed_container:
    _start_container()
    _wait_ready()

os.environ["DATABASE_URL"] = TEST_DATABASE_URL

_migrate(TEST_DATABASE_URL)

from app.config import get_settings  # noqa: E402

# Belt and suspenders: nothing should have called `get_settings()` before this
# point, but clearing the cache means this file's import order is the only
# thing that has to be right, not every future caller's.
get_settings.cache_clear()

from app.db import async_session, engine  # noqa: E402
from app.repos.customers import create_customer  # noqa: E402
from app.scope import Scope, scope_from_row  # noqa: E402

#: Every table the app owns, in one statement so CASCADE resolves the FKs
#: regardless of which tables reference which.
ALL_TABLES = [
    "customers",
    "endpoints",
    "endpoint_models",
    "prompts",
    "prompt_versions",
    "toolsets",
    "tools",
    "documents",
    "test_groups",
    "test_cases",
    "test_case_toolsets",
    "param_groups",
    "runs",
    "run_results",
    "users",
    "sessions",
    "api_tokens",
    "user_invites",
]


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    del session, exitstatus
    if _managed_container:
        _stop_container()


@pytest_asyncio.fixture(autouse=True)
async def _clean_database() -> AsyncIterator[None]:
    """Truncates every table before each test.

    A fresh schema rather than a fresh database is what gives each test its
    known-empty starting point without paying `docker run` (and `initdb`)
    again per test.
    """
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {', '.join(ALL_TABLES)} RESTART IDENTITY CASCADE"))
    yield


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    async with async_session() as db_session:
        yield db_session


@pytest_asyncio.fixture
def create_workspace(
    session: AsyncSession,
) -> Callable[[str], Awaitable[tuple[int, Scope]]]:
    """A factory fixture: `await create_workspace("A")` -> `(customer_id, scope)`.

    `scope_from_row` is the same constructor the background executor uses, so
    a test scope is indistinguishable from a real one — there is no
    test-only way to make a `Scope`. A factory rather than a fixed value
    because most of this suite's tests need two or more workspaces.
    """

    async def _create(name: str) -> tuple[int, Scope]:
        customer = await create_customer(session, name=name)
        return customer.id, scope_from_row(customer.id)

    return _create


@pytest_asyncio.fixture
async def scope(
    create_workspace: Callable[[str], Awaitable[tuple[int, Scope]]],
) -> Scope:
    """The one workspace most tests need — a convenience over `create_workspace`
    for tests that aren't themselves about cross-workspace behavior.
    """
    _, workspace_scope = await create_workspace("Acme")
    return workspace_scope
