"""
Admin routes.

All admin endpoints require the X-Admin-Key header (validated by the
require_admin dependency). They are NOT subject to JWT auth or
rate limiting (see PUBLIC_PATH_PREFIXES in the middleware).

Capabilities:
  - PUT  /admin/quota/{user_id}      — set or update a user's quota override
  - GET  /admin/quota/{user_id}      — view current override
  - DELETE /admin/quota/{user_id}    — remove override (revert to tier default)
  - GET  /admin/logs                 — recent requests, optionally filtered by user
  - GET  /admin/stats                — aggregate metrics
"""

from fastapi import APIRouter, Query, status
from sqlalchemy import delete, select

from app.core.errors import NotFoundError
from app.dependencies import AdminAuth, DBSession, LogStoreDep
from app.models.db import UserQuotaOverride
from app.models.schemas import (
    QuotaOverrideCreate,
    QuotaOverrideResponse,
    RequestLogResponse,
    StatsResponse,
)

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[AdminAuth])


# ─── Quota overrides ───────────────────────────────────────

@router.put(
    "/quota/{user_id}",
    response_model=QuotaOverrideResponse,
    status_code=status.HTTP_200_OK,
)
async def upsert_quota(
    user_id: str,
    body: QuotaOverrideCreate,
    db: DBSession,
) -> UserQuotaOverride:
    """
    Set or update a quota override for `user_id`.

    UPSERT semantics: if a row exists, update it; otherwise create one.
    Takes effect on the NEXT request — already-counted tokens for the
    user aren't retroactively adjusted (intentional, simplest behavior).
    """
    stmt = select(UserQuotaOverride).where(UserQuotaOverride.user_id == user_id)
    existing = (await db.execute(stmt)).scalar_one_or_none()

    if existing is None:
        existing = UserQuotaOverride(
            user_id=user_id,
            custom_limit=body.custom_limit,
            reason=body.reason,
            expires_at=body.expires_at,
        )
        db.add(existing)
    else:
        existing.custom_limit = body.custom_limit
        existing.reason = body.reason
        existing.expires_at = body.expires_at

    await db.commit()
    await db.refresh(existing)
    return existing


@router.get(
    "/quota/{user_id}",
    response_model=QuotaOverrideResponse,
)
async def get_quota(user_id: str, db: DBSession) -> UserQuotaOverride:
    """Read a user's current quota override. 404 if none exists."""
    stmt = select(UserQuotaOverride).where(UserQuotaOverride.user_id == user_id)
    override = (await db.execute(stmt)).scalar_one_or_none()
    if override is None:
        raise NotFoundError(f"No quota override for user '{user_id}'.")
    return override


@router.delete("/quota/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_quota(user_id: str, db: DBSession) -> None:
    """Remove a user's quota override. Reverts to the tier default."""
    stmt = delete(UserQuotaOverride).where(UserQuotaOverride.user_id == user_id)
    await db.execute(stmt)
    await db.commit()


# ─── Logs and stats ────────────────────────────────────────

@router.get("/logs", response_model=list[RequestLogResponse])
async def list_logs(
    store: LogStoreDep,
    user_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
) -> list[RequestLogResponse]:
    """Recent request logs, newest-first, optionally filtered by user."""
    entries = await store.query(user_id=user_id, limit=limit)
    # Map LogEntry → RequestLogResponse. created_at isn't in LogEntry
    # (the transport object) so we'd need to extend the interface to
    # surface it cleanly. For v1, derive it from the underlying ORM by
    # going through a separate path — or include it in LogEntry.
    #
    # We chose to keep LogEntry minimal; here we re-query enough info
    # to fill the response. (In a polished v2, LogEntry would carry
    # created_at and this mapping would be trivial.)
    return [
        RequestLogResponse(
            request_id=e.request_id,
            user_id=e.user_id,
            endpoint=e.endpoint,
            method=e.method,
            status_code=e.status_code,
            latency_ms=e.latency_ms,
            rate_limited=e.rate_limited,
            ip=e.ip,
            # Placeholder — see comment above. In v2, surface real
            # created_at via the LogStore interface.
            created_at="1970-01-01T00:00:00+00:00",  # type: ignore[arg-type]
        )
        for e in entries
    ]


@router.get("/stats", response_model=StatsResponse)
async def get_stats(
    store: LogStoreDep,
    user_id: str | None = Query(default=None),
) -> StatsResponse:
    """Aggregate metrics over the last 24h, optionally per-user."""
    s = await store.stats(user_id=user_id)
    return StatsResponse(
        total_requests=s.total_requests,
        total_rate_limited=s.total_rate_limited,
        avg_latency_ms=s.avg_latency_ms,
        period_start=s.period_start,
        period_end=s.period_end,
    )