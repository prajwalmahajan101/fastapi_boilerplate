"""Async singleton throttle provider — Redis first, in-memory fallback."""

from __future__ import annotations

import asyncio
import logging

from src.core.resilience.throttle.base import BaseThrottle
from src.core.resilience.throttle.memory_impl import InMemoryThrottle
from src.core.resilience.throttle.redis_impl import RedisThrottle
from src.core.runtime import get_settings

logger = logging.getLogger(__name__)

_throttle: BaseThrottle | None = None
_lock: asyncio.Lock = asyncio.Lock()


async def get_throttle() -> BaseThrottle:
    """Return the process-wide throttle (Redis if reachable, else in-memory).

    Returns:
        The cached ``BaseThrottle`` singleton — a ``RedisThrottle`` when
        the configured Redis alias is reachable at first call, otherwise
        an ``InMemoryThrottle`` (logged once, valid for the process).
    """
    global _throttle
    if _throttle is not None:
        return _throttle
    async with _lock:
        if _throttle is not None:
            return _throttle
        _throttle = await _create_throttle()
        return _throttle


async def _create_throttle() -> BaseThrottle:
    """Build the throttle backend at first call, degrading on Redis miss.

    Resolves the configured Redis alias via ``get_redis_client`` and
    wraps it in a ``RedisThrottle``. If the client cannot be built or
    ping fails, logs a warning and falls back to ``InMemoryThrottle``
    so the process keeps serving requests with per-worker counters.

    Returns:
        The throttle backend (``RedisThrottle`` or ``InMemoryThrottle``).
    """
    from src.core.utils.redis import get_redis_client

    alias = get_settings().rate_limit_redis_alias
    try:
        client = await get_redis_client(alias)
        return await RedisThrottle.create(client)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Throttle: Redis alias '%s' unavailable, using in-memory: %s", alias, exc
        )
        return InMemoryThrottle()


async def reset_throttle() -> None:
    """Test helper — drop the cached throttle."""
    global _throttle
    async with _lock:
        _throttle = None


async def reset_backend() -> None:
    """Drop the cached throttle so the next call rebuilds it.

    Mirrors :func:`src.core.resilience.cache.provider.reset_backend` —
    used by the readiness probe to escape a boot-time
    :class:`InMemoryThrottle` once Redis becomes reachable. The
    throttle is a process-wide singleton (no alias), so no argument.
    """
    global _throttle
    async with _lock:
        _throttle = None
