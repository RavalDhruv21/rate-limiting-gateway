"""
Authentication service.

Currently thin — just wraps the JWT primitives in core/security.py.
The service layer exists so that future additions (user lookup,
token revocation, refresh tokens) have a home.

Routes call this; this calls core/security.py.
Routes never import core/security.py directly.
"""

from typing import Literal

from app.core.config import settings
from app.core.security import create_access_token

Tier = Literal["free", "pro", "enterprise"]


def issue_token(user_id: str, tier: Tier) -> tuple[str, int]:
    """
    Mint an access token.

    Returns:
        (token_string, expires_in_seconds)
    """
    token = create_access_token(user_id=user_id, tier=tier)
    expires_in = settings.jwt_expire_minutes * 60
    return token, expires_in