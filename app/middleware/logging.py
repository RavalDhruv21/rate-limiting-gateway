"""
Request logging middleware.

Wraps the entire request lifecycle to capture:
  - Total latency (start-to-finish, including all other middleware).
  - Final status code (success, auth failure, rate limit denial, etc.).
  - Whether this request was rate-limited.

Logging is fire-and-forget via the logging service — the user never
waits on the DB write.

Lives OUTERMOST in the middleware stack so its timer captures
everything: auth check, rate-limit check, route handler, upstream call.
"""

import time

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from app.dependencies import get_log_store
from app.services.logging_service import schedule_log


class LoggingMiddleware(BaseHTTPMiddleware):
    """Capture request outcome and fire-and-forget to the log store."""

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        start = time.perf_counter()
        # Default to None — populated by AuthMiddleware on success.
        # If auth fails before user_id is set, we log "anonymous".
        # status_code starts at 500 — overwritten when we get a real response.
        status_code = 500

        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            # Runs whether the request succeeded, was rejected, or threw.
            # We *don't* re-raise here — the exception (if any) bubbles
            # past us untouched. We only want to log.

            latency_ms = int((time.perf_counter() - start) * 1000)
            user_id = getattr(request.state, "user_id", "anonymous")
            request_id = getattr(request.state, "request_id", "unknown")
            rate_limit_info = getattr(request.state, "rate_limit_info", None)
            rate_limited = (
                rate_limit_info is not None and not rate_limit_info.allowed
            )

            schedule_log(
                store=get_log_store(),
                request_id=request_id,
                user_id=user_id,
                endpoint=str(request.url.path),
                method=request.method,
                status_code=status_code,
                latency_ms=latency_ms,
                rate_limited=rate_limited,
                ip=request.client.host if request.client else None,
            )