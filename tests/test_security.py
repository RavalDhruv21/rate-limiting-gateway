"""
Unit tests for JWT encode/decode primitives (app/core/security.py).

Pure logic — no HTTP, no DB. These are the fastest tests in the suite
and catch the most basic kind of bug (the JWT round-trip is broken).
"""

from datetime import timedelta

import pytest

from app.core.errors import InvalidTokenError, TokenExpiredError
from app.core.security import create_access_token, decode_access_token


def test_create_and_decode_round_trip():
    """A freshly-minted token decodes to the same user_id and tier."""
    token = create_access_token("user_42", "pro")
    payload = decode_access_token(token)

    assert payload["sub"] == "user_42"
    assert payload["tier"] == "pro"
    assert "iat" in payload
    assert "exp" in payload


def test_expired_token_raises_typed_exception():
    """A token with an expiry in the past raises TokenExpiredError."""
    # Mint a token that expired one second ago.
    token = create_access_token("user_42", "free", expires_in=timedelta(seconds=-1))

    with pytest.raises(TokenExpiredError):
        decode_access_token(token)


def test_tampered_signature_raises_typed_exception():
    """Modifying the token body invalidates the signature."""
    token = create_access_token("user_42", "free")
    # Flip a character in the payload portion (middle of the JWT).
    parts = token.split(".")
    parts[1] = "A" + parts[1][1:]
    tampered = ".".join(parts)

    with pytest.raises(InvalidTokenError):
        decode_access_token(tampered)


def test_garbage_token_raises_invalid():
    """Random non-JWT input raises InvalidTokenError, not some library error."""
    with pytest.raises(InvalidTokenError):
        decode_access_token("not-a-jwt")


def test_different_tier_values_round_trip():
    """All three valid tiers survive the round-trip intact."""
    for tier in ("free", "pro", "enterprise"):
        token = create_access_token("u", tier)  # type: ignore[arg-type]
        assert decode_access_token(token)["tier"] == tier