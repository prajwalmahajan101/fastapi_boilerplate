"""Unit tests for the JWT provider — mint / verify / blacklist."""

from __future__ import annotations

import time

import pytest

from src.auth import jwt as jwt_module
from src.core.exceptions.auth import (
    TokenExpiredError,
    TokenInvalidError,
)


class _S:
    """Stub settings — overridden per test via the autouse fixture."""

    jwt_signing_key = type("S", (), {"get_secret_value": lambda self: "k" * 64})()
    jwt_algorithm = "HS256"
    jwt_issuer = None
    jwt_audience = None
    jwt_access_ttl_seconds = 60
    jwt_refresh_ttl_seconds = 600
    jwt_blacklist_cache_alias = "default"


@pytest.fixture(autouse=True)
def _stub_settings(monkeypatch):
    monkeypatch.setattr(jwt_module, "get_settings", lambda: _S())


def test_mint_and_decode_access_token_roundtrip():
    token, claims = jwt_module.mint_access_token(42)
    decoded = jwt_module.decode_token(token, expected_type="access")
    assert decoded["sub"] == "42"
    assert decoded["type"] == "access"
    assert decoded["jti"] == claims["jti"]


def test_mint_token_pair_carries_both():
    pair = jwt_module.mint_token_pair(7)
    assert {"access_token", "refresh_token", "token_type", "expires_in"} <= pair.keys()
    assert pair["token_type"] == "bearer"
    # both must decode to distinct types
    a = jwt_module.decode_token(pair["access_token"], expected_type="access")
    r = jwt_module.decode_token(pair["refresh_token"], expected_type="refresh")
    assert a["type"] == "access"
    assert r["type"] == "refresh"
    assert a["jti"] != r["jti"]


def test_decode_rejects_expired_token(monkeypatch):
    # mint with tiny TTL via direct claim manipulation
    import jwt as pyjwt  # noqa: PLC0415

    expired_payload = {
        "sub": "1",
        "type": "access",
        "jti": "x",
        "iat": int(time.time()) - 120,
        "exp": int(time.time()) - 60,
    }
    token = pyjwt.encode(expired_payload, "k" * 64, algorithm="HS256")
    with pytest.raises(TokenExpiredError):
        jwt_module.decode_token(token, expected_type="access")


def test_decode_rejects_tampered_signature():
    token, _ = jwt_module.mint_access_token(1)
    tampered = token[:-4] + ("AAAA" if token[-4:] != "AAAA" else "BBBB")
    with pytest.raises(TokenInvalidError):
        jwt_module.decode_token(tampered, expected_type="access")


def test_decode_rejects_wrong_token_type():
    token, _ = jwt_module.mint_access_token(1)
    with pytest.raises(TokenInvalidError):
        jwt_module.decode_token(token, expected_type="refresh")
