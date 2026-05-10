"""
Abstract interface for rate limiters.

This is the contract every rate limiter implementation must satisfy.
The service layer calls the abstract methods; it never knows which
concrete implementation is plugged in.

To add a new backend (e.g., Redis), create a subclass that implements
`check()` — that's the only required method. Everything else stays
the same.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class LimitResult:
    """
    Result of a rate-limit check.

    Returned by RateLimiter.check() to whoever's deciding what to do
    with the request. The HTTP middleware uses these fields to set
    the X-RateLimit-* response headers.
    """

    allowed: bool
    limit: int             # the limit that was applied
    remaining: int         # tokens/requests remaining in the window
    retry_after: int       # seconds until the user can retry (0 if allowed)


class RateLimiter(ABC):
    """
    Abstract rate limiter.

    Concrete implementations (InMemoryRateLimiter, future RedisRateLimiter)
    must override `check`. The service layer calls this method and reacts
    to the LimitResult — without knowing the storage backend.
    """

    @abstractmethod
    async def check(
        self,
        key: str,
        limit: int,
        window_seconds: int,
    ) -> LimitResult:
        """
        Check whether the request identified by `key` is within limits.

        Args:
            key: an opaque string identifying the rate-limit bucket. Typically
                 "user:{user_id}" but could include endpoint scoping
                 (e.g., "user:{user_id}:endpoint:/api/orders") if we want
                 per-endpoint limits later.
            limit: max requests allowed in the window.
            window_seconds: window size. With token bucket, this together
                            with `limit` determines the refill rate
                            (limit/window_seconds tokens per second).

        Returns:
            LimitResult describing whether the request was allowed, the
            remaining quota, and (if denied) when to retry.

        IMPORTANT contract for implementations: this method must be
        "atomic" in the sense that two concurrent calls for the same
        key must not both succeed if only one token is left. The
        in-memory implementation uses asyncio.Lock; the Redis version
        will use a Lua script.
        """
        ...