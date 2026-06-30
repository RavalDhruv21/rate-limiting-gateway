"""
Security middleware: CORS, trusted-host, and response security headers.

Registration order in main.py (added last = runs first on ingress):
  add_middleware(SecurityHeadersMiddleware)   ← outermost: sets headers on every response
  add_middleware(CORSMiddleware, ...)
  add_middleware(TrustedHostMiddleware, ...)
  ... existing middleware ...
"""

from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.core.config import settings


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Inject security-related response headers on every response."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        if settings.app_env == "production":
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains"
            )
        return response


def add_security_middleware(app) -> None:
    """
    Register all security middleware onto the FastAPI app.

    Call this in main.py's create_app() AFTER all other middleware
    so these wrappers run outermost (first on request, last on response).
    """
    # TrustedHostMiddleware rejects requests whose Host header doesn't
    # match. Use ["*"] in development; set ALLOWED_HOSTS in production.
    if settings.allowed_hosts != ["*"]:
        app.add_middleware(
            TrustedHostMiddleware,
            allowed_hosts=settings.allowed_hosts,
        )

    # CORSMiddleware handles preflight OPTIONS and sets Access-Control-*
    # headers. Only wired if ALLOWED_ORIGINS is configured.
    if settings.allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.allowed_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
            max_age=3600,
        )

    # SecurityHeadersMiddleware is always active.
    app.add_middleware(SecurityHeadersMiddleware)
