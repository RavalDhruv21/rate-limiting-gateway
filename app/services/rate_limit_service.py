"""
Rate limiting business logic.

The middleware delegates to this service:
  - service decides WHICH limit applies (tier default or admin override)
  - service applies the upstream health factor (adaptive rate limiting)
  - service calls the rate limiter to check
  - service returns the LimitResult

Keeping policy in a service (not middleware) means:
  - Testable without HTTP machinery.
  - Reusable from anywhere (e.g., a future CLI tool that mass-checks
    usage could call the same service).
"""

from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.infra.rate_limiter.base import LimitResult, RateLimiter
from app.models.db import UserQuotaOverride
from app.utils.time import utc_now

Tier = Literal["free", "pro", "enterprise"]

# Window we apply rate limits over. 60 seconds = per-minute limits.
_WINDOW_SECONDS = 60


def _tier_default(tier: Tier) -> int:
    """Map a tier to its requests-per-window allowance from config."""
    if tier == "free":
        return settings.rate_limit_free
    if tier == "pro":
        return settings.rate_limit_pro
    if tier == "enterprise":
        return settings.rate_limit_enterprise
    return settings.rate_limit_free


async def _lookup_override(
    db: AsyncSession,
    user_id: str,
) -> int | None:
    """
    Return the custom limit for `user_id` if an active override exists.

    Active = exists AND (expires_at is NULL OR in the future).
    Returns None if no active override.
    """
    stmt = select(UserQuotaOverride).where(UserQuotaOverride.user_id == user_id)
    result = await db.execute(stmt)
    override = result.scalar_one_or_none()

    if override is None:
        return None

    if override.expires_at is not None and override.expires_at < utc_now():
        return None

    return override.custom_limit


async def resolve_limit(
    db: AsyncSession,
    user_id: str,
    tier: Tier,
) -> int:
    """
    Decide the base rate limit (requests/minute) for a user.

    Resolution order:
      1. Admin override, if active.
      2. Tier default from config.
    """
    override = await _lookup_override(db, user_id)
    if override is not None:
        return override
    return _tier_default(tier)


async def check_rate_limit(
    limiter: RateLimiter,
    db: AsyncSession,
    user_id: str,
    tier: Tier,
    health_factor: float = 1.0,
) -> LimitResult:
    """
    Top-level entry point used by the middleware.

    Resolves the applicable limit, applies the upstream health factor
    (adaptive rate limiting), then asks the limiter whether this request
    is allowed.

    health_factor: 1.0 = upstream healthy, 0.5 = upstream degraded.
    When degraded, effective limits are halved to protect the upstream.
    """
    base_limit = await resolve_limit(db, user_id, tier)
    effective_limit = max(1, int(base_limit * health_factor))
    key = f"user:{user_id}"
    return await limiter.check(key, effective_limit, _WINDOW_SECONDS)
