"""Async SQLAlchemy engine, session factory, and the `get_session` dependency.

One pooled async engine per process. `pool_size` mirrors the old Node app's
`DATABASE_POOL_MAX`: an executing run holds one connection for its whole
duration (see the future `run_lock` service), so this must exceed concurrent
runs plus normal request concurrency.
"""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings

settings = get_settings()

engine = create_async_engine(
    settings.database_url,
    pool_size=settings.database_pool_max,
    pool_pre_ping=True,
)

async_session = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a session scoped to one request."""
    async with async_session() as session:
        yield session
