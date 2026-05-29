"""Redis sliding-window throttle using an atomic Lua script.

Same Lua shape as the Django Valkey throttle: ``ZREMRANGEBYSCORE`` to
evict expired entries, ``ZCARD`` for the current count, and a randomised
``ZADD`` token to keep entries unique within the same epoch second.

**Recovery from Redis fallback.** When ``check()`` fails to reach Redis,
``_health`` flips to ``BackendHealth.DEGRADED`` and subsequent calls
short-circuit to the in-memory deque. Two paths bring the throttle
back to Redis once the outage clears:

* :meth:`is_healthy` — wired into the readiness endpoint. Mirrors
  ``RedisCacheBackend.is_healthy``: on a successful ``PING`` it clears
  the fallback flag under the lock and emits one info log line.
* In-call probe inside :meth:`check` — when degraded, at most one
  ``PING`` per :data:`_RECOVERY_PROBE_INTERVAL_S` seconds; on success
  the call falls through to the normal Redis Lua path so the current
  request lands on Redis. This covers deployments that don't poll the
  readiness probe on a fixed cadence.

Without either path, a transient Redis blip would silently degrade the
cluster to per-worker counts forever (effectively multiplying every
configured limit by the worker count).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from src.core.resilience.health import BackendHealth
from src.core.resilience.throttle.base import BaseThrottle, ThrottleResult
from src.core.resilience.throttle.lua_scripts import THROTTLE_LUA_SCRIPT
from src.core.resilience.throttle.memory_impl import InMemoryThrottle

logger = logging.getLogger(__name__)

# Max frequency of in-call PING probes while degraded. Read/written
# without the lock — a racy double-probe under contention is harmless
# (two workers may PING once each before they observe the cleared flag,
# every subsequent worker reuses the cleared flag).
_RECOVERY_PROBE_INTERVAL_S = 30.0

class RedisThrottle(BaseThrottle):
    """Distributed sliding-window throttle via a single Lua call per check."""

    def __init__(
        self,
        redis_client: Any,
        lua_sha: str,
        key_prefix: str = "throttle",
        *,
        alias: str = "throttle:default",
    ) -> None:
        """Wire the throttle to a Redis client + preloaded Lua script.

        Args:
            redis_client: An ``aioredis``-style async client.
            lua_sha: SHA returned by ``SCRIPT LOAD`` of the throttle Lua.
            key_prefix: Redis key namespacing prefix.
        """
        self._redis = redis_client
        self._lua_sha = lua_sha
        self._key_prefix = key_prefix
        self._fallback = InMemoryThrottle()
        self._health: BackendHealth = BackendHealth.ACTIVE
        self._lock = asyncio.Lock()
        # Last unix timestamp at which an in-call recovery probe ran.
        # Initialised to 0.0 so the first degraded ``check()`` probes
        # immediately.
        self._last_probe_at: float = 0.0
        self.alias = alias

    @property
    def health(self) -> BackendHealth:
        """Expose the current ``BackendHealth`` to the recovery monitor."""
        return self._health

    async def try_recover(self) -> bool:
        """Probe Redis and clear the sticky fallback flag on success.

        Returns:
            ``True`` exactly when this call flipped ``DEGRADED`` →
            ``ACTIVE``. ``False`` for an already-``ACTIVE`` backend or
            a probe that still found Redis down.
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
                    "Redis throttle recovered (monitor probe); leaving fallback mode."
                )
                return True
        return False

    @classmethod
    async def create(
        cls,
        redis_client: Any,
        *,
        key_prefix: str = "throttle",
        alias: str = "throttle:default",
    ) -> "RedisThrottle":
        """Async constructor — pings Redis and pre-loads the Lua script.

        Args:
            redis_client: An ``aioredis``-style async client.
            key_prefix: Redis key namespacing prefix.

        Returns:
            A ready-to-use ``RedisThrottle`` instance.
        """
        await redis_client.ping()
        lua_sha = await redis_client.script_load(THROTTLE_LUA_SCRIPT)
        return cls(redis_client, lua_sha, key_prefix, alias=alias)

    async def _flip_fallback(self, exc: Exception) -> None:
        """Mark the throttle as degraded and log the first occurrence.

        Args:
            exc: The exception raised by ``redis-py``.
        """
        async with self._lock:
            if self._health is BackendHealth.ACTIVE:
                logger.warning("Redis throttle unavailable, falling back: %s", exc)
                self._health = BackendHealth.DEGRADED

    async def check(
        self,
        identifier: str,
        *,
        limit: int,
        window_seconds: int,
    ) -> ThrottleResult:
        """Execute one Lua-driven throttle check, falling back on Redis error.

        While degraded, attempts an in-call recovery probe at most once
        per :data:`_RECOVERY_PROBE_INTERVAL_S` seconds — on success, the
        current call falls through to the Redis Lua path so the cluster
        starts sharing counts again immediately.

        Args:
            identifier: Throttle bucket key (already namespaced by scope).
            limit: Maximum allowed requests in ``window_seconds``.
            window_seconds: Rolling window duration in seconds.

        Returns:
            ``ThrottleResult`` with allow/deny + remaining quota +
            retry-after.
        """
        if self._health is BackendHealth.DEGRADED:
            now_probe = time.time()
            if (now_probe - self._last_probe_at) < _RECOVERY_PROBE_INTERVAL_S:
                return await self._fallback.check(
                    identifier, limit=limit, window_seconds=window_seconds
                )
            self._last_probe_at = now_probe
            try:
                await self._redis.ping()
            except Exception:  # noqa: BLE001
                return await self._fallback.check(
                    identifier, limit=limit, window_seconds=window_seconds
                )
            async with self._lock:
                if self._health is BackendHealth.DEGRADED:
                    self._health = BackendHealth.ACTIVE
                    logger.info(
                        "Redis throttle recovered (in-call probe); leaving fallback mode."
                    )
            # Fall through to the Redis Lua path below so the current
            # call lands on Redis instead of paying for two PING+lua
            # round trips on the same request.
        key = f"{self._key_prefix}:{identifier}"
        now = time.time()
        try:
            from redis.exceptions import NoScriptError

            try:
                result = await self._redis.evalsha(
                    self._lua_sha, 1, key, str(limit), str(window_seconds), str(now)
                )
            except NoScriptError:
                self._lua_sha = await self._redis.script_load(THROTTLE_LUA_SCRIPT)
                result = await self._redis.evalsha(
                    self._lua_sha, 1, key, str(limit), str(window_seconds), str(now)
                )
        except Exception as exc:  # noqa: BLE001
            await self._flip_fallback(exc)
            return await self._fallback.check(
                identifier, limit=limit, window_seconds=window_seconds
            )

        allowed = bool(int(result[0]))
        count = int(result[1])
        ttl = float(result[2])
        return ThrottleResult(
            allowed=allowed,
            limit=limit,
            remaining=max(0, limit - count),
            reset_at=int(now + ttl),
            retry_after=ttl if not allowed else 0.0,
        )

    async def check_fixed_window(
        self,
        identifier: str,
        *,
        limit: int,
        window_seconds: int,
    ) -> ThrottleResult:
        """Sliding-window-counter check via the dedicated O(1) Lua script.

        Three string ops (``GET`` current bucket + ``GET`` previous +
        ``INCR``) instead of the sorted-set triplet that
        :meth:`check` performs. The cost-per-call drop is the reason
        callers reach for this over a per-identifier sliding window
        on high-RPS global gates.

        Args:
            identifier: Throttle bucket key (already namespaced by scope).
                Becomes the Lua script's ``key_prefix``; the script
                appends ``:<window_start>`` to form the per-window keys.
            limit: Maximum allowed events in ``window_seconds``.
            window_seconds: Rolling window duration in seconds.

        Returns:
            ``ThrottleResult`` with allow/deny + remaining quota +
            retry-after.
        """
        from src.core.resilience.throttle.global_lua import load_sha, reset_sha

        if self._health is BackendHealth.DEGRADED:
            now_probe = time.time()
            if (now_probe - self._last_probe_at) < _RECOVERY_PROBE_INTERVAL_S:
                return await self._fallback.check_fixed_window(
                    identifier, limit=limit, window_seconds=window_seconds
                )
            self._last_probe_at = now_probe
            try:
                await self._redis.ping()
            except Exception:  # noqa: BLE001
                return await self._fallback.check_fixed_window(
                    identifier, limit=limit, window_seconds=window_seconds
                )
            async with self._lock:
                if self._health is BackendHealth.DEGRADED:
                    self._health = BackendHealth.ACTIVE
                    logger.info(
                        "Redis throttle recovered (in-call probe, fixed-window); "
                        "leaving fallback mode."
                    )

        key_prefix = f"{self._key_prefix}:global:{identifier}"
        now = time.time()
        try:
            from redis.exceptions import NoScriptError

            sha = await load_sha(self._redis)
            try:
                result = await self._redis.evalsha(
                    sha, 1, key_prefix, str(limit), str(window_seconds), str(now)
                )
            except NoScriptError:
                # Redis ``SCRIPT FLUSH`` (or a failover that lost the
                # cache) — drop our cache and re-load once.
                await reset_sha()
                sha = await load_sha(self._redis)
                result = await self._redis.evalsha(
                    sha, 1, key_prefix, str(limit), str(window_seconds), str(now)
                )
        except Exception as exc:  # noqa: BLE001
            await self._flip_fallback(exc)
            return await self._fallback.check_fixed_window(
                identifier, limit=limit, window_seconds=window_seconds
            )

        allowed = bool(int(result[0]))
        count = int(result[1])
        ttl = float(result[2])
        return ThrottleResult(
            allowed=allowed,
            limit=limit,
            remaining=max(0, limit - count),
            reset_at=int(now + ttl),
            retry_after=ttl if not allowed else 0.0,
        )

    async def is_healthy(self) -> bool:
        """Probe Redis and clear the sticky fallback flag on recovery.

        Wired into the readiness endpoint. Matches the in-call probe
        semantics (single log line on recovery, lock held only for the
        flag flip) so the two recovery paths can race without producing
        duplicate "recovered" log lines.

        Returns:
            ``True`` when ``PING`` succeeds; ``False`` otherwise.
        """
        try:
            pong = await self._redis.ping()
            healthy = bool(pong)
            if healthy and self._health is BackendHealth.DEGRADED:
                async with self._lock:
                    if self._health is BackendHealth.DEGRADED:
                        self._health = BackendHealth.ACTIVE
                        logger.info("Redis throttle recovered; leaving fallback mode.")
            return healthy
        except Exception:  # noqa: BLE001
            return False

    @property
    def backend_name(self) -> str:
        """Identify which path is serving rate-limit decisions.

        Returns:
            ``"redis-fallback"`` while degraded, otherwise ``"redis"``.
        """
        return "redis-fallback" if self._health is BackendHealth.DEGRADED else "redis"
