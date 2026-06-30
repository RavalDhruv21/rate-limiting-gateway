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

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEV_JWT_SECRET = "change-me-to-a-long-random-string"
_DEV_ADMIN_KEY = "change-me-admin-key"


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
    jwt_secret: str = Field(..., min_length=16)
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60

    # ─── Database ──────────────────────────────────────────
    database_url: str = "postgresql+asyncpg://gateway_user:gateway_pass@localhost:5432/gateway_db"

    # ─── Redis ─────────────────────────────────────────────
    redis_url: str = "redis://localhost:6379/0"

    # ─── Upstream backend ──────────────────────────────────
    upstream_base_url: str = "https://httpbin.org"

    # ─── Admin plane ───────────────────────────────────────
    admin_api_key: str = Field(..., min_length=16)

    # ─── CORS / trusted hosts ──────────────────────────────
    # ALLOWED_ORIGINS: comma-separated list, e.g. "https://app.example.com,https://www.example.com"
    # Empty list = no CORS headers emitted (same-origin only).
    allowed_origins: list[str] = []
    # ALLOWED_HOSTS: comma-separated list. "*" = accept any (fine for dev).
    allowed_hosts: list[str] = ["*"]

    # ─── Rate limit defaults (requests per minute per tier) ─
    rate_limit_free: int = 60
    rate_limit_pro: int = 1000
    rate_limit_enterprise: int = 10000

    # ─── Pydantic config ───────────────────────────────────
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @field_validator("allowed_origins", "allowed_hosts", mode="before")
    @classmethod
    def _split_csv(cls, v: object) -> object:
        """Parse a comma-separated env var string into a list."""
        if isinstance(v, str):
            return [item.strip() for item in v.split(",") if item.strip()]
        return v

    @model_validator(mode="after")
    def _check_production_secrets(self) -> "Settings":
        """Refuse to start in production with placeholder secrets."""
        if self.app_env == "production":
            if self.jwt_secret == _DEV_JWT_SECRET:
                raise ValueError(
                    "JWT_SECRET must be changed for production. "
                    "Generate: python -c 'import secrets; print(secrets.token_urlsafe(32))'"
                )
            if self.admin_api_key == _DEV_ADMIN_KEY:
                raise ValueError(
                    "ADMIN_API_KEY must be changed for production. "
                    "Generate: python -c 'import secrets; print(secrets.token_urlsafe(32))'"
                )
        return self


@lru_cache
def get_settings() -> Settings:
    """Returns the Settings singleton (cached)."""
    return Settings()  # type: ignore[call-arg]


# Module-level singleton for convenient imports.
settings = get_settings()
