"""
Tests for the rate limiter — both the pure algorithm and the
in-memory implementation that wraps it.
"""

import asyncio

import pytest
import pytest_asyncio

from app.infra.rate_limiter.algorithms import (
    TokenBucketState,
    check_token_bucket,
)
from app.infra.rate_limiter.memory import InMemoryRateLimiter


# ─── Pure algorithm tests ──────────────────────────────────

class TestTokenBucketAlgorithm:
    """Math-only tests with no storage."""

    def test_new_user_first_request_allowed(self):
        """No previous state = full bucket = request allowed."""
        result = check_token_bucket(
            state=None, now=1000, capacity=60, refill_rate_per_sec=1.0
        )
        assert result.allowed is True
        assert result.remaining == 59  # capacity - 1 consumed

    def test_empty_bucket_denies(self):
        """Zero tokens, no elapsed time = denied."""
        state = TokenBucketState(tokens=0.0, last_refill=1000)
        result = check_token_bucket(
            state=state, now=1000, capacity=60, refill_rate_per_sec=1.0
        )
        assert result.allowed is False
        assert result.retry_after >= 1

    def test_refill_after_elapsed_time(self):
        """30 seconds of elapsed time at 1 token/sec = 30 tokens refilled."""
        state = TokenBucketState(tokens=0.0, last_refill=1000)
        result = check_token_bucket(
            state=state, now=1030, capacity=60, refill_rate_per_sec=1.0
        )
        assert result.allowed is True
        assert result.remaining == 29  # 30 refilled, 1 consumed

    def test_refill_capped_at_capacity(self):
        """Excessive elapsed time doesn't overfill the bucket."""
        state = TokenBucketState(tokens=0.0, last_refill=1000)
        # 1000 seconds elapsed would add 1000 tokens at 1/sec.
        result = check_token_bucket(
            state=state, now=2000, capacity=60, refill_rate_per_sec=1.0
        )
        assert result.allowed is True
        assert result.remaining == 59  # capped at 60, then -1

    def test_clock_drift_backward_is_safe(self):
        """If now < last_refill (clock went backward), don't lose tokens."""
        state = TokenBucketState(tokens=5.0, last_refill=2000)
        result = check_token_bucket(
            state=state, now=1000, capacity=60, refill_rate_per_sec=1.0
        )
        assert result.allowed is True
        # Tokens unchanged by the clock-skew defense: 5 - 1 consumed = 4.
        assert result.remaining == 4

    def test_denial_still_advances_clock(self):
        """Even on denial, last_refill is updated to `now`."""
        state = TokenBucketState(tokens=0.0, last_refill=1000)
        result = check_token_bucket(
            state=state, now=1100, capacity=60, refill_rate_per_sec=0.001
        )
        # Tiny refill rate — still denied.
        assert result.allowed is False
        # But the clock has advanced so the next call will refill.
        assert result.new_state.last_refill == 1100


# ─── In-memory limiter tests (with async) ─────────────────

class TestInMemoryRateLimiter:
    """Behavior of the wrapping InMemoryRateLimiter."""

    @pytest.mark.asyncio
    async def test_basic_consumption(self):
        """Five allowed, sixth denied with retry_after."""
        limiter = InMemoryRateLimiter()
        key = "user:1"

        for i in range(5):
            result = await limiter.check(key, limit=5, window_seconds=60)
            assert result.allowed, f"Request {i+1} should be allowed"

        result = await limiter.check(key, limit=5, window_seconds=60)
        assert result.allowed is False
        assert result.retry_after >= 1

    @pytest.mark.asyncio
    async def test_different_keys_independent(self):
        """User A burning their quota doesn't affect user B."""
        limiter = InMemoryRateLimiter()

        for _ in range(3):
            await limiter.check("user:A", limit=3, window_seconds=60)

        # User A is now empty.
        result_a = await limiter.check("user:A", limit=3, window_seconds=60)
        assert result_a.allowed is False

        # User B still has a full bucket.
        result_b = await limiter.check("user:B", limit=3, window_seconds=60)
        assert result_b.allowed is True

    @pytest.mark.asyncio
    async def test_concurrent_requests_atomic(self):
        """20 concurrent requests against a limit of 5 — exactly 5 allowed."""
        limiter = InMemoryRateLimiter()
        key = "user:concurrent"

        async def one_request() -> bool:
            r = await limiter.check(key, limit=5, window_seconds=60)
            return r.allowed

        results = await asyncio.gather(*[one_request() for _ in range(20)])
        allowed_count = sum(1 for r in results if r)

        # Race-condition test: if the lock works, exactly 5 allowed.
        # Without the lock, you'd see 6-10 allowed due to interleaving.
        assert allowed_count == 5

    @pytest.mark.asyncio
    async def test_returns_correct_limit_in_result(self):
        """LimitResult.limit matches the configured limit."""
        limiter = InMemoryRateLimiter()
        result = await limiter.check("user:x", limit=42, window_seconds=60)
        assert result.limit == 42