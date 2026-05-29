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
    """Build the breaker registry at first call, degrading on Redis miss.

    Resolves the configured Redis alias and constructs a ``RedisRegistry``.
    If the client cannot be built (``get_redis_client`` raises) the
    helper logs a warning and returns an ``InMemoryRegistry`` so the
    application boots even with Redis offline.

    Returns:
        The constructed registry (``RedisRegistry`` or ``InMemoryRegistry``).
    """
    from src.core.resilience.recovery import (
        register_boot_fallback,
        register_for_recovery,
    )
    from src.core.utils.redis import get_redis_client

    settings = get_settings()
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
