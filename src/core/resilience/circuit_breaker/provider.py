"""Async singleton provider for the circuit breaker registry.

First call constructs a ``RedisRegistry`` (which itself degrades to
``InMemoryRegistry`` if Redis is unreachable). Subsequent calls return
the cached instance.
"""

from __future__ import annotations

import asyncio
import logging

from src.core.resilience.circuit_breaker.base import BaseCircuitBreakerRegistry
from src.core.resilience.circuit_breaker.memory_impl import InMemoryRegistry
from src.core.resilience.circuit_breaker.redis_impl import RedisRegistry
from src.core.runtime import get_settings

logger = logging.getLogger(__name__)

_registry: BaseCircuitBreakerRegistry | None = None
_lock: asyncio.Lock = asyncio.Lock()


async def get_registry() -> BaseCircuitBreakerRegistry:
    """Return the process-wide circuit breaker registry (lazy init, async-safe).

    Returns:
        The cached registry — a ``RedisRegistry`` when the configured
        Redis alias was reachable at first call, otherwise an
        ``InMemoryRegistry`` (degradation logged once).
    """
    global _registry
    if _registry is not None:
        return _registry
    async with _lock:
        if _registry is not None:
            return _registry
        _registry = await _create_registry()
        return _registry


async def _create_registry() -> BaseCircuitBreakerRegistry:
    """Build the breaker registry at first call.

    Honours ``settings.circuit_breaker_backend``:

    * ``"auto"`` / ``"redis"`` — try Redis, fall back to in-memory on
      connection failure (historical behaviour).
    * ``"memory"`` — skip Redis entirely; return an
      :class:`InMemoryRegistry`.
    * ``"pybreaker"`` — build a process-local
      :class:`PyBreakerRegistry` (third-party in-process tier).

    Returns:
        The constructed registry.
    """
    from src.core.resilience.recovery import (
        register_boot_fallback,
        register_for_recovery,
    )

    settings = get_settings()
    backend = settings.circuit_breaker_backend

    if backend == "memory":
        logger.info("Circuit breaker: in-memory registry selected.")
        return InMemoryRegistry()

    if backend == "pybreaker":
        from src.core.resilience.circuit_breaker.pybreaker_impl import (  # noqa: PLC0415
            PyBreakerRegistry,
        )

        logger.info("Circuit breaker: pybreaker registry selected.")
        return PyBreakerRegistry()

    # ``auto`` / ``redis`` paths — try Redis with in-memory fallback.
    from src.core.utils.redis import get_redis_client  # noqa: PLC0415

    alias = settings.circuit_breaker_redis_alias
    prefix = settings.circuit_breaker_key_prefix
    recovery_alias = f"breaker:{alias}"

    try:
        redis_client = await get_redis_client(alias)
    except Exception as exc:
        logger.warning(
            "Circuit breaker: Redis alias '%s' unavailable, using in-memory registry: %s",
            alias,
            exc,
        )
        register_boot_fallback(recovery_alias)
        return InMemoryRegistry()

    registry = await RedisRegistry.create(
        redis_client, key_prefix=prefix, alias=recovery_alias
    )
    # ``RedisRegistry.create`` returns ``InMemoryRegistry`` on ping
    # failure — only register Redis-backed registries.
    if isinstance(registry, RedisRegistry):
        register_for_recovery(registry)
    else:
        register_boot_fallback(recovery_alias)
    return registry


async def reset_registry() -> None:
    """Test helper — drop the cached registry."""
    global _registry
    async with _lock:
        _registry = None


async def reset_backend() -> None:
    """Drop the cached registry so the next call rebuilds it.

    Mirrors :func:`src.core.resilience.cache.provider.reset_backend` —
    used by the readiness probe to escape a boot-time
    :class:`InMemoryRegistry` once Redis becomes reachable. The
    registry is a process-wide singleton (no alias), so no argument.
    """
    global _registry
    async with _lock:
        _registry = None
