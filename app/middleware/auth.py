"""
JWT authentication middleware.

Reads the Authorization header, validates the JWT, attaches user info
to request.state. On failure, returns a 401 JSONResponse directly.

Routes that don't need a user (e.g., the token-minting endpoint,
admin endpoints) are explicitly skipped via PUBLIC_PATH_PREFIXES.

Like RateLimitMiddleware, we construct the error response directly
rather than raising — Starlette middleware exceptions don't reach
FastAPI's @exception_handler.
"""

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse, Response

from app.core.errors import AuthError, InvalidTokenError, build_error_response
from app.core.security import decode_access_token

# Paths that bypass JWT auth.
# /auth/* — can't have a token before you've minted one.
# /admin/* — uses X-Admin-Key via Depends, not JWT.
# /health  — health checks should never require auth.
PUBLIC_PATH_PREFIXES = ("/auth", "/admin", "/health", "/docs", "/openapi.json", "/redoc")


def _auth_failure_response(request: Request, exc: AuthError) -> JSONResponse:
    """Build a 401 response without raising — same shape the global handler would."""
    request_id = getattr(request.state, "request_id", None)
    body = build_error_response(exc, request_id=request_id)
    return JSONResponse(
        status_code=exc.status_code,
        content=body,
        headers={"WWW-Authenticate": "Bearer"},
    )


class AuthMiddleware(BaseHTTPMiddleware):
    """Validate JWT and attach user info to request.state."""

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        # Bypass for public paths.
        if any(request.url.path.startswith(p) for p in PUBLIC_PATH_PREFIXES):
            return await call_next(request)

        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return _auth_failure_response(
                request,
                InvalidTokenError("Missing or malformed Authorization header."),
            )

        token = auth_header.removeprefix("Bearer ").strip()

        try:
            payload = decode_access_token(token)
        except AuthError as exc:
            # Covers TokenExpiredError and InvalidTokenError.
            return _auth_failure_response(request, exc)

        # Attach for downstream middleware (rate limiter) and routes.
        request.state.user_id = payload["sub"]
        request.state.tier = payload["tier"]

        return await call_next(request)