"""
Tests for the pure token bucket algorithm (storage-agnostic math).
"""

from app.infra.rate_limiter.algorithms import (
    TokenBucketState,
    check_token_bucket,
)


class TestTokenBucketAlgorithm:
    """Math-only tests — no storage, no I/O."""

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
