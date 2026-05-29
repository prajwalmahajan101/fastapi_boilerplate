"""Unit tests for the fire-and-forget ``last_used_at`` write (ISSUE-026).

Auth dependency must stay read-only — the UPDATE is submitted to the
``_last_used_queue`` and runs in its own session. Tests assert:

* successful authenticate submits exactly one background task.
* a debounced second authenticate within the window submits nothing.
* the queue's overflow drop fires when at capacity (no auth-side exception).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.auth import api_key as api_key_module
from src.auth.api_key import APIKeyProvider


class _StubUser:
    id = 1
    is_active = True


class _StubAPIKey:
    id = 99
    is_revoked = False
    secret = "rawsecret123"
    user = _StubUser()
    last_used_at = None


class _StubRequest:
    def __init__(self, raw_key: str) -> None:
        self.headers = {"x-api-key": raw_key}
        self.state = type("S", (), {})()


@pytest.fixture
def _patch_lookup(monkeypatch: pytest.MonkeyPatch) -> _StubAPIKey:
    """Replace the DB lookup with a stub returning a fixed APIKey row."""
    api_key = _StubAPIKey()
    monkeypatch.setattr(
        api_key_module,
        "_load_api_key_by_prefix",
        AsyncMock(return_value=api_key),
    )
    return api_key


@pytest.fixture
def _quiet_persist(monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, Any]]:
    """Replace ``_persist_last_used`` so background work does not touch the DB."""
    calls: list[tuple[int, Any]] = []

    async def _stub(api_key_id: int, ts: Any) -> None:
        calls.append((api_key_id, ts))

    monkeypatch.setattr(api_key_module, "_persist_last_used", _stub)
    return calls


@pytest.mark.asyncio
async def test_authenticate_submits_last_used_to_queue(
    monkeypatch: pytest.MonkeyPatch,
    _patch_lookup: _StubAPIKey,
    _quiet_persist: list[tuple[int, Any]],
) -> None:
    """Happy path: one authenticate → one queued background write."""
    monkeypatch.setattr(
        api_key_module, "_debounce_last_used", AsyncMock(return_value=True)
    )

    provider = APIKeyProvider()
    await provider.authenticate(_StubRequest("rawsecret123"), session=object())

    # Either the call was already awaited (fast path), or the background
    # task is still pending — both prove the auth dependency did not block.
    queue_pending = len(api_key_module._last_used_queue._pending)
    assert len(_quiet_persist) + queue_pending == 1
    # Auth itself never wrote on the request-scoped session.
    assert _StubAPIKey.last_used_at is None


@pytest.mark.asyncio
async def test_debounced_authenticate_does_not_submit(
    monkeypatch: pytest.MonkeyPatch,
    _patch_lookup: _StubAPIKey,
    _quiet_persist: list[tuple[int, Any]],
) -> None:
    """Debounce blocks the write inside the configured window."""
    monkeypatch.setattr(
        api_key_module, "_debounce_last_used", AsyncMock(return_value=False)
    )

    provider = APIKeyProvider()
    await provider.authenticate(_StubRequest("rawsecret123"), session=object())

    assert _quiet_persist == []


@pytest.mark.asyncio
async def test_queue_overflow_does_not_break_auth(
    monkeypatch: pytest.MonkeyPatch,
    _patch_lookup: _StubAPIKey,
    _quiet_persist: list[tuple[int, Any]],
) -> None:
    """Capacity drops are logged but the auth response is unaffected."""
    monkeypatch.setattr(
        api_key_module, "_debounce_last_used", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        api_key_module._last_used_queue, "_max_pending", 0
    )

    provider = APIKeyProvider()
    result = await provider.authenticate(
        _StubRequest("rawsecret123"), session=object()
    )
    assert result is not None  # auth still succeeded
    assert _quiet_persist == []  # nothing submitted because dropped
