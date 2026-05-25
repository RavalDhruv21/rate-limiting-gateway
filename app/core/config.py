"""
Application configuration.

All environment variables are loaded into a single typed Settings object
via pydantic-settings. Import `settings` from this module anywhere in the
app that needs a config value. Never read os.environ directly.

Fail-fast principle: if a required env var is missing or has the wrong
type, the app raises at startup — not at the first request that happens
to need it.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Typed application settings.

    Field names map to env vars in UPPER_CASE. For example, the attribute
    `jwt_secret` is loaded from the env var JWT_SECRET.
    """

    # ─── Application ───────────────────────────────────────
    app_name: str = "rate-limited-gateway"
    app_env: Literal["development", "production", "test"] = "development"
    log_level: str = "INFO"

    # ─── JWT ───────────────────────────────────────────────
    # `...` (Ellipsis) means "required, no default". Combined with
    # min_length=16, a weak or missing secret fails at startup.
    jwt_secret: str = Field(..., min_length=16)
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60

    # ─── Database ──────────────────────────────────────────
    # v1 default is SQLite. v2 changes this to postgresql+asyncpg://...
    # No other code changes needed — that's the point of using SQLAlchemy.
    database_url: str = "sqlite+aiosqlite:///./gateway.db"
    # ─── Redis ─────────────────────────────────────────────────
    # Used by RedisRateLimiter in v2.
    redis_url: str = "redis://localhost:6379/0"

    # ─── Upstream backend ──────────────────────────────────
    upstream_base_url: str = "https://httpbin.org"

    # ─── Admin plane ───────────────────────────────────────
    admin_api_key: str = Field(..., min_length=16)

    # ─── Rate limit defaults (requests per minute per tier) ─
    rate_limit_free: int = 60
    rate_limit_pro: int = 1000
    rate_limit_enterprise: int = 10000

    # ─── Pydantic config ───────────────────────────────────
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,  # env vars are conventionally upper-case
        extra="ignore",        # don't fail if .env has extra unknown vars
    )


@lru_cache
def get_settings() -> Settings:
    """
    Returns the Settings singleton.

    Wrapped in lru_cache so that Settings() is instantiated only once per
    process — reading .env and validating all fields has a small cost we
    don't want to pay on every call. This pattern also makes it trivial
    to override settings in tests (see tests/conftest.py in Step 5).
    """
    return Settings()  # type: ignore[call-arg]


# Module-level singleton for convenient imports.
# Prefer `get_settings` (the function) when using FastAPI's DI system.
settings = get_settings()