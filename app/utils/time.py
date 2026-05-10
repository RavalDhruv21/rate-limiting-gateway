"""
Time helpers.

Rules:
  - Everything is UTC. Naive local time (datetime.now() without a
    timezone) is a bug waiting to happen when servers change time
    zones or cross DST boundaries.
  - Rate limiter and logs use epoch seconds (int) for storage —
    cheap, monotonic, and timezone-independent.
"""

from datetime import UTC, datetime


def utc_now() -> datetime:
    """Current UTC time as a timezone-aware datetime."""
    return datetime.now(UTC)


def epoch_now() -> int:
    """
    Current time as integer epoch seconds (UTC).

    Used by the rate limiter for window math and reset timestamps.
    Integer seconds are precise enough for rate limiting and avoid
    floating-point headaches in windowing logic.
    """
    return int(datetime.now(UTC).timestamp())