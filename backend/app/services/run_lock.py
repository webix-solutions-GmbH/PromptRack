"""One execution per run, across processes — a Postgres advisory lock.

The lock lives on a **dedicated connection held for the whole run**, and that
is the point: it dies with the connection, so a crashed process releases it
automatically (rows left `running` are reclaimed to `pending` by the next
execution), while more than one app process is still safe. A lock *table*
would have needed expiry and heartbeats to get the same crash semantics.

This is the one place outside `app.db` that reaches for the engine rather than
taking a session from its caller: a session's connection goes back to the pool
at the end of the request, and a session-level advisory lock has to outlive
that. The connection runs in `AUTOCOMMIT` so it does not sit idle-in-transaction
for the length of a run, holding a snapshot open against vacuum.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from app.db import engine

#: Namespace for this app's advisory locks. Postgres advisory locks are a
#: single global int8 (or two int4s) per database, so the class id keeps run
#: locks from colliding with anything else that might use them later. `PRRK`
#: read as a big-endian int4.
LOCK_CLASS = 1347354699

_TRY_LOCK = text("SELECT pg_try_advisory_lock(:class_id, :run_id)")
_UNLOCK = text("SELECT pg_advisory_unlock(:class_id, :run_id)")

# For a two-key advisory lock, `pg_locks` reports classid = the first key,
# objid = the second and objsubid = 2.
_IS_LOCKED = text(
    """
    SELECT 1 FROM pg_locks
     WHERE locktype = 'advisory'
       AND database = (SELECT oid FROM pg_database WHERE datname = current_database())
       AND classid = :class_id
       AND objid = :run_id
       AND objsubid = 2
       AND granted
    """
)


class RunLock:
    """A claim on one run's execution, released by :meth:`release`."""

    def __init__(self, run_id: int, connection: AsyncConnection) -> None:
        self._run_id = run_id
        self._connection = connection
        self._released = False

    @property
    def run_id(self) -> int:
        return self._run_id

    async def release(self) -> None:
        """Unlocks and returns the connection to the pool. Safe to call twice.

        The unlock is explicit rather than left to the connection close: the
        connection goes back into the pool, and a session-level lock riding
        along on it would never be released.
        """
        if self._released:
            return
        self._released = True
        try:
            await self._connection.execute(
                _UNLOCK, {"class_id": LOCK_CLASS, "run_id": self._run_id}
            )
        except Exception:
            # The connection is gone; the lock died with it.
            pass
        finally:
            await self._connection.close()


async def acquire_run_lock(run_id: int) -> RunLock | None:
    """Claims exclusive execution of a run, or None when someone else holds it.

    None is what the execute route turns into a 409 — the caller decides,
    because "already running" is a legitimate answer to a resume request, not
    an error here.
    """
    connection = await engine.connect()
    try:
        await connection.execution_options(isolation_level="AUTOCOMMIT")
        locked = await connection.scalar(
            _TRY_LOCK, {"class_id": LOCK_CLASS, "run_id": run_id}
        )
    except Exception:
        await connection.close()
        raise

    if not locked:
        await connection.close()
        return None
    return RunLock(run_id, connection)


async def is_run_executing(session: AsyncSession, run_id: int) -> bool:
    """Whether some process is executing this run.

    Read-only: it inspects `pg_locks` rather than trying to take the lock, so
    asking the question can never answer it by accident.
    """
    row = await session.execute(_IS_LOCKED, {"class_id": LOCK_CLASS, "run_id": run_id})
    return row.first() is not None
