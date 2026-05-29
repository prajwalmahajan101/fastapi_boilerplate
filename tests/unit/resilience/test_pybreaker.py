"""pybreaker circuit-breaker tier — open / half-open / closed transitions."""

from __future__ import annotations

import asyncio

import pytest

from src.core.exceptions.infrastructure import (
    ExternalServiceError,
    ServiceUnavailableError,
)
from src.core.resilience.circuit_breaker.base import CircuitBreakerConfig
from src.core.resilience.circuit_breaker.pybreaker_impl import (
    PyBreakerCircuitBreaker,
    PyBreakerRegistry,
)


def _config(**overrides) -> CircuitBreakerConfig:
    defaults = {
        "failure_threshold": 2,
        "success_threshold": 1,
        "recovery_timeout": 0.05,
    }
    defaults.update(overrides)
    return CircuitBreakerConfig(**defaults)


@pytest.mark.asyncio
async def test_initial_state_is_available():
    cb = PyBreakerCircuitBreaker("svc", _config())
    assert await cb.is_available() is True
    stats = await cb.get_stats()
    assert stats["backend"] == "pybreaker"
    assert stats["state"] == "closed"


@pytest.mark.asyncio
async def test_opens_after_failure_threshold():
    cb = PyBreakerCircuitBreaker("svc", _config())
    await cb.record_failure(ExternalServiceError("svc"))
    await cb.record_failure(ExternalServiceError("svc"))
    assert await cb.is_available() is False
    assert (await cb.get_stats())["state"] == "open"


@pytest.mark.asyncio
async def test_recovers_via_call_after_timeout():
    cb = PyBreakerCircuitBreaker("svc", _config(recovery_timeout=0.05))
    await cb.record_failure(ExternalServiceError("svc"))
    await cb.record_failure(ExternalServiceError("svc"))
    assert await cb.is_available() is False
    await asyncio.sleep(0.1)
    # pybreaker only probes on the next actual .call() — drive one
    # through and verify the breaker re-closes.
    result = await cb.call(lambda: "ok")
    assert result == "ok"
    assert (await cb.get_stats())["state"] == "closed"


@pytest.mark.asyncio
async def test_excluded_exceptions_do_not_open():
    cb = PyBreakerCircuitBreaker(
        "svc", _config(excluded_exceptions=(ValueError,))
    )
    for _ in range(5):
        await cb.record_failure(ValueError("validation"))
    assert await cb.is_available() is True


@pytest.mark.asyncio
async def test_registry_reuses_breakers():
    reg = PyBreakerRegistry()
    a = await reg.get_or_create("svc")
    b = await reg.get_or_create("svc")
    assert a is b
    assert reg.backend_name == "pybreaker"
    assert await reg.is_healthy() is True


@pytest.mark.asyncio
async def test_call_raises_when_open():
    cb = PyBreakerCircuitBreaker("svc", _config())
    await cb.record_failure(ExternalServiceError("svc"))
    await cb.record_failure(ExternalServiceError("svc"))
    with pytest.raises(ServiceUnavailableError):
        await cb.call(lambda: 1)
