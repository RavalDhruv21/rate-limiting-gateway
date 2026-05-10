"""
Pydantic schemas — the API contract.

These define what HTTP requests and responses look like over the wire.
They are deliberately separate from the SQLAlchemy models in
app/models/db.py.

Why the separation:
  - DB models describe storage. API schemas describe the API contract.
    Storage can change (rename columns, add internal fields, denormalize)
    without breaking API consumers.
  - Schemas are the security boundary: any field NOT in a Response
    schema can never accidentally leak to clients.
  - Schemas validate input at the API edge. Bad input never reaches
    business logic.

Naming convention:
  - ...Create:    inbound body for POST
  - ...Update:    inbound body for PUT/PATCH
  - ...Response:  outbound body for GET/POST responses
"""

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

# Tier values are a closed set.
# Mirrors the Tier type in core/security.py — kept in sync by hand.
# (For a bigger project, we'd put this in one place and import it.)
Tier = Literal["free", "pro", "enterprise"]


# ─── Auth schemas ──────────────────────────────────────────

class TokenRequest(BaseModel):
    """Inbound body for POST /auth/token (dev-only token minting)."""

    user_id: str = Field(..., min_length=1, max_length=128)
    tier: Tier = "free"


class TokenResponse(BaseModel):
    """Outbound body for POST /auth/token."""

    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int  # seconds until expiry


# ─── Quota override schemas ────────────────────────────────

class QuotaOverrideCreate(BaseModel):
    """
    Inbound body for PUT /admin/quota/{user_id}.

    user_id comes from the URL path, not the body — admin endpoints
    are designed REST-style.
    """

    custom_limit: int = Field(..., gt=0, le=1_000_000)
    reason: Optional[str] = Field(None, max_length=512)
    expires_at: Optional[datetime] = None


class QuotaOverrideResponse(BaseModel):
    """Outbound body for GET/PUT /admin/quota/{user_id}."""

    user_id: str
    custom_limit: int
    reason: Optional[str]
    created_at: datetime
    expires_at: Optional[datetime]

    # `from_attributes=True` lets Pydantic build this model directly
    # from a SQLAlchemy ORM object via QuotaOverrideResponse.model_validate(orm_obj).
    # That's the "convert DB model → API schema" bridge.
    model_config = ConfigDict(from_attributes=True)


# ─── Log query schemas ─────────────────────────────────────

class RequestLogResponse(BaseModel):
    """
    Outbound body for GET /admin/logs.

    Note what's NOT here vs. RequestLog ORM model:
      - id is omitted (internal autoincrement, not part of API contract).
      - We can add computed fields like `latency_seconds` later
        without touching the database schema.
    """

    request_id: str
    user_id: str
    endpoint: str
    method: str
    status_code: int
    latency_ms: int
    rate_limited: bool
    ip: Optional[str]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class StatsResponse(BaseModel):
    """Outbound body for GET /admin/stats. Aggregate over all users."""

    total_requests: int
    total_rate_limited: int
    avg_latency_ms: float
    period_start: datetime
    period_end: datetime


# ─── Standardized error schema ─────────────────────────────

class ErrorBody(BaseModel):
    """Inner 'error' object inside the standardized error response."""

    code: str
    message: str
    details: Optional[dict] = None


class ErrorResponse(BaseModel):
    """
    Standardized error response body.

    Every error from the gateway — 401, 403, 404, 429, 500, etc. —
    returns this shape. Defined in core/errors.py via build_error_response();
    duplicated here as a Pydantic schema so OpenAPI/Swagger UI knows
    what error responses look like.
    """

    error: ErrorBody
    request_id: Optional[str] = None