"""Smoke + SSRF-parity tests for ``AsyncAPIClient.download_bytes``.

Guards two issues at once:

* ISSUE-022 — ``download_bytes`` raised NameError on the default
  ``check_ssrf=True`` path because ``assert_public_url`` was never
  imported.
* ISSUE-023 — the same path skipped ``resolve_and_validate`` +
  ``pinned_dns``, leaving the DNS-rebinding TOCTOU that commit
  ``20a4150`` closed for ``_request`` wide open in the download path.

The tests stub the aiohttp session, drive ``download_bytes`` end-to-
end, and assert that ``pinned_dns`` is set during dispatch then reset
on exit (both happy and failure paths).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.utils import ssrf
from src.core.utils.http_client._client import AsyncAPIClient


class _StubResponse:
    """Minimal aiohttp response stub returning a fixed body."""

    status = 200
    content_length = 11
    headers = {"Content-Type": "application/octet-stream"}

    async def __aenter__(self) -> "_StubResponse":
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        return None

    @property
    def content(self) -> "_StubContent":
        return _StubContent()


class _StubContent:
    """Yields one chunk so ``iter_chunked`` terminates immediately."""

    async def iter_chunked(self, _n: int):
        yield b"hello-world"


def _stub_session(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Patch ``SessionManager.get_session`` to return a stub aiohttp session."""
    session = MagicMock()
    session.get = MagicMock(return_value=_StubResponse())
    monkeypatch.setattr(
        "src.core.utils.http_client._client.SessionManager.get_session",
        AsyncMock(return_value=session),
    )
    return session


@pytest.fixture(autouse=True)
def _reset_pin():
    """Each test starts with an empty pin contextvar."""
    token = ssrf.pinned_dns.set(None)
    yield
    ssrf.pinned_dns.reset(token)


@pytest.mark.asyncio
async def test_download_bytes_pins_dns_then_resets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The SSRF guard resolves, pins, fetches, and unwinds the pin on exit."""
    session = _stub_session(monkeypatch)

    captured: dict[str, Any] = {}

    def _resolve(url: str) -> set[str]:
        captured["resolved_url"] = url
        return {"203.0.113.7"}

    monkeypatch.setattr("src.core.utils.ssrf.resolve_and_validate", _resolve)
    monkeypatch.setattr(
        "src.core.utils.ssrf.assert_allowed_url",
        lambda _u: captured.setdefault("allow_called", True),
    )

    # Confirm the pin is set during the fetch.
    def _during_fetch(*_a: Any, **_kw: Any) -> _StubResponse:
        captured["pin_during_fetch"] = ssrf.pinned_dns.get()
        return _StubResponse()

    session.get.side_effect = _during_fetch

    body, ctype = await AsyncAPIClient.download_bytes(
        "https://example.com/x", max_size=1024
    )

    assert body == b"hello-world"
    assert ctype == "application/octet-stream"
    assert captured["resolved_url"] == "https://example.com/x"
    assert captured["allow_called"] is True
    assert captured["pin_during_fetch"] == {"example.com": {"203.0.113.7"}}
    # After exit the contextvar is back to its starting state.
    assert ssrf.pinned_dns.get() is None


@pytest.mark.asyncio
async def test_download_bytes_resets_pin_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An exception during fetch must still unwind the pin (try/finally)."""
    session = _stub_session(monkeypatch)
    session.get.side_effect = RuntimeError("boom")

    monkeypatch.setattr(
        "src.core.utils.ssrf.resolve_and_validate",
        lambda _u: {"203.0.113.8"},
    )
    monkeypatch.setattr("src.core.utils.ssrf.assert_allowed_url", lambda _u: None)

    with pytest.raises(RuntimeError):
        await AsyncAPIClient.download_bytes("https://example.com/x", max_size=1024)

    assert ssrf.pinned_dns.get() is None


@pytest.mark.asyncio
async def test_download_bytes_skips_ssrf_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``check_ssrf=False`` still runs the allow-list but skips DNS pinning."""
    _stub_session(monkeypatch)

    monkeypatch.setattr(
        "src.core.utils.ssrf.resolve_and_validate",
        lambda _u: pytest.fail("resolve_and_validate should not run"),
    )
    called: dict[str, bool] = {}
    monkeypatch.setattr(
        "src.core.utils.ssrf.assert_allowed_url",
        lambda _u: called.setdefault("allow", True),
    )

    body, _ctype = await AsyncAPIClient.download_bytes(
        "https://example.com/x", max_size=1024, check_ssrf=False
    )
    assert body == b"hello-world"
    assert called.get("allow") is True
    assert ssrf.pinned_dns.get() is None
