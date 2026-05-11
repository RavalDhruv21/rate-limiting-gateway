"""
FastAPI dependency providers.

This is the SEAM. Every concrete implementation of the abstract
interfaces (RateLimiter, LogStore) is selected here. To upgrade from
v1 to v2 (in-memory + SQLite → Redis + Postgres), you change the
return value of these factory functions; nothing else in the codebase
needs to know.

For example, switching the rate limiter is a single edit:

    # v1:
    def get_rate_limiter() -> RateLimiter:
        return _rate_limiter  # InMemoryRateLimiter singleton

    # v2:
    def get_rate_limiter() -> RateLimiter:
        return _redis_rate_limiter  # RedisRateLimiter singleton

All service, middleware, and route code stays untouched.
"""

from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.infra.database import get_db_session
from app.infra.log_store.base import LogStore
from app.infra.log_store.sqlite import SqliteLogStore
from app.infra.rate_limiter.base import RateLimiter
from app.infra.rate_limiter.memory import InMemoryRateLimiter

# ─── Singletons ────────────────────────────────────────────
# These objects hold state (the rate limiter's in-memory buckets) or
# are expensive to construct (the log store's underlying engine).
# We create ONE instance per process and reuse it across all requests.
_rate_limiter: RateLimiter = InMemoryRateLimiter()
_log_store: LogStore = SqliteLogStore()


# ─── Provider functions ───────────────────────────────────
# Used as Depends(...) in routes and middleware. The functions are
# tiny — their value is being a single seam for swapping implementations.

def get_rate_limiter() -> RateLimiter:
    """Return the process-wide rate limiter."""
    return _rate_limiter


def get_log_store() -> LogStore:
    """Return the process-wide log store."""
    return _log_store


# ─── Type aliases for cleaner route signatures ─────────────
# Using these saves repeating `= Depends(get_rate_limiter)` in every
# route. Routes can write `RateLimiterDep` and it expands to the full
# annotation.

DBSession = Annotated[AsyncSession, Depends(get_db_session)]
RateLimiterDep = Annotated[RateLimiter, Depends(get_rate_limiter)]
LogStoreDep = Annotated[LogStore, Depends(get_log_store)]


# ─── Admin auth dependency ─────────────────────────────────

async def require_admin(
    x_admin_key: Annotated[str | None, Header()] = None,
) -> None:
    """
    Guard admin routes with a static API key.

    Admin auth is intentionally separate from user JWT auth — different
    trust domain, different rotation cadence, different blast radius
    on compromise. Mixing the two is a classic security mistake.

    The key is read from the X-Admin-Key header (FastAPI auto-converts
    HTTP header `X-Admin-Key` to function parameter `x_admin_key`).
    """
    if not x_admin_key or x_admin_key != settings.admin_api_key:
        # Raise FastAPI's HTTPException directly here (rather than our
        # GatewayError) because this dependency runs before our app's
        # exception handler chain. Both result in 401 to the client.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing admin key.",
            headers={"WWW-Authenticate": "X-Admin-Key"},
        )

# A Depends() object — for use in APIRouter(dependencies=[...]) or
# as a route parameter default.
AdminAuth = Depends(require_admin)

# A type annotation — for use as `admin: AdminAuthDep` in route signatures.
# Not currently used by any route (all admin routes get auth from the
# router-level dependencies=[AdminAuth] above), but defined for completeness.
AdminAuthDep = Annotated[None, Depends(require_admin)]