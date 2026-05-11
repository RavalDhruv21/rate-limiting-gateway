"""
Rate limit middleware.

Runs AFTER auth middleware (which set request.state.user_id and .tier).
Skips public paths (no user_id to limit by) and admin paths.

On allow: attaches LimitResult to request.state.rate_limit_info so the
response can carry standard X-RateLimit-* headers.

On deny: constructs and returns a 429 JSONResponse DIRECTLY rather than
raising an exception. This is because FastAPI's @exception_handler only
catches exceptions raised inside route handlers — not inside Starlette
middleware. Raising RateLimitExceeded here would bubble past FastAPI's
handler machinery and result in a generic 500.
"""

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse, Response

from app.core.errors import RateLimitExceeded, build_error_response
from app.dependencies import get_rate_limiter
from app.services.rate_limit_service import check_rate_limit

# Same list as auth — these paths don't have a user_id so we can't
# rate-limit them per-user.
PUBLIC_PATH_PREFIXES = ("/auth", "/admin", "/health", "/docs", "/openapi.json", "/redoc")


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Per-user rate limiting via the configured RateLimiter."""

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        if any(request.url.path.startswith(p) for p in PUBLIC_PATH_PREFIXES):
            return await call_next(request)

        user_id: str = request.state.user_id
        tier: str = request.state.tier

        limiter = get_rate_limiter()

        # Import AsyncSessionLocal DYNAMICALLY (at call time, not module
        # load time). This is so test fixtures that monkeypatch the
        # session factory in app.infra.database take effect — if we did
        # a top-level `from app.infra.database import AsyncSessionLocal`,
        # we'd capture a reference at import time that bypasses the patch.
        from app.infra import database as db_module

        async with db_module.AsyncSessionLocal() as db:
            result = await check_rate_limit(
                limiter=limiter,
                db=db,
                user_id=user_id,
                tier=tier,  # type: ignore[arg-type]
            )

        request.state.rate_limit_info = result

        if not result.allowed:
            exc = RateLimitExceeded(
                f"Rate limit exceeded. Try again in {result.retry_after}s.",
                details={"retry_after": result.retry_after},
            )
            request_id = getattr(request.state, "request_id", None)
            body = build_error_response(exc, request_id=request_id)

            return JSONResponse(
                status_code=exc.status_code,
                content=body,
                headers={
                    "Retry-After": str(result.retry_after),
                    "X-RateLimit-Limit": str(result.limit),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Retry-After": str(result.retry_after),
                },
            )

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(result.limit)
        response.headers["X-RateLimit-Remaining"] = str(result.remaining)
        response.headers["X-RateLimit-Retry-After"] = str(result.retry_after)
        return response