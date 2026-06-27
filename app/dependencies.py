"""
FastAPI dependency providers — Redis + PostgreSQL.

The service layer, middleware, and routes depend on these providers,
never on concrete infrastructure classes directly.
"""

from typing import Annotated

import redis.asyncio as aioredis
from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.infra.database import get_db_session
from app.infra.log_store.base import LogStore
from app.infra.log_store.postgres_log_store import PostgresLogStore
from app.infra.rate_limiter.base import RateLimiter
from app.infra.rate_limiter.redis_limiter import RedisRateLimiter

# ─── Redis client singleton ────────────────────────────────
# One connection pool shared across all requests.
# Created at module import time; closed in main.py lifespan.
_redis_client = aioredis.from_url(
    settings.redis_url,
    encoding="utf-8",
    decode_responses=False,  # Lua scripts return bytes; we decode manually
)

# ─── Singletons ────────────────────────────────────────────
_rate_limiter: RateLimiter = RedisRateLimiter(_redis_client)
_log_store: LogStore = PostgresLogStore()


# ─── Provider functions ───────────────────────────────────

def get_rate_limiter() -> RateLimiter:
    """Return the process-wide Redis-backed rate limiter."""
    return _rate_limiter


def get_log_store() -> LogStore:
    """Return the process-wide PostgreSQL-backed log store."""
    return _log_store


def get_redis_client() -> aioredis.Redis:
    """Return the Redis client (for lifespan cleanup)."""
    return _redis_client


# ─── Type aliases ─────────────────────────────────────────

DBSession = Annotated[AsyncSession, Depends(get_db_session)]
RateLimiterDep = Annotated[RateLimiter, Depends(get_rate_limiter)]
LogStoreDep = Annotated[LogStore, Depends(get_log_store)]


# ─── Admin auth ───────────────────────────────────────────

async def require_admin(
    x_admin_key: Annotated[str | None, Header()] = None,
) -> None:
    if not x_admin_key or x_admin_key != settings.admin_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing admin key.",
            headers={"WWW-Authenticate": "X-Admin-Key"},
        )


AdminAuth = Depends(require_admin)
AdminAuthDep = Annotated[None, Depends(require_admin)]
