"""
Lightweight async circuit breaker for upstream HTTP calls.

States:
  CLOSED   — normal operation; failures are counted.
  OPEN     — fast-fail with CircuitOpenError; no calls forwarded.
  HALF_OPEN — one probe request allowed; success → CLOSED, failure → OPEN.

Usage:
    breaker = CircuitBreaker(failure_threshold=5, recovery_timeout=60)

    try:
        result = await breaker.call(my_async_func, arg1, arg2)
    except CircuitOpenError:
        # circuit is open — return 503 immediately
    except Exception:
        # real error from the function
"""

import asyncio
import logging
import time
from enum import Enum

logger = logging.getLogger(__name__)


class _State(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(Exception):
    """Raised when a call is attempted while the circuit is open."""


class CircuitBreaker:
    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        name: str = "upstream",
    ) -> None:
        self._threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._name = name
        self._state = _State.CLOSED
        self._failures = 0
        self._opened_at: float = 0.0
        self._lock = asyncio.Lock()

    @property
    def state(self) -> str:
        return self._state.value

    async def call(self, func, *args, **kwargs):
        async with self._lock:
            if self._state == _State.OPEN:
                if time.monotonic() - self._opened_at >= self._recovery_timeout:
                    self._state = _State.HALF_OPEN
                    logger.warning(
                        "Circuit %s → half_open (probing after %.0fs)",
                        self._name, self._recovery_timeout,
                    )
                else:
                    raise CircuitOpenError(
                        f"Circuit '{self._name}' is open — upstream unavailable."
                    )

        try:
            result = await func(*args, **kwargs)
        except Exception:
            async with self._lock:
                self._failures += 1
                if self._state == _State.HALF_OPEN or self._failures >= self._threshold:
                    self._state = _State.OPEN
                    self._opened_at = time.monotonic()
                    logger.warning(
                        "Circuit %s → open (failures=%d)",
                        self._name, self._failures,
                    )
            raise

        # Success path
        async with self._lock:
            if self._state == _State.HALF_OPEN:
                self._state = _State.CLOSED
                self._failures = 0
                logger.info("Circuit %s → closed (recovered)", self._name)
            elif self._state == _State.CLOSED:
                self._failures = 0

        return result


# Module-level singleton shared across all requests.
upstream_breaker = CircuitBreaker(
    failure_threshold=5,
    recovery_timeout=60.0,
    name="upstream",
)
