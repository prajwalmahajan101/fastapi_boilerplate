"""Unit tests for the auth provider registry."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from src.auth import registry
from src.auth.base import AuthResult


@dataclass
class _StubProvider:
    name: str
    result: AuthResult | None = None

    async def authenticate(self, request, session):  # noqa: ANN001
        return self.result


@pytest.fixture(autouse=True)
def _isolate_registry(monkeypatch):
    """Snapshot the registry around each test so no state leaks."""
    original = registry._REGISTRY.copy()
    yield
    registry._REGISTRY.clear()
    registry._REGISTRY.update(original)
    registry._WARNED_UNKNOWN.clear()


def _user_stub(uid: int = 1):
    class _U:
        id = uid
        is_active = True
        roles: list = []

    return _U()


def test_register_and_unregister():
    p = _StubProvider("stub")
    registry.register(p)
    assert "stub" in registry.registered_names()
    registry.unregister("stub")
    assert "stub" not in registry.registered_names()


def test_enabled_providers_honours_settings_order(monkeypatch):
    a = _StubProvider("a")
    b = _StubProvider("b")
    registry.register(a)
    registry.register(b)

    class _S:
        auth_enabled_providers = ["b", "a"]

    monkeypatch.setattr(registry, "get_settings", lambda: _S())
    names = [p.name for p in registry.enabled_providers()]
    assert names == ["b", "a"]


def test_enabled_providers_skips_unknown(monkeypatch, caplog):
    a = _StubProvider("a")
    registry.register(a)

    class _S:
        auth_enabled_providers = ["a", "ghost"]

    monkeypatch.setattr(registry, "get_settings", lambda: _S())
    with caplog.at_level("WARNING"):
        names = [p.name for p in registry.enabled_providers()]
    assert names == ["a"]
    assert any("ghost" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_resolve_first_match_wins(monkeypatch):
    user = _user_stub()
    a = _StubProvider("a", result=None)
    b = _StubProvider("b", result=AuthResult(user=user, provider="b"))
    c = _StubProvider("c", result=AuthResult(user=_user_stub(2), provider="c"))
    for p in (a, b, c):
        registry.register(p)

    class _S:
        auth_enabled_providers = ["a", "b", "c"]

    monkeypatch.setattr(registry, "get_settings", lambda: _S())

    class _Req:
        class _State:
            pass

        state = _State()
        headers: dict = {}

    result = await registry._resolve(_Req(), session=None)
    assert result is not None and result.provider == "b"
    assert _Req.state is not None
