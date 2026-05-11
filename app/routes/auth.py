"""
Auth routes — DEV ONLY.

POST /auth/token mints a JWT for any user_id and tier.
In production, real authentication (password, OAuth, etc.) would
issue tokens; the gateway would only verify them, never mint them.

This endpoint exists so you can experiment with the gateway without
building a full identity system.
"""

from fastapi import APIRouter

from app.models.schemas import TokenRequest, TokenResponse
from app.services.auth_service import issue_token

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/token", response_model=TokenResponse)
async def mint_token(body: TokenRequest) -> TokenResponse:
    """
    Mint an access token for a given user_id and tier.

    Dev-only: do not enable in production. A real auth provider should
    own token issuance.
    """
    token, expires_in = issue_token(user_id=body.user_id, tier=body.tier)
    return TokenResponse(access_token=token, expires_in=expires_in)