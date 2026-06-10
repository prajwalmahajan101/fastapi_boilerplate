"""Unit tests for the hybrid JWT blacklist fail policy (ISSUE-024).

* Healthy cache, hit  → ``LISTED``
* Healthy cache, miss → ``NOT_LISTED``
* Cache outage        → ``UNAVAILABLE`` with WARNING + counter

Callers consume the three-state outcome: the access path treats
``UNAVAILABLE`` as allow (short-lived); the refresh path treats it as
deny (long-lived).
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from src.auth import jwt as jwt_module
from src.auth.jwt import BlacklistOutcome, check_blacklist


class _StubCache:
    """In-memory cache stub that can be flipped to raise on ``get``."""

    def __init__(self, *, hit: bool = False, raises: bool = False) -> None:
        self._hit = hit
        self._raises = raises

    async def get(self, _key: str) -> Any:
        if self._raises:
            raise RuntimeError("redis unreachable")
        return "1" if self._hit else None


class _S:
    jwt_blacklist_cache_alias = "default"


@pytest.fixture(autouse=True)
def _stub_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jwt_module, "get_settings", lambda: _S())


def _patch_cache(monkeypatch: pytest.MonkeyPatch, cache: _StubCache) -> None:
    async def _get_cache(_alias: str) -> _StubCache:
        return cache

    monkeypatch.setattr("resilience_kit.cache.provider.get_cache", _get_cache)


@pytest.mark.asyncio
async def test_listed_when_cache_hits(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_cache(monkeypatch, _StubCache(hit=True))
    assert await check_blacklist("jti-1") is BlacklistOutcome.LISTED


@pytest.mark.asyncio
async def test_not_listed_when_cache_misses(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_cache(monkeypatch, _StubCache(hit=False))
    assert await check_blacklist("jti-1") is BlacklistOutcome.NOT_LISTED


@pytest.mark.asyncio
async def test_unavailable_warns_and_counts_on_cache_outage(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Cache outage → UNAVAILABLE, WARNING with jti/sub/token_type, counter fired."""
    _patch_cache(monkeypatch, _StubCache(raises=True))

    counter_calls: list[dict[str, Any]] = []

    def _capture_counter(event: str, **kw: Any) -> None:
        counter_calls.append({"event": event, **kw})

    monkeypatch.setattr(jwt_module, "record_counter", _capture_counter)

    with caplog.at_level(logging.WARNING, logger="src.auth.jwt"):
        outcome = await check_blacklist("jti-abc", sub="42", token_type="refresh")

    assert outcome is BlacklistOutcome.UNAVAILABLE
    # WARNING includes identifying claims via extra=
    record = next(r for r in caplog.records if "unavailable" in r.message)
    assert record.jti == "jti-abc"
    assert record.sub == "42"
    assert record.token_type == "refresh"
    # Counter event names the token type so refresh spikes stand out.
    assert counter_calls == [
        {"event": "auth_blacklist_unreachable_refresh", "status": "error"},
    ]


@pytest.mark.asyncio
async def test_back_compat_shim_allows_on_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_is_blacklisted`` (the access-path shim) is fail-open."""
    _patch_cache(monkeypatch, _StubCache(raises=True))
    assert await jwt_module._is_blacklisted("jti-x") is False
