"""
Pure rate-limiting algorithms.

Storage-agnostic math. Given the previous state and the current time,
return the new state and whether the request is allowed.

Why isolate this from storage?
  - Testable in isolation, with no DB or Redis required.
  - The Lua script inside RedisRateLimiter implements the same math;
    this module is the reference and the unit-test target.

Returns are explicit dataclasses (not tuples) so the call sites stay
self-documenting.
"""

from dataclasses import dataclass


@dataclass
class TokenBucketState:
    """
    Persisted state of a single user's token bucket.

    Stored by the limiter implementation (in a dict for memory,
    in a Redis hash for v2). The algorithm reads it, updates it,
    and writes it back — atomically, in real implementations.
    """

    tokens: float          # current available tokens (can be fractional)
    last_refill: int       # epoch seconds of last refill calculation


@dataclass
class TokenBucketResult:
    """Outcome of a single check_token_bucket call."""

    allowed: bool
    new_state: TokenBucketState
    remaining: int         # whole tokens left (rounded down) for headers
    retry_after: int       # seconds until at least 1 token available


def check_token_bucket(
    state: TokenBucketState | None,
    now: int,
    capacity: int,
    refill_rate_per_sec: float,
) -> TokenBucketResult:
    """
    Run the token bucket algorithm for one request.

    Args:
        state: previous bucket state, or None for a brand-new user
               (treated as "full bucket" so first request is always allowed).
        now: current epoch seconds.
        capacity: max tokens the bucket can hold.
        refill_rate_per_sec: tokens added per second.

    Returns the new state, whether the request is allowed, and headers
    metadata (remaining, retry_after).
    """
    # Brand-new user: bucket starts full. Their first request is allowed.
    if state is None:
        state = TokenBucketState(tokens=float(capacity), last_refill=now)

    # ── 1. Refill phase ──
    # Add tokens for the elapsed time since last check.
    # Negative elapsed (clock skew, time mocking) is treated as 0.
    elapsed = max(0, now - state.last_refill)
    refilled_tokens = state.tokens + (elapsed * refill_rate_per_sec)
    current_tokens = min(refilled_tokens, float(capacity))  # cap at capacity

    # ── 2. Decision phase ──
    if current_tokens >= 1.0:
        # Consume one token. Request allowed.
        new_tokens = current_tokens - 1.0
        return TokenBucketResult(
            allowed=True,
            new_state=TokenBucketState(tokens=new_tokens, last_refill=now),
            remaining=int(new_tokens),
            retry_after=0,
        )

    # Not enough tokens. Compute how long until we'd have at least 1.
    deficit = 1.0 - current_tokens
    wait_seconds = int(deficit / refill_rate_per_sec) + 1  # round up

    return TokenBucketResult(
        allowed=False,
        # Important: we DO update last_refill even on rejection,
        # so the bucket continues to refill in real time.
        new_state=TokenBucketState(tokens=current_tokens, last_refill=now),
        remaining=0,
        retry_after=wait_seconds,
    )