"""
SQLite log store — v1 implementation.

Backed by the SQLAlchemy async engine and the RequestLog ORM model.
For v2, swapping to Postgres requires no changes here — only the
DATABASE_URL in config changes (asyncpg driver instead of aiosqlite).

Errors during write() are logged but never raised. The contract from
LogStore says logging must not break user requests; we honor that
even at the storage layer.
"""

import logging
from datetime import datetime, timedelta

from sqlalchemy import func, select

from app.infra.database import AsyncSessionLocal
from app.infra.log_store.base import LogEntry, LogStats, LogStore
from app.models.db import RequestLog
from app.utils.time import utc_now

# Module-level logger. Distinct from the request-log table — this is
# Python's standard logging, used for our own diagnostics ("DB write
# failed") that shouldn't go into the user-facing request log.
logger = logging.getLogger(__name__)


class SqliteLogStore(LogStore):
    """SQLAlchemy-backed log store. Works for SQLite and Postgres."""

    async def write(self, entry: LogEntry) -> None:
        """
        Insert one row. Errors are logged but never raised — the contract.
        """
        try:
            async with AsyncSessionLocal() as session:
                row = RequestLog(
                    request_id=entry.request_id,
                    user_id=entry.user_id,
                    endpoint=entry.endpoint,
                    method=entry.method,
                    status_code=entry.status_code,
                    latency_ms=entry.latency_ms,
                    rate_limited=entry.rate_limited,
                    ip=entry.ip,
                    # created_at populated by server_default in the ORM
                )
                session.add(row)
                await session.commit()
        except Exception as exc:
            # Don't let a logging failure bubble up. Log it for our own
            # debugging, then drop the entry.
            logger.warning("Log write failed: %s", exc)

    async def query(
        self,
        user_id: str | None = None,
        limit: int = 100,
    ) -> list[LogEntry]:
        """Return recent entries, newest-first, optionally scoped to user."""
        async with AsyncSessionLocal() as session:
            stmt = select(RequestLog).order_by(RequestLog.created_at.desc())
            if user_id is not None:
                stmt = stmt.where(RequestLog.user_id == user_id)
            stmt = stmt.limit(limit)

            result = await session.execute(stmt)
            rows = result.scalars().all()

            # Convert ORM rows to LogEntry transport objects.
            # The interface returns LogEntry; callers shouldn't need to
            # know we're using SQLAlchemy underneath.
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
        """
        Compute aggregate stats over the last 24 hours.

        Hard-coded 24h window for simplicity; a fancier version would
        accept a period parameter.
        """
        period_end = utc_now()
        period_start = period_end - timedelta(hours=24)

        async with AsyncSessionLocal() as session:
            base_filter = RequestLog.created_at >= period_start
            if user_id is not None:
                base_filter = base_filter & (RequestLog.user_id == user_id)

            # Three aggregate queries in one round-trip would require
            # combining them into a single SELECT. For clarity (and
            # because v1 is SQLite, not high-throughput), we run three
            # simple queries.
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