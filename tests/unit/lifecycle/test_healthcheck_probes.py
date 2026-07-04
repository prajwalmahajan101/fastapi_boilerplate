"""Regression tests for the real resilience health probes.

Guards against the provider-call bug where ``cache_check`` /
``throttle_check`` / ``breaker_check`` awaited the *synchronous* kit
providers (and called ``get_breaker`` positionally without a config),
raising ``TypeError`` that the probes' ``except`` swallowed into a
permanent ``healthy=False``. With no Redis (unit tier) the providers
resolve in-memory backends, which always report healthy.
"""

from __future__ import annotations

import pytest

from src.core.lifecycle.healthcheck import (
    breaker_check,
    cache_check,
    throttle_check,
)


@pytest.mark.asyncio
async def test_cache_probe_resolves_backend_without_error() -> None:
    """The cache probe resolves the (in-memory) backend and reports healthy."""
    result = await cache_check("default")()
    assert result.healthy is True
    assert result.detail == "memory"
    assert "Error" not in (result.detail or "")


@pytest.mark.asyncio
async def test_throttle_probe_resolves_backend_without_error() -> None:
    """The throttle probe resolves the backend without awaiting a sync call."""
    result = await throttle_check()()
    assert result.healthy is True
    assert result.detail == "memory"


@pytest.mark.asyncio
async def test_breaker_probe_resolves_via_registry() -> None:
    """The breaker probe resolves through the registry (config applied)."""
    result = await breaker_check("default")()
    assert result.healthy is True
    assert result.detail == "memory"
