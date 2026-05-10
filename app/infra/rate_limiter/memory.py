"""
In-memory rate limiter — v1 implementation.

Storage:
  - self._buckets: dict mapping key -> TokenBucketState.
  - self._locks:   dict mapping key -> asyncio.Lock for atomic updates.

Limitations vs. Redis (intentional, this is a learning project):
  - Single-process only. Multiple gateway instances would each have
    their own state, and a user could effectively send N times their
    limit (where N is the number of instances). Redis fixes this by
    being a shared, atomic store.
  - State is lost on restart. After a deployment, every user starts
    with a fresh full bucket. Acceptable for v1; a real production
    system uses persistent shared state.

The asyncio.Lock per key gives us per-user atomicity within this
process — critical even with one gateway instance, because FastAPI
handles concurrent requests on a single event loop.
"""

import asyncio

from app.infra.rate_limiter.algorithms import (
    TokenBucketState,
    check_token_bucket,
)
from app.infra.rate_limiter.base import LimitResult, RateLimiter
from app.utils.time import epoch_now


class InMemoryRateLimiter(RateLimiter):
    """
    Token-bucket rate limiter using process-local memory.

    Implements the RateLimiter abstract interface. Drop-in replaceable
    by RedisRateLimiter in v2 — neither the service layer nor the
    middleware needs to know the difference.
    """

    def __init__(self) -> None:
        # Per-key state.
        self._buckets: dict[str, TokenBucketState] = {}
        # Per-key locks. We allocate a lock the first time a key is
        # seen, and reuse it forever. (We never clean these up — for
        # a learning project that's fine. A production version might
        # garbage-collect keys not seen recently.)
        self._locks: dict[str, asyncio.Lock] = {}
        # Lock for the locks dict itself, to safely allocate new locks
        # under concurrent first-time access for the same key.
        self._meta_lock = asyncio.Lock()

    async def _get_lock(self, key: str) -> asyncio.Lock:
        """
        Get or create the per-key lock.

        We need _meta_lock because two concurrent first-time calls
        for the same key could both allocate fresh locks and bypass
        each other. The meta_lock is held only briefly — long enough
        to ensure exactly one Lock object exists per key.
        """
        if key in self._locks:
            return self._locks[key]
        async with self._meta_lock:
            # Double-check inside the lock — another coroutine may have
            # just created it between our first check and the lock acquisition.
            if key not in self._locks:
                self._locks[key] = asyncio.Lock()
            return self._locks[key]

    async def check(
        self,
        key: str,
        limit: int,
        window_seconds: int,
    ) -> LimitResult:
        """
        Check whether the request keyed at `key` is allowed.

        Atomically reads, updates, and writes the bucket state under
        a per-key lock — so concurrent requests for the same user
        produce correct results.
        """
        # Convert (limit, window) into token-bucket parameters:
        #   capacity = limit (max burst = the limit itself)
        #   refill rate = limit / window (so a full window of
        #   inactivity refills the bucket)
        capacity = limit
        refill_rate = limit / window_seconds

        lock = await self._get_lock(key)
        async with lock:
            previous_state = self._buckets.get(key)
            now = epoch_now()

            result = check_token_bucket(
                state=previous_state,
                now=now,
                capacity=capacity,
                refill_rate_per_sec=refill_rate,
            )

            # Persist new state — even on rejection, so the refill
            # clock keeps advancing.
            self._buckets[key] = result.new_state

        return LimitResult(
            allowed=result.allowed,
            limit=limit,
            remaining=result.remaining,
            retry_after=result.retry_after,
        )

    # ─── Diagnostic / test helpers ─────────────────────────
    # Not part of the abstract interface — only used by tests
    # and by an admin debug endpoint we may add later.

    def _peek_state(self, key: str) -> TokenBucketState | None:
        """Read current state without consuming. For tests only."""
        return self._buckets.get(key)

    def _reset(self) -> None:
        """Clear all state. For tests only."""
        self._buckets.clear()
        self._locks.clear()