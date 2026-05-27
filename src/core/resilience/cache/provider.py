"""Async singleton cache provider, per Redis alias."""

from __future__ import annotations

import asyncio
import logging

from src.core.resilience.cache.base import BaseCacheBackend
from src.core.resilience.cache.memory_impl import InMemoryCacheBackend
from src.core.resilience.cache.redis_impl import RedisCacheBackend

logger = logging.getLogger(__name__)

_caches: dict[str, BaseCacheBackend] = {}
_lock: asyncio.Lock = asyncio.Lock()


async def get_cache(alias: str = "default") -> BaseCacheBackend:
    """Return the cache backend for ``alias`` (Redis if reachable, else in-memory).

    Args:
        alias: Cache backend alias from ``redis_urls`` config.

    Returns:
        A ``BaseCacheBackend`` (Redis-backed or in-memory).
    """
    cached = _caches.get(alias)
    if cached is not None:
        return cached
    async with _lock:
        if alias in _caches:
            return _caches[alias]
        backend = await _create_cache(alias)
        _caches[alias] = backend
        return backend


async def _create_cache(alias: str) -> BaseCacheBackend:
    """Build a backend for ``alias``, falling back to in-memory on Redis miss.

    Args:
        alias: Cache backend alias from ``redis_urls`` config.

    Returns:
        Either a ``RedisCacheBackend`` (Redis reachable) or
        ``InMemoryCacheBackend`` (degraded path with a warning log).
    """
    from src.core.utils.redis import get_redis_client

    try:
        client = await get_redis_client(alias)
        await client.ping()
        logger.info("Cache backend ready (redis, alias=%s)", alias)
        return RedisCacheBackend(client)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Cache alias '%s' Redis unavailable, using in-memory: %s", alias, exc
        )
        return InMemoryCacheBackend()


async def reset_caches() -> None:
    """Test helper — drop every cached backend."""
    async with _lock:
        _caches.clear()


async def reset_backend(alias: str) -> None:
    """Drop the cached backend for ``alias`` so the next call rebuilds it.

    Used by the readiness probe to escape a boot-time in-memory fallback
    once the configured Redis alias starts answering ``PING``: the
    in-call recovery probe inside :class:`RedisCacheBackend` only
    promotes a wrapped Redis that lost connectivity later — it cannot
    upgrade a bare :class:`InMemoryCacheBackend` that was cached because
    the first ``PING`` failed at boot. Dropping the cached entry lets
    the next ``get_cache(alias)`` rebuild against the now-live Redis.

    Args:
        alias: Cache backend alias from ``redis_urls`` config.
    """
    async with _lock:
        _caches.pop(alias, None)
