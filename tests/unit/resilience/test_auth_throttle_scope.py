"""Unit tests for the ``auth`` throttle scope."""

from __future__ import annotations


from src.core.resilience.throttle.scopes import (
    AuthThrottle,
    SCOPES,
    resolve_scope,
)


class _Req:
    """Minimal Starlette-ish request stub for scope identify() calls."""

    def __init__(self, ip: str = "1.2.3.4") -> None:
        self.client = type("c", (), {"host": ip})
        self.headers: dict[str, str] = {}
        self.scope = {"path": "/auth/token/refresh"}
        self.state = type("s", (), {})()


def test_auth_scope_is_registered():
    assert "auth" in SCOPES
    assert isinstance(SCOPES["auth"], AuthThrottle)
    assert resolve_scope("auth") is SCOPES["auth"]


def test_auth_scope_buckets_by_ip_under_auth_namespace():
    bucket, limit, window = AuthThrottle().identify(_Req("9.9.9.9"), "5/min")
    assert bucket.startswith("auth:")
    assert "9.9.9.9" in bucket
    assert (limit, window) == (5, 60)


def test_auth_scope_does_not_collide_with_ip_scope():
    """auth:<ip> must not share a counter with ip:<ip>."""
    ip_bucket, _, _ = SCOPES["ip"].identify(_Req("9.9.9.9"), "5/min")
    auth_bucket, _, _ = SCOPES["auth"].identify(_Req("9.9.9.9"), "5/min")
    assert ip_bucket != auth_bucket
