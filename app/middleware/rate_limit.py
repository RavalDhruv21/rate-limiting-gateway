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

Also sets X-RateLimit-Policy per the IETF RateLimit Headers draft:
  https://datatracker.ietf.org/doc/draft-ietf-httpapi-ratelimit-headers/
"""

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse, Response

from app.core.errors import RateLimitExceeded, build_error_response
from app.dependencies import get_rate_limiter
from app.middleware.metrics import REQUESTS_ALLOWED, REQUESTS_LIMITED
from app.services.rate_limit_service import _WINDOW_SECONDS, check_rate_limit

PUBLIC_PATH_PREFIXES = (
    "/auth", "/admin", "/health", "/ready",
    "/docs", "/openapi.json", "/redoc", "/metrics",
)


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

        # Get upstream health factor for adaptive rate limiting.
        # Imported locally so test fixtures that monkeypatch dependencies work.
        from app.dependencies import get_redis_client
        from app.services.upstream_health import get_health_factor
        redis = get_redis_client()
        health_factor = await get_health_factor(redis)

        from app.infra import database as db_module
        async with db_module.AsyncSessionLocal() as db:
            result = await check_rate_limit(
                limiter=limiter,
                db=db,
                user_id=user_id,
                tier=tier,  # type: ignore[arg-type]
                health_factor=health_factor,
            )

        request.state.rate_limit_info = result

        # IETF RateLimit-Policy header: "<limit>;w=<window>"
        policy_header = f"{result.limit};w={_WINDOW_SECONDS}"

        if not result.allowed:
            REQUESTS_LIMITED.labels(tier=tier).inc()
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
                    "X-RateLimit-Policy": policy_header,
                },
            )

        REQUESTS_ALLOWED.labels(tier=tier).inc()
        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(result.limit)
        response.headers["X-RateLimit-Remaining"] = str(result.remaining)
        response.headers["X-RateLimit-Retry-After"] = str(result.retry_after)
        response.headers["X-RateLimit-Policy"] = policy_header
        if result.degraded:
            response.headers["X-RateLimit-Degraded"] = "true"
        return response
