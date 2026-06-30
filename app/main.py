"""
FastAPI application factory.

Wires together:
  - All middleware (registered in REVERSE order — see comment below).
  - All routers (health, auth, admin, proxy).
  - A single global exception handler for standardized JSON error responses.
  - The shared httpx client and Prometheus instrumentation via lifespan.
  - Structured JSON logging configured before anything else.
  - DB table creation on startup (idempotent).

Route registration order matters: the proxy router uses a {path:path}
catch-all, so specific routes MUST be registered before it or they
will be shadowed.
"""

import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.errors import GatewayError, RateLimitExceeded, build_error_response
from app.core.logging import setup_logging
from app.infra.database import init_db
from app.middleware.auth import AuthMiddleware
from app.middleware.logging import LoggingMiddleware
from app.middleware.metrics import setup_metrics
from app.middleware.rate_limit import RateLimitMiddleware
from app.middleware.request_id import RequestIDMiddleware
from app.middleware.security import add_security_middleware
from app.routes import admin, auth, proxy
from app.routes import health as health_routes

# Configure structured JSON logging before anything else logs.
setup_logging(settings.log_level)
logger = logging.getLogger(__name__)


# ─── Lifespan: startup + shutdown ──────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Run setup on startup, teardown on shutdown."""
    logger.info("Starting gateway", extra={"event": "startup"})
    await init_db()

    app.state.http_client = httpx.AsyncClient(timeout=30.0)
    logger.info("Gateway ready", extra={"event": "ready", "env": settings.app_env})

    yield

    logger.info("Shutting down", extra={"event": "shutdown"})
    await app.state.http_client.aclose()

    from app.dependencies import _redis_client
    await _redis_client.aclose()  # type: ignore[union-attr]
    logger.info("Shutdown complete", extra={"event": "shutdown_complete"})


# ─── App factory ───────────────────────────────────────────

def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description="A production-shaped API gateway with per-user rate limiting.",
        lifespan=lifespan,
        swagger_ui_parameters={"persistAuthorization": True},
    )

    # ── Custom OpenAPI schema ──
    def custom_openapi():
        if app.openapi_schema:
            return app.openapi_schema
        openapi_schema = get_openapi(
            title=settings.app_name,
            version="0.1.0",
            description="A production-shaped API gateway with per-user rate limiting.",
            routes=app.routes,
        )
        openapi_schema["components"]["securitySchemes"] = {
            "bearerAuth": {
                "type": "http",
                "scheme": "bearer",
                "bearerFormat": "JWT",
            }
        }
        openapi_schema["security"] = [{"bearerAuth": []}]
        return openapi_schema

    app.openapi = custom_openapi  # type: ignore[method-assign]

    # ── Middleware registration ──
    #
    # Starlette applies middleware in REVERSE order of registration.
    # The LAST added middleware runs FIRST on incoming requests.
    #
    # Desired request order:
    #   1. SecurityHeadersMiddleware  (outermost — sets headers on all responses)
    #   2. CORSMiddleware             (handles preflight)
    #   3. TrustedHostMiddleware      (rejects bad Host headers)
    #   4. LoggingMiddleware          (times total round-trip)
    #   5. RequestIDMiddleware        (assigns request ID)
    #   6. AuthMiddleware             (validates JWT)
    #   7. RateLimitMiddleware        (innermost — checks quotas)
    #
    # Registration order (reverse of above):
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(AuthMiddleware)
    app.add_middleware(RequestIDMiddleware)
    app.add_middleware(LoggingMiddleware)
    # Security middleware (CORS, TrustedHost, SecurityHeaders) registered last
    # so they run outermost. add_security_middleware handles conditional logic.
    add_security_middleware(app)

    # ── Global exception handler ──
    @app.exception_handler(GatewayError)
    async def handle_gateway_error(request: Request, exc: GatewayError) -> JSONResponse:
        request_id = getattr(request.state, "request_id", None)
        body = build_error_response(exc, request_id=request_id)
        headers: dict[str, str] = {}
        if isinstance(exc, RateLimitExceeded):
            retry_after = exc.details.get("retry_after")
            if retry_after is not None:
                headers["Retry-After"] = str(retry_after)
        return JSONResponse(
            status_code=exc.status_code,
            content=body,
            headers=headers,
        )

    # ── Routers ──
    # health and specific routers BEFORE the catch-all proxy.
    app.include_router(health_routes.router)
    app.include_router(auth.router)
    app.include_router(admin.router)
    app.include_router(proxy.router)

    # ── Prometheus metrics ──
    # Must be called AFTER routes are registered so the instrumentator
    # can see all route handlers.
    setup_metrics(app)

    return app


# Module-level app instance for uvicorn / gunicorn to import.
app = create_app()
