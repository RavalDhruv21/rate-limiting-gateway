"""
Abstract interface for request log storage.

The logging middleware calls write(); the admin endpoints call
query() and stats(). Concrete implementations decide how the data
is persisted — currently PostgresLogStore (SQLAlchemy + PostgreSQL).
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


@dataclass
class LogEntry:
    """
    A single request's log record.

    Constructed by the logging middleware with everything we know
    about the request, then handed to the LogStore.write() method.

    This is intentionally NOT the same as the SQLAlchemy RequestLog ORM
    model — it's a transport object. The store implementation converts
    LogEntry to whatever its storage requires (a RequestLog row for
    PostgresLogStore; a JSON document for a hypothetical search backend).
    """

    request_id: str
    user_id: str
    endpoint: str
    method: str
    status_code: int
    latency_ms: int
    rate_limited: bool
    ip: str | None


@dataclass
class LogStats:
    """Aggregate metrics returned by stats()."""

    total_requests: int
    total_rate_limited: int
    avg_latency_ms: float
    period_start: datetime
    period_end: datetime


class LogStore(ABC):
    """Abstract log store. Concrete implementation: PostgresLogStore."""

    @abstractmethod
    async def write(self, entry: LogEntry) -> None:
        """
        Persist a single log entry.

        Implementations should be fast; this is called on every
        request. Failures should be swallowed (logged but not raised),
        because logging must never break the user's request — see the
        fire-and-forget discussion in the architecture doc.
        """
        ...

    @abstractmethod
    async def query(
        self,
        user_id: str | None = None,
        limit: int = 100,
    ) -> list[LogEntry]:
        """
        Return recent log entries, optionally filtered by user.

        Sorted newest-first. `limit` caps the result size to prevent
        admin queries from accidentally returning millions of rows.
        """
        ...

    @abstractmethod
    async def stats(
        self,
        user_id: str | None = None,
    ) -> LogStats:
        """
        Aggregate metrics over recent logs.

        If user_id is given, scope to that user; otherwise global.
        """
        ...