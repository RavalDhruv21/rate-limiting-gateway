"""
PostgreSQL-backed log store.

Backed by the SQLAlchemy async engine and the RequestLog ORM model.
Errors during write() are logged but never raised — logging must not
break user requests.
"""

import logging
from datetime import timedelta

from sqlalchemy import func, select

from app.infra.log_store.base import LogEntry, LogStats, LogStore
from app.models.db import RequestLog
from app.utils.time import utc_now

logger = logging.getLogger(__name__)


def _session_factory():
    """
    Look up AsyncSessionLocal dynamically.

    This indirection is critical for testability — test fixtures
    monkeypatch app.infra.database.AsyncSessionLocal, and a static
    import at the top of this file would capture the unpatched value.
    """
    from app.infra import database as db_module
    return db_module.AsyncSessionLocal


class PostgresLogStore(LogStore):
    """SQLAlchemy-backed log store targeting PostgreSQL."""

    async def write(self, entry: LogEntry) -> None:
        try:
            async with _session_factory()() as session:
                row = RequestLog(
                    request_id=entry.request_id,
                    user_id=entry.user_id,
                    endpoint=entry.endpoint,
                    method=entry.method,
                    status_code=entry.status_code,
                    latency_ms=entry.latency_ms,
                    rate_limited=entry.rate_limited,
                    ip=entry.ip,
                )
                session.add(row)
                await session.commit()
        except Exception as exc:
            logger.warning("Log write failed: %s", exc)

    async def query(
        self,
        user_id: str | None = None,
        limit: int = 100,
    ) -> list[LogEntry]:
        async with _session_factory()() as session:
            stmt = select(RequestLog).order_by(RequestLog.created_at.desc())
            if user_id is not None:
                stmt = stmt.where(RequestLog.user_id == user_id)
            stmt = stmt.limit(limit)

            result = await session.execute(stmt)
            rows = result.scalars().all()

            return [
                LogEntry(
                    request_id=r.request_id,
                    user_id=r.user_id,
                    endpoint=r.endpoint,
                    method=r.method,
                    status_code=r.status_code,
                    latency_ms=r.latency_ms,
                    rate_limited=r.rate_limited,
                    ip=r.ip,
                )
                for r in rows
            ]

    async def stats(
        self,
        user_id: str | None = None,
    ) -> LogStats:
        period_end = utc_now()
        period_start = period_end - timedelta(hours=24)

        async with _session_factory()() as session:
            base_filter = RequestLog.created_at >= period_start
            if user_id is not None:
                base_filter = base_filter & (RequestLog.user_id == user_id)

            total_stmt = select(func.count()).select_from(RequestLog).where(base_filter)
            total = (await session.execute(total_stmt)).scalar_one()

            limited_stmt = (
                select(func.count())
                .select_from(RequestLog)
                .where(base_filter & (RequestLog.rate_limited.is_(True)))
            )
            limited = (await session.execute(limited_stmt)).scalar_one()

            avg_stmt = (
                select(func.avg(RequestLog.latency_ms))
                .select_from(RequestLog)
                .where(base_filter)
            )
            avg_latency = (await session.execute(avg_stmt)).scalar() or 0.0

            return LogStats(
                total_requests=total,
                total_rate_limited=limited,
                avg_latency_ms=float(avg_latency),
                period_start=period_start,
                period_end=period_end,
            )
