"""
Request logging service — fire-and-forget.

Architectural rule from Step 1: a slow or failing logging backend must
NEVER slow down or break user requests. We achieve this by:

  1. Constructing the log entry synchronously (cheap — just packaging
     data the middleware already has).
  2. Scheduling the actual DB write as a background task via
     asyncio.create_task. The middleware doesn't await it.

Trade-off: under heavy stress, log entries can be lost (process crashes,
backpressure). That's acceptable — we picked stable latency over lossless
logging. In a real system, you'd add a buffer queue (Kafka / Redis stream)
between gateway and DB to reduce loss without re-introducing blocking.
"""

import asyncio
import logging

from app.infra.log_store.base import LogEntry, LogStore

logger = logging.getLogger(__name__)


def schedule_log(
    store: LogStore,
    *,
    request_id: str,
    user_id: str,
    endpoint: str,
    method: str,
    status_code: int,
    latency_ms: int,
    rate_limited: bool,
    ip: str | None,
) -> None:
    """
    Schedule a log write without blocking the caller.

    Returns immediately. The actual DB write happens in a background
    task. Errors are swallowed (logged but not raised).
    """
    entry = LogEntry(
        request_id=request_id,
        user_id=user_id,
        endpoint=endpoint,
        method=method,
        status_code=status_code,
        latency_ms=latency_ms,
        rate_limited=rate_limited,
        ip=ip,
    )

    # Wrap write in a closure that swallows exceptions, so a failing
    # background task doesn't generate an "unhandled exception in task"
    # warning in the logs on every error.
    async def _safe_write() -> None:
        try:
            await store.write(entry)
        except Exception as exc:
            logger.warning("Background log write failed: %s", exc)

    # asyncio.create_task schedules the coroutine on the running event
    # loop. Returns a Task object we deliberately don't keep — the
    # event loop holds a reference until the task completes.
    asyncio.create_task(_safe_write())