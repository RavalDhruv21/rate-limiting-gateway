"""
JWT encode/decode primitives.

Pure functions — no FastAPI, no HTTP, no storage. They take simple
Python values and return simple Python values. The auth middleware
(app/middleware/auth.py) is what wires these into the request flow.

Claims we put in the payload:
  - sub:  user ID (standard JWT claim for "subject")
  - tier: user's plan ("free" | "pro" | "enterprise") — embedded so the
          rate limiter doesn't need a DB lookup per request
  - iat:  issued-at (epoch seconds)
  - exp:  expires-at (epoch seconds)
"""

from datetime import timedelta
from typing import Any, Literal

from jose import ExpiredSignatureError, JWTError, jwt

from app.core.config import settings
from app.core.errors import InvalidTokenError, TokenExpiredError
from app.utils.time import utc_now

# Tiers are a closed set. Literal types catch typos at static-analysis time.
Tier = Literal["free", "pro", "enterprise"]


def create_access_token(
    user_id: str,
    tier: Tier,
    expires_in: timedelta | None = None,
) -> str:
    """
    Mint a signed JWT for the given user.

    Args:
        user_id: stable unique ID for the user.
        tier:    plan tier; the rate limiter uses this to look up limits.
        expires_in: optional override for testing. Defaults to the
                    value of JWT_EXPIRE_MINUTES from config.

    Returns the encoded JWT string.
    """
    now = utc_now()
    expire_delta = expires_in or timedelta(minutes=settings.jwt_expire_minutes)

    payload: dict[str, Any] = {
        "sub": user_id,
        "tier": tier,
        "iat": int(now.timestamp()),
        "exp": int((now + expire_delta).timestamp()),
    }

    return jwt.encode(
        payload,
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )


def decode_access_token(token: str) -> dict[str, Any]:
    """
    Verify signature, check expiry, return the payload.

    Raises:
        TokenExpiredError: token's exp is in the past.
        InvalidTokenError: signature mismatch, malformed token, missing
                           required claims, or any other validation failure.

    We raise our own typed exceptions (not jose's) so the rest of the app
    never needs to know what JWT library we're using. If we ever swap
    python-jose for PyJWT or authlib, only this file changes.
    """
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
        )
    except ExpiredSignatureError as exc:
        raise TokenExpiredError("Token has expired.") from exc
    except JWTError as exc:
        raise InvalidTokenError("Token is invalid.") from exc

    # Defensive: verify required claims are present. jose's decode
    # checks signature and exp automatically, but doesn't care what's
    # inside the payload. We do.
    if "sub" not in payload or "tier" not in payload:
        raise InvalidTokenError("Token missing required claims.")

    return payload