"""
Application-level exceptions and the standardized error response shape.

Design:
  - Business code raises typed exceptions (GatewayError subclasses).
  - A single FastAPI exception handler (registered in main.py later)
    translates them into JSON responses with a consistent shape.

Client-facing shape:
  {
    "error": {
      "code": "RATE_LIMITED",
      "message": "Rate limit exceeded. Try again in 23 seconds.",
      "details": { "retry_after": 23 }   # optional, error-specific
    },
    "request_id": "req_abc123..."
  }
"""

from typing import Any


class GatewayError(Exception):
    """
    Base class for all application errors.

    Subclasses MUST set:
      - status_code: the HTTP status to return
      - code: a stable machine-readable identifier (SHOUTING_SNAKE_CASE)

    `message` is the human-readable string shown to the client.
    `details` is an optional dict for error-specific metadata
    (e.g., retry_after for rate-limit errors).
    """

    status_code: int = 500
    code: str = "INTERNAL_ERROR"

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


# ─── Auth errors (401) ─────────────────────────────────────

class AuthError(GatewayError):
    """Generic auth failure — missing token, bad signature, expired, etc."""
    status_code = 401
    code = "UNAUTHORIZED"


class TokenExpiredError(AuthError):
    """JWT was valid but is past its expiration time."""
    code = "TOKEN_EXPIRED"


class InvalidTokenError(AuthError):
    """JWT is malformed, signature mismatch, or otherwise unparseable."""
    code = "INVALID_TOKEN"


# ─── Authorization errors (403) ────────────────────────────

class ForbiddenError(GatewayError):
    """Valid user, but they're not allowed to do this. Used by admin routes."""
    status_code = 403
    code = "FORBIDDEN"


# ─── Rate limit errors (429) ───────────────────────────────

class RateLimitExceeded(GatewayError):
    """User is over their quota. `details` carries retry_after seconds."""
    status_code = 429
    code = "RATE_LIMITED"


# ─── Upstream errors (502 / 504) ───────────────────────────

class UpstreamError(GatewayError):
    """Upstream backend returned an error or was unreachable."""
    status_code = 502
    code = "UPSTREAM_ERROR"


class UpstreamTimeout(UpstreamError):
    """Upstream backend didn't respond within our timeout."""
    status_code = 504
    code = "UPSTREAM_TIMEOUT"


# ─── Validation / not-found (4xx) ──────────────────────────

class NotFoundError(GatewayError):
    """Resource doesn't exist (e.g., admin querying an unknown user)."""
    status_code = 404
    code = "NOT_FOUND"


def build_error_response(
    exc: GatewayError,
    request_id: str | None = None,
) -> dict[str, Any]:
    """
    Convert a GatewayError into the standardized response body.

    Lives here (not in a FastAPI handler) so it's pure and testable —
    and so the exception handler in main.py can stay a one-liner.
    """
    body: dict[str, Any] = {
        "error": {
            "code": exc.code,
            "message": exc.message,
        }
    }
    if exc.details:
        body["error"]["details"] = exc.details
    if request_id:
        body["request_id"] = request_id
    return body