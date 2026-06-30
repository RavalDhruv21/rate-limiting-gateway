"""
Health and readiness endpoints.

/health  — liveness probe: is the process running? Always 200 if the app is up.
/ready   — readiness probe: are all dependencies reachable?
           Returns 200 if Redis + Postgres are responsive, 503 if either is down.
           Used by Render's zero-downtime deploy: traffic only routes to instances
           that pass /ready.
"""

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict:
    """Liveness probe — returns 200 if the process is alive."""
    return {"status": "ok"}


@router.get("/ready")
async def ready() -> JSONResponse:
    """
    Readiness probe — checks Redis and Postgres connectivity.

    Returns 200 with all checks green, or 503 with details on what failed.
    """
    from app.dependencies import get_redis_client
    from app.infra import database as db_module

    checks: dict[str, str] = {}
    healthy = True

    # ── Redis ──
    try:
        redis = get_redis_client()
        await redis.ping()
        checks["redis"] = "ok"
    except Exception as exc:
        checks["redis"] = f"error: {exc}"
        healthy = False

    # ── Postgres ──
    try:
        async with db_module.AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        checks["postgres"] = "ok"
    except Exception as exc:
        checks["postgres"] = f"error: {exc}"
        healthy = False

    status_code = 200 if healthy else 503
    return JSONResponse(
        status_code=status_code,
        content={"status": "ok" if healthy else "degraded", **checks},
    )
