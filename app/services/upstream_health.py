"""
Upstream health tracking for adaptive rate limiting.

Records each upstream response (success/error) in Redis sliding windows.
When the error rate exceeds a threshold, get_health_factor() returns 0.5,
causing rate_limit_service to halve all effective limits — protecting a
struggling upstream from being flooded.

Redis keys:
  upstream:window:success  — sorted set of success timestamps
  upstream:window:error    — sorted set of error timestamps

Each entry is a float timestamp; ZREMRANGEBYSCORE prunes old entries on
every write, keeping the sets bounded.
"""

import logging
import time

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

_WINDOW_SECONDS = 60
_ERROR_THRESHOLD = 0.20   # 20% error rate → degraded
_DEGRADED_FACTOR = 0.5    # halve limits when degraded

_KEY_SUCCESS = "upstream:window:success"
_KEY_ERROR = "upstream:window:error"


async def record_upstream_result(
    redis_client: aioredis.Redis,
    success: bool,
) -> None:
    """
    Record whether an upstream call succeeded or failed.

    Called from proxy.py after every upstream HTTP request.
    Failures include 5xx responses and connection errors.
    """
    now = time.time()
    key = _KEY_SUCCESS if success else _KEY_ERROR
    cutoff = now - _WINDOW_SECONDS

    try:
        pipe = redis_client.pipeline()
        pipe.zadd(key, {str(now): now})                   # add new entry
        pipe.zremrangebyscore(key, "-inf", cutoff)         # prune old entries
        pipe.expire(key, int(_WINDOW_SECONDS * 3))         # auto-clean TTL
        await pipe.execute()
    except aioredis.RedisError:
        pass  # don't let health tracking block the request


async def get_health_factor(redis_client: aioredis.Redis) -> float:
    """
    Return 1.0 (healthy) or 0.5 (degraded) based on upstream error rate.

    Returns 1.0 (no restriction) if Redis is unavailable or no data yet.
    """
    now = time.time()
    since = now - _WINDOW_SECONDS

    try:
        pipe = redis_client.pipeline()
        pipe.zcount(_KEY_SUCCESS, since, "+inf")
        pipe.zcount(_KEY_ERROR, since, "+inf")
        successes, errors = await pipe.execute()

        total = successes + errors
        if total < 10:  # too few samples — don't penalise
            return 1.0

        error_rate = errors / total
        if error_rate > _ERROR_THRESHOLD:
            logger.warning(
                "Upstream degraded: error_rate=%.1f%% (%d/%d in last %ds)",
                error_rate * 100, errors, total, _WINDOW_SECONDS,
            )
            return _DEGRADED_FACTOR
        return 1.0

    except aioredis.RedisError:
        return 1.0  # fail open


async def get_health_status(redis_client: aioredis.Redis) -> dict:
    """Return a status dict for the /admin/upstream-health endpoint."""
    now = time.time()
    since = now - _WINDOW_SECONDS

    try:
        pipe = redis_client.pipeline()
        pipe.zcount(_KEY_SUCCESS, since, "+inf")
        pipe.zcount(_KEY_ERROR, since, "+inf")
        successes, errors = await pipe.execute()

        total = successes + errors
        error_rate = round(errors / total, 4) if total > 0 else 0.0
        factor = _DEGRADED_FACTOR if (total >= 10 and error_rate > _ERROR_THRESHOLD) else 1.0

        return {
            "status": "degraded" if factor < 1.0 else "healthy",
            "error_rate": error_rate,
            "factor": factor,
            "requests_in_window": total,
            "window_seconds": _WINDOW_SECONDS,
            "threshold": _ERROR_THRESHOLD,
        }
    except aioredis.RedisError as exc:
        return {
            "status": "unknown",
            "error": str(exc),
            "factor": 1.0,
            "window_seconds": _WINDOW_SECONDS,
        }
