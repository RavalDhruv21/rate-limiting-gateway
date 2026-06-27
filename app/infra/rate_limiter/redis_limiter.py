"""
Redis-backed rate limiter.

Implements the RateLimiter abstract interface. The service layer and
middleware depend only on that interface and are unaware of the backend.

Why Redis:
  - Shared state across multiple gateway instances (horizontal scaling).
  - Survives gateway restarts (tokens persist in Redis).
  - Lua scripts execute atomically — no other Redis command interleaves
    between the read and write, making this safe under any concurrency.

Token bucket algorithm:
  Same math as algorithms.py — capacity, refill rate, elapsed time.
  Reimplemented in Lua so the check-and-update is one atomic operation.
"""

import redis.asyncio as aioredis

from app.infra.rate_limiter.base import LimitResult, RateLimiter
from app.utils.time import epoch_now

# ─── Lua script ────────────────────────────────────────────
# This runs atomically inside Redis. The entire token bucket
# check-and-update happens as one indivisible operation.
#
# KEYS[1]   = the rate limit key (e.g., "user:alice")
# ARGV[1]   = capacity (max tokens)
# ARGV[2]   = refill_rate (tokens per second, as float)
# ARGV[3]   = now (current epoch seconds)
#
# Returns: {allowed, remaining, retry_after}
#   allowed:     1 if request is allowed, 0 if denied
#   remaining:   integer tokens left after this request
#   retry_after: seconds until 1 token is available (0 if allowed)

_TOKEN_BUCKET_LUA = """
local key          = KEYS[1]
local capacity     = tonumber(ARGV[1])
local refill_rate  = tonumber(ARGV[2])
local now          = tonumber(ARGV[3])

-- Read current state from Redis hash.
-- HGET returns nil if the key doesn't exist (new user).
local tokens_str      = redis.call('HGET', key, 'tokens')
local last_refill_str = redis.call('HGET', key, 'last_refill')

local tokens
local last_refill

if tokens_str == false then
    -- Brand-new user: start with a full bucket.
    tokens      = capacity
    last_refill = now
else
    tokens      = tonumber(tokens_str)
    last_refill = tonumber(last_refill_str)
end

-- Refill phase: add tokens for elapsed time.
local elapsed  = math.max(0, now - last_refill)
local refilled = tokens + (elapsed * refill_rate)
local current  = math.min(refilled, capacity)  -- cap at capacity

local allowed
local remaining
local retry_after

if current >= 1.0 then
    -- Allow: consume one token.
    allowed      = 1
    remaining    = math.floor(current - 1.0)
    retry_after  = 0

    -- Persist new state.
    redis.call('HSET', key, 'tokens', tostring(current - 1.0))
    redis.call('HSET', key, 'last_refill', tostring(now))
else
    -- Deny: not enough tokens.
    allowed      = 0
    remaining    = 0
    local deficit = 1.0 - current
    retry_after  = math.ceil(deficit / refill_rate)

    -- Still update last_refill so the bucket keeps refilling.
    redis.call('HSET', key, 'tokens', tostring(current))
    redis.call('HSET', key, 'last_refill', tostring(now))
end

-- Set TTL: expire the key after capacity/refill_rate seconds of
-- inactivity (the time it takes to refill a fully empty bucket).
-- This prevents Redis from filling up with stale keys for users
-- who never return.
local ttl = math.ceil(capacity / refill_rate) + 60
redis.call('EXPIRE', key, ttl)

return {allowed, remaining, retry_after}
"""


class RedisRateLimiter(RateLimiter):
    """
    Token-bucket rate limiter backed by Redis.

    The service layer and middleware call the abstract check() method
    and receive a LimitResult — they never depend on this class directly.
    Thread-safe across multiple processes and machines via Lua atomicity.
    """

    def __init__(self, redis_client: aioredis.Redis) -> None:
        self._redis = redis_client
        # Pre-register the Lua script with Redis for efficiency.
        # Redis compiles and caches it; we call it by SHA hash.
        self._script = self._redis.register_script(_TOKEN_BUCKET_LUA)

    async def check(
        self,
        key: str,
        limit: int,
        window_seconds: int,
    ) -> LimitResult:
        """
        Atomically check and update the token bucket for `key`.

        The Lua script runs as a single Redis transaction — no other
        Redis command can interleave, making this safe under any
        level of concurrency, across any number of gateway instances.
        """
        capacity = limit
        refill_rate = limit / window_seconds
        now = epoch_now()

        # Execute the Lua script atomically.
        # Returns [allowed, remaining, retry_after] as integers.
        result = await self._script(
            keys=[key],
            args=[capacity, refill_rate, now],
        )

        allowed, remaining, retry_after = result

        return LimitResult(
            allowed=bool(allowed),
            limit=limit,
            remaining=int(remaining),
            retry_after=int(retry_after),
        )