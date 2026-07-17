"""
Enterprise tier throughput tests.

Validates that the 10,000 req/min enterprise limit works correctly:
  1. Token bucket math handles 10,000 capacity.
  2. Concurrent requests all pass within the limit.
  3. Requests beyond the limit are correctly rejected with 429.
  4. X-RateLimit-Limit header reports 10,000 for enterprise users.
"""

import asyncio

import pytest

from app.core.security import create_access_token
from app.infra.rate_limiter.algorithms import TokenBucketState, check_token_bucket


# ─── Pure algorithm tests (no I/O) ────────────────────────────────────────────

class TestEnterpriseThroughputAlgorithm:
    """Validate token bucket math at 10,000 capacity."""

    def test_enterprise_burst_capacity(self):
        """A fresh enterprise bucket allows 10,000 requests before exhaustion."""
        capacity = 10_000
        refill_rate = capacity / 60  # tokens per second

        state = None
        now = 1000.0
        allowed_count = 0

        for _ in range(10_000):
            result = check_token_bucket(
                state=state,
                now=now,
                capacity=capacity,
                refill_rate_per_sec=refill_rate,
            )
            assert result.allowed, f"Request {allowed_count + 1} should be allowed"
            state = result.new_state
            allowed_count += 1

        assert allowed_count == 10_000

    def test_enterprise_request_10001_denied(self):
        """Request 10,001 with no elapsed time is denied."""
        capacity = 10_000
        refill_rate = capacity / 60

        state = None
        now = 1000.0

        for _ in range(10_000):
            result = check_token_bucket(
                state=state, now=now, capacity=capacity, refill_rate_per_sec=refill_rate
            )
            state = result.new_state

        # 10,001st — bucket exhausted, no time elapsed
        result = check_token_bucket(
            state=state, now=now, capacity=capacity, refill_rate_per_sec=refill_rate
        )
        assert result.allowed is False
        assert result.retry_after >= 1

    def test_enterprise_refills_at_correct_rate(self):
        """After exhaustion, 1 second refills ~166 tokens (10000/60 ≈ 166.7/sec)."""
        capacity = 10_000
        refill_rate = capacity / 60
        now = 1000.0

        # Exhaust the bucket
        state = TokenBucketState(tokens=0.0, last_refill=now)

        # 1 second later
        result = check_token_bucket(
            state=state, now=now + 1.0, capacity=capacity, refill_rate_per_sec=refill_rate
        )
        assert result.allowed is True
        # Should have refilled ~166 tokens, consumed 1 → ~165 remaining
        assert result.remaining >= 165
        assert result.remaining <= 167

    def test_enterprise_vs_free_limit_difference(self):
        """Enterprise allows 166x more burst than free tier."""
        free_capacity = 60
        enterprise_capacity = 10_000

        assert enterprise_capacity / free_capacity == pytest.approx(166.67, rel=0.01)


# ─── Integration tests (Redis-backed, concurrent) ─────────────────────────────

@pytest.mark.asyncio
class TestEnterpriseThroughputIntegration:
    """End-to-end rate limit tests via the full FastAPI app."""

    async def test_enterprise_header_shows_10000_limit(self, client):
        """X-RateLimit-Limit header must be 10,000 for enterprise users."""
        token = create_access_token(user_id="ent_user", tier="enterprise")
        headers = {"Authorization": f"Bearer {token}"}

        r = await client.get("/get", headers=headers)

        assert r.status_code == 200
        assert r.headers["X-RateLimit-Limit"] == "10000"
        assert int(r.headers["X-RateLimit-Remaining"]) == 9999

    async def test_100_concurrent_enterprise_requests_all_pass(self, client):
        """100 simultaneous enterprise requests must all return 200."""
        token = create_access_token(user_id="ent_concurrent", tier="enterprise")
        headers = {"Authorization": f"Bearer {token}"}

        responses = await asyncio.gather(
            *[client.get("/get", headers=headers) for _ in range(100)]
        )

        status_codes = [r.status_code for r in responses]
        assert all(s == 200 for s in status_codes), (
            f"Expected all 200, got: {set(status_codes)}"
        )

    async def test_enterprise_remaining_decrements_correctly(self, client):
        """Remaining tokens decrement by 1 per request."""
        token = create_access_token(user_id="ent_decrement", tier="enterprise")
        headers = {"Authorization": f"Bearer {token}"}

        r1 = await client.get("/get", headers=headers)
        r2 = await client.get("/get", headers=headers)
        r3 = await client.get("/get", headers=headers)

        assert int(r1.headers["X-RateLimit-Remaining"]) == 9999
        assert int(r2.headers["X-RateLimit-Remaining"]) == 9998
        assert int(r3.headers["X-RateLimit-Remaining"]) == 9997

    async def test_enterprise_exhaustion_returns_429(self, client, test_redis):
        """After exhausting 10,000 tokens, next request returns 429."""
        import redis.asyncio as aioredis

        token = create_access_token(user_id="ent_exhaust", tier="enterprise")
        headers = {"Authorization": f"Bearer {token}"}

        # Manually drain the bucket in Redis (faster than sending 10k requests)
        await test_redis.hset("user:ent_exhaust", mapping={"tokens": "0.0", "last_refill": "9999999999"})

        r = await client.get("/get", headers=headers)

        assert r.status_code == 429
        assert int(r.headers["X-RateLimit-Remaining"]) == 0
        assert int(r.headers["Retry-After"]) >= 1

    async def test_enterprise_limit_higher_than_pro(self, client):
        """Enterprise (10,000) must have higher limit than pro (1,000)."""
        ent_token = create_access_token(user_id="ent_cmp", tier="enterprise")
        pro_token = create_access_token(user_id="pro_cmp", tier="pro")

        r_ent = await client.get("/get", headers={"Authorization": f"Bearer {ent_token}"})
        r_pro = await client.get("/get", headers={"Authorization": f"Bearer {pro_token}"})

        assert int(r_ent.headers["X-RateLimit-Limit"]) == 10_000
        assert int(r_pro.headers["X-RateLimit-Limit"]) == 1_000
        assert (
            int(r_ent.headers["X-RateLimit-Limit"])
            > int(r_pro.headers["X-RateLimit-Limit"])
        )
