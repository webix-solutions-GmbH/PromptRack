"""App configuration, read from the environment (see root `.env.example`)."""

import secrets
from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Process-wide settings. Field names map case-insensitively to env vars,
    so `database_url` reads `DATABASE_URL` with no extra alias needed.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Dev fallback so a fresh clone works with nothing else configured —
    # mirrors the old Node app's DEV_DATABASE_URL and docker-compose.dev.yml.
    database_url: str = "postgres://promptrack:dev@127.0.0.1:5433/promptrack"

    # Max size of the async connection pool. Must exceed the number of runs
    # that can execute concurrently plus normal request concurrency: an
    # executing run holds one connection for its whole duration.
    database_pool_max: int = 10

    # OIDC (optional). Unset `oidc_issuer` means no SSO: `app.auth.oidc`
    # mounts no routes at all and the app runs on email/password alone.
    oidc_issuer: str | None = None
    oidc_client_id: str | None = None
    oidc_client_secret: str | None = None
    # Comma-separated, mirroring the old app's `OIDC_SCOPES`.
    oidc_scopes: str = "openid,profile,email"
    # The role a *new* OIDC-provisioned account gets. Read through
    # `parse_role` wherever it is used, never trusted verbatim — an
    # unrecognised value has to land on viewer, never admin.
    oidc_default_role: str = "member"

    # Signs the short-lived cookie Authlib's Starlette client uses to carry
    # the OAuth `state`/`nonce` across the redirect to the provider and back.
    # Unrelated to `app.auth.sessions` (a signed-in user's session): this one
    # only has to survive a single login round trip, so a fresh random value
    # per process start is fine for a single instance — pin it via env for a
    # multi-process deployment, so a callback landing on a different worker
    # than the redirect still verifies.
    session_secret: str = Field(default_factory=lambda: secrets.token_urlsafe(32))

    # "development" (the default, matching a fresh clone with nothing else
    # configured) or "production" — the latter is what `docker-entrypoint.sh`
    # (Task 6.3) sets. Mirrors the old Node app's `NODE_ENV`; used only by
    # `app.api.mocks.mocks_enabled` today.
    environment: str = "development"
    # Forces the mock LLM / mock MCP routes on even when `environment` is
    # "production" — see `app.api.mocks`. Off by default so a production
    # image never accidentally exposes a fake OpenAI endpoint.
    enable_mocks: bool = False

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
