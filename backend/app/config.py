"""App configuration, read from the environment (see root `.env.example`)."""

from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Process-wide settings. Field names map case-insensitively to env vars,
    so `database_url` reads `DATABASE_URL` with no extra alias needed.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Dev fallback so a fresh clone works with nothing else configured —
    # mirrors the old Node app's DEV_DATABASE_URL and docker-compose.dev.yml.
    database_url: str = "postgres://agentval:dev@127.0.0.1:5433/agentval"

    # Max size of the async connection pool. Must exceed the number of runs
    # that can execute concurrently plus normal request concurrency: an
    # executing run holds one connection for its whole duration.
    database_pool_max: int = 10

    @field_validator("database_url")
    @classmethod
    def _asyncpg_scheme(cls, v: str) -> str:
        """Accept the `postgres://`/`postgresql://` form used elsewhere in the
        stack (.env.example, docker-compose) and normalize it to the asyncpg
        driver SQLAlchemy's async engine requires.
        """
        if v.startswith("postgres://"):
            return "postgresql+asyncpg://" + v[len("postgres://") :]
        if v.startswith("postgresql://") and "+asyncpg" not in v:
            return "postgresql+asyncpg://" + v[len("postgresql://") :]
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()
