# cd C:\dev\rate-limited-gateway
"""
FastAPI application factory.

Wires together:
  - All middleware (registered in REVERSE order — see comment below).
  - All routers (auth, admin, proxy).
  - A single global exception handler that turns GatewayError into
    standardized JSON responses.
  - The shared httpx client via the lifespan context.
  - DB table creation on startup (idempotent).
"""

import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.errors import GatewayError, RateLimitExceeded, build_error_response
from app.infra.database import init_db
from app.middleware.auth import AuthMiddleware
from app.middleware.logging import LoggingMiddleware
from app.middleware.rate_limit import RateLimitMiddleware
from app.middleware.request_id import RequestIDMiddleware
from app.routes import admin, auth, proxy

logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


# ─── Lifespan: startup + shutdown ──────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Run setup on startup, teardown on shutdown."""
    logger.info("Starting gateway. Initializing database...")
    await init_db()

    # Shared httpx client used by the proxy route. One pool, reused
    # across requests. Closed cleanly on shutdown.
    app.state.http_client = httpx.AsyncClient(timeout=30.0)
    logger.info("Gateway ready.")

    yield  # ← app runs here

    logger.info("Shutting down. Closing HTTP client...")
    await app.state.http_client.aclose()


# ─── App factory ───────────────────────────────────────────

def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description="A learning-focused API gateway with per-user rate limiting.",
        lifespan=lifespan,
    )

    # ── Middleware registration order ──
    #
    # IMPORTANT: Starlette/FastAPI applies middleware in REVERSE order
    # of registration. The LAST added middleware runs FIRST on incoming
    # requests (and LAST on outgoing responses).
    #
    # We want this incoming order:
    #   1. LoggingMiddleware  (outermost — captures total latency)
    #   2. RequestIDMiddleware (set ID before anything else logs)
    #   3. AuthMiddleware
    #   4. RateLimitMiddleware (innermost middleware before routes)
    #
    # So we register in REVERSE:
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(AuthMiddleware)
    app.add_middleware(RequestIDMiddleware)
    app.add_middleware(LoggingMiddleware)

    # ── Routers ──
    app.include_router(auth.router)
    app.include_router(admin.router)
    # Proxy is registered LAST so its catch-all doesn't shadow named routes.
    app.include_router(proxy.router)

    # ── Global exception handler ──
    @app.exception_handler(GatewayError)
    async def handle_gateway_error(request: Request, exc: GatewayError) -> JSONResponse:
        request_id = getattr(request.state, "request_id", None)
        body = build_error_response(exc, request_id=request_id)

        headers: dict[str, str] = {}
        # Rate-limit errors include a Retry-After (standard HTTP header).
        if isinstance(exc, RateLimitExceeded):
            retry_after = exc.details.get("retry_after")
            if retry_after is not None:
                headers["Retry-After"] = str(retry_after)

        return JSONResponse(
            status_code=exc.status_code,
            content=body,
            headers=headers,
        )

    # ── Health check ──
    @app.get("/health", tags=["health"])
    async def health() -> dict:
        return {"status": "ok"}

    return app


# Module-level app instance for uvicorn to import.
# Run with: uvicorn app.main:app --reload
app = create_app()