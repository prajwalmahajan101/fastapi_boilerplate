"""Smoke test for ``AsyncAPIClient.download_bytes`` — guards ISSUE-022.

The bug: ``download_bytes`` called ``assert_public_url`` without
importing it, so every invocation with the default ``check_ssrf=True``
raised ``NameError`` at runtime. This test exercises the call path
with a stubbed aiohttp session and asserts the SSRF guard runs
without exploding.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

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


@pytest.mark.asyncio
async def test_download_bytes_with_ssrf_guard_does_not_raise_nameerror(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``check_ssrf=True`` (the default) used to raise NameError; now it returns bytes."""
    session = MagicMock()
    session.get = MagicMock(return_value=_StubResponse())
    monkeypatch.setattr(
        "src.core.utils.http_client._client.SessionManager.get_session",
        AsyncMock(return_value=session),
    )

    with patch(
        "src.core.utils.http_client._client.assert_public_url"
    ) as guard:
        body, ctype = await AsyncAPIClient.download_bytes(
            "https://example.com/x", max_size=1024
        )

    guard.assert_called_once_with("https://example.com/x")
    assert body == b"hello-world"
    assert ctype == "application/octet-stream"
