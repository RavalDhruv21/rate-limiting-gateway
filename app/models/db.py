"""
SQLAlchemy ORM models — the database tables.

Two tables:
  - RequestLog: one row per request through the gateway.
  - UserQuotaOverride: per-user rate limit overrides.

These models describe the *storage layer*. They are NOT the same as the
API request/response models in app/models/schemas.py. See the comment
at the top of schemas.py for why the split matters.
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.database import Base


class RequestLog(Base):
    """
    One row per request handled by the gateway.

    Written fire-and-forget by the logging middleware, queried by the
    admin /admin/stats endpoints.
    """

    __tablename__ = "request_logs"

    # Synthetic primary key — autoincrementing integer.
    # Cheap to insert, useful for ordering, no business meaning.
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # UUID generated per request by the request_id middleware.
    # Indexed for log lookup ("show me request abc123").
    request_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)

    # Who made the request. Stable ID, NOT email/name (those can change).
    user_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)

    # What they asked for.
    endpoint: Mapped[str] = mapped_column(String(512), nullable=False)
    method: Mapped[str] = mapped_column(String(16), nullable=False)

    # What happened.
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)

    # Was this request rejected by the rate limiter?
    # Indexed because admin queries filter on this constantly.
    rate_limited: Mapped[bool] = mapped_column(Boolean, index=True, nullable=False)

    # Client IP — nullable because test clients and some proxy setups
    # don't expose it.
    ip: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    # When it happened. server_default uses the database's NOW() so the
    # timestamp is consistent across replicas (in v2 with Postgres).
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        index=True,
        nullable=False,
        server_default=func.now(),
    )

    # Composite index for the most common admin query pattern:
    # "show me logs for user_42 ordered by recency."
    # Trying to satisfy that with separate user_id and created_at
    # indexes is much slower than one combined index.
    __table_args__ = (
        Index("ix_logs_user_created", "user_id", "created_at"),
    )

    def __repr__(self) -> str:
        return (
            f"<RequestLog id={self.id} user={self.user_id} "
            f"endpoint={self.endpoint} status={self.status_code}>"
        )


class UserQuotaOverride(Base):
    """
    Admin-set rate limit override for a specific user.

    A row here means "ignore the tier default for this user; use the
    custom_limit value instead." If no row exists, the rate limit
    service falls back to the tier defaults from config.
    """

    __tablename__ = "user_quota_overrides"

    # User ID is the primary key — at most one override per user.
    # Inserting a second override for the same user is an UPSERT
    # (we'll handle that in the admin service).
    user_id: Mapped[str] = mapped_column(String(128), primary_key=True)

    # Requests per minute. We don't store window — the algorithm
    # uses a 60-second window for everyone.
    custom_limit: Mapped[int] = mapped_column(Integer, nullable=False)

    # Audit trail. Why was this override set?
    # Optional but strongly encouraged in admin UI.
    reason: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)

    # When the override was created.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    # When it expires. NULL = permanent.
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    def __repr__(self) -> str:
        return (
            f"<UserQuotaOverride user={self.user_id} "
            f"limit={self.custom_limit}/min>"
        )