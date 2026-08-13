"""The advisory lock that keeps one run from executing twice.

Ports `git show master:tests/integration/run-lock.test.ts`. Nothing here is
mockable: the whole point of `pg_try_advisory_lock` is that Postgres, not this
process, is what makes the claim exclusive — a second app process has to lose
the race just as a second call inside one process does, and `pg_locks` has to
report the truth to a connection that is not the holder.

No rows are involved, so these run ids need no runs behind them.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.run_lock import acquire_run_lock, is_run_executing


async def test_exactly_one_holder_claims_a_run(session: AsyncSession):
    first = await acquire_run_lock(1)
    assert first is not None

    assert await acquire_run_lock(1) is None
    assert await is_run_executing(session, 1) is True

    await first.release()
    assert await is_run_executing(session, 1) is False

    second = await acquire_run_lock(1)
    assert second is not None
    await second.release()


async def test_runs_lock_independently(session: AsyncSession):
    one = await acquire_run_lock(1)
    two = await acquire_run_lock(2)
    assert one is not None
    assert two is not None

    assert await is_run_executing(session, 1) is True
    assert await is_run_executing(session, 2) is True

    await one.release()
    assert await is_run_executing(session, 1) is False
    assert await is_run_executing(session, 2) is True

    await two.release()
    assert await is_run_executing(session, 2) is False


async def test_releasing_twice_is_safe(session: AsyncSession):
    lock = await acquire_run_lock(3)
    assert lock is not None

    await lock.release()
    await lock.release()

    assert await is_run_executing(session, 3) is False


async def test_a_released_connection_carries_no_lock_back_into_the_pool(
    session: AsyncSession,
):
    """The unlock is explicit for this reason: the connection goes back to the
    pool, and a session-level lock riding along on it would outlive the run
    forever. Cycling more locks than the pool has connections is what would
    surface that — a recycled connection still holding lock 4 would make the
    reclaim below fail.
    """
    for run_id in range(4, 20):
        lock = await acquire_run_lock(run_id)
        assert lock is not None
        await lock.release()

    for run_id in range(4, 20):
        assert await is_run_executing(session, run_id) is False
