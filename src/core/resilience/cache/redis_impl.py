"""Redis-backed async cache, with per-call fail-open to in-memory.

If a Redis operation raises (other than a contract-mandated ``ValueError``
from ``incr``), the call is retried against an embedded
``InMemoryCacheBackend`` so the application keeps working while Redis
recovers. The fallback flag is sticky — but two paths bring the backend
back to Redis after a transient outage:

* :meth:`is_healthy` — wired into the readiness endpoint. On a
  successful ``PING`` it clears the fallback flag.
* In-call probe inside :meth:`_try_recover` — when degraded, at most
  one ``PING`` per :data:`_RECOVERY_PROBE_INTERVAL_S` seconds; on
  success the current call falls through to the Redis path. Mirrors
  ``RedisThrottle.check`` so deployments that don't poll
  ``/readyz`` still recover.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from src.core.resilience.cache.base import BaseCacheBackend
from src.core.resilience.cache.memory_impl import InMemoryCacheBackend
from src.core.resilience.health import BackendHealth

logger = logging.getLogger(__name__)

# Max frequency of in-call PING probes while degraded. Read/written
# without the lock — a racy double-probe under contention is harmless
# (two workers may PING once each before they observe the cleared
# flag, every subsequent worker reuses the cleared flag).
_RECOVERY_PROBE_INTERVAL_S = 30.0


class RedisCacheBackend(BaseCacheBackend):
    """Distributed cache using ``redis.asyncio``."""

    def __init__(
        self,
        redis_client: Any,
        *,
        alias: str = "cache:default",
        key_prefix: str = "",
    ) -> None:
        """Bind the backend to an ``aioredis``-style async client.

        Args:
            redis_client: An async Redis client (``redis.asyncio.Redis``
                or compatible).
            alias: Stable identifier used by the recovery monitor; the
                provider passes ``"cache:<config-alias>"``.
            key_prefix: String prepended to every key before the Redis
                call. Empty by default; production deployments should
                pass ``settings.cache_key_prefix`` so two services
                sharing a Redis cluster cannot collide. The in-memory
                fallback is per-process and does not need the prefix.
        """
        self._redis = redis_client
        self._fallback = InMemoryCacheBackend()
        self._health: BackendHealth = BackendHealth.ACTIVE
        self._lock = asyncio.Lock()
        # Last unix timestamp at which an in-call recovery probe ran.
        # Initialised to 0.0 so the first degraded operation probes
        # immediately.
        self._last_probe_at: float = 0.0
        self.alias = alias
        self._key_prefix = f"{key_prefix}:" if key_prefix else ""

    def _k(self, key: str) -> str:
        """Apply :attr:`_key_prefix` to a logical key."""
        return f"{self._key_prefix}{key}"

    @property
    def health(self) -> BackendHealth:
        """Expose the current ``BackendHealth`` to the recovery monitor."""
        return self._health

    async def try_recover(self) -> bool:
        """Probe Redis and clear the sticky fallback flag on success.

        Returns:
            ``True`` exactly when this call flipped the internal state
            from ``DEGRADED`` to ``ACTIVE``. ``False`` for an already-
            ``ACTIVE`` backend or a probe that still found Redis down.
        """
        if self._health is BackendHealth.ACTIVE:
            return False
        try:
            if not bool(await self._redis.ping()):
                return False
        except Exception:  # noqa: BLE001
            return False
        async with self._lock:
            if self._health is BackendHealth.DEGRADED:
                self._health = BackendHealth.ACTIVE
                logger.info(
                    "Redis cache recovered (monitor probe); leaving fallback mode."
                )
                return True
        return False

    @property
    def backend_name(self) -> str:
        """Label this backend reports in health checks / logs.

        Returns:
            ``"redis-fallback"`` once the in-memory fallback has been
            activated, otherwise ``"redis"``.
        """
        return "redis-fallback" if self._health is BackendHealth.DEGRADED else "redis"

    async def _flip_fallback(self, op: str, exc: Exception) -> None:
        """Mark the backend as degraded and log the first occurrence.

        Args:
            op: The operation that failed (e.g. ``"get"``); included
                in the warning log.
            exc: The exception raised by ``redis-py``.
        """
        async with self._lock:
            if self._health is BackendHealth.ACTIVE:
                logger.warning(
                    "Redis cache unavailable (%s); falling back to in-memory: %s",
                    op,
                    exc,
                )
                self._health = BackendHealth.DEGRADED

    async def _try_recover(self) -> bool:
        """Decide whether the current call should attempt Redis.

        Always ``True`` when the backend is not in fallback mode. While
        degraded, runs an opportunistic ``PING`` at most once per
        :data:`_RECOVERY_PROBE_INTERVAL_S` seconds; on success the
        flag is cleared and the caller is asked to fall through to the
        Redis path. The throttle uses the same shape — see
        ``RedisThrottle.check``.

        Returns:
            ``True`` when the caller can use Redis; ``False`` when the
            caller must keep using the in-memory fallback.
        """
        if self._health is BackendHealth.ACTIVE:
            return True
        now = time.time()
        if (now - self._last_probe_at) < _RECOVERY_PROBE_INTERVAL_S:
            return False
        self._last_probe_at = now
        try:
            await self._redis.ping()
        except Exception:  # noqa: BLE001
            return False
        async with self._lock:
            if self._health is BackendHealth.DEGRADED:
                self._health = BackendHealth.ACTIVE
                logger.info(
                    "Redis cache recovered (in-call probe); leaving fallback mode."
                )
        return True

    async def get(self, key: str) -> Any | None:
        """Fetch a value by key (fails over to in-memory on Redis error).

        Args:
            key: Cache key.

        Returns:
            Decoded value, or ``None`` on miss.
        """
        if not await self._try_recover():
            return await self._fallback.get(key)
        try:
            raw = await self._redis.get(self._k(key))
        except Exception as exc:  # noqa: BLE001
            await self._flip_fallback("get", exc)
            return await self._fallback.get(key)
        if raw is None:
            return None
        return _decode(raw)

    async def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        """Set ``key`` to ``value`` with optional TTL, falling back on Redis failure.

        Args:
            key: Cache key.
            value: JSON-encodable payload.
            ttl: Lifetime in seconds; ``None`` means no expiry.
        """
        if not await self._try_recover():
            await self._fallback.set(key, value, ttl)
            return
        try:
            payload = _encode(value)
            if ttl:
                await self._redis.set(self._k(key), payload, ex=ttl)
            else:
                await self._redis.set(self._k(key), payload)
        except Exception as exc:  # noqa: BLE001
            await self._flip_fallback("set", exc)
            await self._fallback.set(key, value, ttl)

    async def delete(self, key: str) -> None:
        """Delete ``key`` (no-op if missing), falling back on Redis failure.

        Args:
            key: Cache key.
        """
        if not await self._try_recover():
            await self._fallback.delete(key)
            return
        try:
            await self._redis.delete(self._k(key))
        except Exception as exc:  # noqa: BLE001
            await self._flip_fallback("delete", exc)
            await self._fallback.delete(key)

    async def incr(self, key: str) -> int:
        # Note: do NOT flip fallback on missing-key error — that's the caller's
        # contract. Only flip for transport / connection failures.
        """Increment an integer counter at ``key`` by 1.

        Raises ``KeyError`` (without flipping to fallback) when the
        key is absent — callers rely on this to detect cold start.

        Args:
            key: Cache key holding the counter.

        Returns:
            The new counter value after the increment.

        Raises:
            KeyError: ``key`` does not yet exist.
        """
        if not await self._try_recover():
            return await self._fallback.incr(key)
        try:
            # We use INCR — redis-py creates the key with value 1 if missing.
            # To match the contract (raise on missing), check existence first.
            prefixed = self._k(key)
            exists = await self._redis.exists(prefixed)
            if not exists:
                raise KeyError(key)
            return int(await self._redis.incr(prefixed))
        except KeyError:
            raise
        except Exception as exc:  # noqa: BLE001
            await self._flip_fallback("incr", exc)
            return await self._fallback.incr(key)

    async def add(self, key: str, value: Any, ttl: int | None = None) -> bool:
        """Atomic create — set ``key`` only when it doesn't already exist.

        Uses Redis ``SET key value NX EX ttl``.

        Args:
            key: Cache key.
            value: JSON-encodable payload.
            ttl: Lifetime in seconds; ``None`` means no expiry.

        Returns:
            ``True`` if the key was set, ``False`` if it already existed.
        """
        if not await self._try_recover():
            return await self._fallback.add(key, value, ttl)
        try:
            payload = _encode(value)
            # NX = set only if not exists; returns True on set, None on no-op.
            result = await self._redis.set(self._k(key), payload, nx=True, ex=ttl)
            return bool(result)
        except Exception as exc:  # noqa: BLE001
            await self._flip_fallback("add", exc)
            return await self._fallback.add(key, value, ttl)

    async def clear(self) -> None:
        """Flush every key (falls back on Redis failure)."""
        if not await self._try_recover():
            await self._fallback.clear()
            return
        try:
            await self._redis.flushdb()
        except Exception as exc:  # noqa: BLE001
            await self._flip_fallback("clear", exc)
            await self._fallback.clear()

    async def is_healthy(self) -> bool:
        """Probe Redis and clear the sticky fallback flag on recovery.

        Returns:
            ``True`` when ``PING`` succeeds; ``False`` otherwise.
        """
        try:
            pong = await self._redis.ping()
            healthy = bool(pong)
            if healthy and self._health is BackendHealth.DEGRADED:
                async with self._lock:
                    self._health = BackendHealth.ACTIVE
                    logger.info("Redis cache recovered; leaving fallback mode.")
            return healthy
        except Exception:  # noqa: BLE001
            return False


def _encode(value: Any) -> Any:
    """Serialise ``value`` for Redis storage.

    Scalars (str, int, float, bool, None) pass through; everything else
    is JSON-dumped with ``default=str`` so unexpected types don't raise.

    Args:
        value: Any payload to store.

    Returns:
        The original scalar, or a JSON-encoded string.
    """
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return json.dumps(value, default=str)


def _decode(raw: Any) -> Any:
    """Reverse :func:`_encode` — bytes → str → JSON → Python value.

    Falls back to the original string if JSON parsing fails so we
    never lose data we stored as a literal.

    Args:
        raw: Value returned by Redis.

    Returns:
        Decoded Python value.
    """
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return raw
    return raw
