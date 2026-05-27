"""Async Redis client cache, keyed by named alias.

``CoreSettings.redis_urls`` is a ``{alias: url}`` mapping (``default``,
``cache``, ``rate_limit``, …). Each alias gets one ``redis.asyncio.Redis``
instance backed by a connection pool, created on first use and re-used
across all callers in the process.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from src.core.runtime import get_settings

if TYPE_CHECKING:
    from redis.asyncio import Redis

logger = logging.getLogger(__name__)

_clients: dict[str, Any] = {}
_lock: asyncio.Lock = asyncio.Lock()


async def get_redis_client(alias: str = "default") -> "Redis":
    """Return the async Redis client bound to ``alias``.

    The client is lazily created on first use; subsequent calls return
    the cached instance, so all callers share one connection pool per
    alias.

    Args:
        alias: Alias from ``CoreSettings.redis_urls``.

    Returns:
        Cached ``redis.asyncio.Redis`` instance for ``alias``.

    Raises:
        KeyError: ``alias`` is not present in ``redis_urls``.
    """
    cached = _clients.get(alias)
    if cached is not None:
        return cached

    async with _lock:
        if alias in _clients:
            return _clients[alias]
        from redis.asyncio import Redis

        urls = get_settings().redis_urls
        if alias not in urls:
            raise KeyError(
                f"Redis alias '{alias}' is not configured. "
                f"Known aliases: {sorted(urls.keys())}"
            )
        client = Redis.from_url(urls[alias], decode_responses=True)
        _clients[alias] = client
        logger.info("Created Redis client for alias=%s", alias)
        return client


async def close_all_redis_clients() -> None:
    """Close every cached client. Call from application lifespan shutdown."""
    async with _lock:
        clients = list(_clients.values())
        _clients.clear()
    for client in clients:
        try:
            await client.aclose()
        except Exception:  # noqa: BLE001
            logger.warning("Failed to close Redis client cleanly.", exc_info=True)


async def wait_for_redis(
    alias: str = "default",
    *,
    retries: int = 5,
    backoff_s: float = 1.0,
) -> bool:
    """Block until the Redis client for ``alias`` responds to ``PING``.

    Used from the FastAPI lifespan so the resilience providers (cache,
    throttle, circuit breaker) have a chance to wrap a live Redis at
    first call instead of caching an in-memory fallback that no probe
    can rebuild. The function never raises — the caller decides whether
    a missing Redis is fatal.

    Args:
        alias: Alias from ``CoreSettings.redis_urls``.
        retries: Total attempts (including the first); each failed
            attempt waits ``backoff_s`` before the next.
        backoff_s: Linear wait between attempts.

    Returns:
        ``True`` when ``PING`` succeeded within ``retries`` attempts;
        ``False`` when every attempt failed.
    """
    for attempt in range(retries):
        try:
            client = await get_redis_client(alias)
            await client.ping()
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Redis alias '%s' not ready (attempt %d/%d): %s",
                alias,
                attempt + 1,
                retries,
                exc,
            )
            if attempt < retries - 1:
                await asyncio.sleep(backoff_s)
    return False
