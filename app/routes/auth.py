"""
Auth routes — DEV ONLY.

POST /auth/token mints a JWT for any user_id and tier.
In production, real authentication (password, OAuth, etc.) would
issue tokens; the gateway would only verify them, never mint them.

In production (APP_ENV=production) this endpoint requires X-Admin-Key
so it can still be used for manual testing without being publicly accessible.
"""

from fastapi import APIRouter

from app.core.config import settings
from app.models.schemas import TokenRequest, TokenResponse
from app.services.auth_service import issue_token

router = APIRouter(prefix="/auth", tags=["auth"])

# Build the dependency list dynamically based on environment.
# In production, require admin key so this endpoint isn't open to the public.
_dependencies = []
if settings.app_env == "production":
    from app.dependencies import AdminAuth
    _dependencies = [AdminAuth]


@router.post("/token", response_model=TokenResponse, dependencies=_dependencies)
async def mint_token(body: TokenRequest) -> TokenResponse:
    """
    Mint an access token for a given user_id and tier.

    Dev-only: requires X-Admin-Key in production.
    """
    token, expires_in = issue_token(user_id=body.user_id, tier=body.tier)
    return TokenResponse(access_token=token, expires_in=expires_in)
