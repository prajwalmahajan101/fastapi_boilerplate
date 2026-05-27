"""Redis-backed circuit breaker — distributed across workers via atomic Lua.

State (state / failure_count / success_count / last_failure) lives in one
Redis hash per breaker name. All transitions execute as a single Lua
script (one ``EVALSHA`` round-trip) so two workers cannot race on the
same breaker.

Fail-open: if Redis is unreachable, each breaker delegates that call (and
subsequent calls) to an embedded ``InMemoryCircuitBreaker`` and logs once.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from src.core.resilience.circuit_breaker.base import (
    BaseCircuitBreaker,
    BaseCircuitBreakerRegistry,
    CircuitBreakerConfig,
)
from src.core.resilience.circuit_breaker.memory_impl import (
    InMemoryCircuitBreaker,
    InMemoryRegistry,
)
from src.core.resilience.health import BackendHealth

logger = logging.getLogger(__name__)

# ARGV: action, failure_threshold, success_threshold, recovery_timeout, now
CIRCUIT_BREAKER_LUA_SCRIPT = """
local key = KEYS[1]
local action = ARGV[1]
local failure_threshold = tonumber(ARGV[2])
local success_threshold = tonumber(ARGV[3])
local recovery_timeout = tonumber(ARGV[4])
local now = tonumber(ARGV[5])
local ttl = math.ceil(recovery_timeout * 10)

local function read_state()
    local vals = redis.call('HMGET', key, 'state', 'failure_count', 'success_count', 'last_failure')
    local state = vals[1] or 'closed'
    local fc = tonumber(vals[2]) or 0
    local sc = tonumber(vals[3]) or 0
    local lf = tonumber(vals[4]) or 0
    return state, fc, sc, lf
end

local function write_state(state, fc, sc, lf)
    redis.call('HMSET', key, 'state', state, 'failure_count', fc, 'success_count', sc, 'last_failure', lf)
    redis.call('EXPIRE', key, ttl)
end

local state, fc, sc, lf = read_state()

if state == 'open' and (now - lf) >= recovery_timeout then
    state = 'half_open'
    sc = 0
    write_state(state, fc, sc, lf)
end

if action == 'is_available' then
    if state == 'open' then
        local remaining = recovery_timeout - (now - lf)
        if remaining < 0 then remaining = 0 end
        return {0, state, tostring(remaining)}
    end
    return {1, state, '0'}

elseif action == 'record_success' then
    if state == 'half_open' then
        sc = sc + 1
        if sc >= success_threshold then
            state = 'closed'
            fc = 0
        end
    elseif state == 'closed' then
        fc = 0
    end
    write_state(state, fc, sc, lf)
    return {1, state, '0'}

elseif action == 'record_failure' then
    fc = fc + 1
    lf = now
    if state == 'half_open' then
        state = 'open'
    elseif state == 'closed' and fc >= failure_threshold then
        state = 'open'
    end
    write_state(state, fc, sc, lf)
    local remaining = 0
    if state == 'open' then
        remaining = recovery_timeout
    end
    return {1, state, tostring(remaining)}

elseif action == 'reset' then
    redis.call('DEL', key)
    return {1, 'closed', '0'}

elseif action == 'get_stats' then
    return {state, tostring(fc), tostring(sc), tostring(lf)}
end

return {0, 'error', '0'}
"""


class RedisCircuitBreaker(BaseCircuitBreaker):
    """Distributed breaker backed by Redis Lua, with in-memory fallback."""

    def __init__(
        self,
        breaker_name: str,
        config: CircuitBreakerConfig,
        redis_client: Any,
        lua_sha: str,
        key_prefix: str = "cb",
    ) -> None:
        """Wire the breaker to a Redis client + preloaded Lua script.

        Args:
            breaker_name: Logical identifier (e.g. ``"bhn_api"``);
                also forms part of the Redis hash key.
            config: Threshold + recovery settings.
            redis_client: An ``aioredis``-style async client.
            lua_sha: SHA returned by ``SCRIPT LOAD`` of the breaker Lua.
            key_prefix: Namespacing prefix for the Redis hash key.
        """
        self._name = breaker_name
        self._config = config
        self._redis = redis_client
        self._lua_sha = lua_sha
        self._key = f"{key_prefix}:{breaker_name}"
        self._fallback = InMemoryCircuitBreaker(breaker_name, config)
        self._health: BackendHealth = BackendHealth.ACTIVE
        self._fallback_lock = asyncio.Lock()

    @property
    def name(self) -> str:
        """Breaker identifier used in stats and log records.

        Returns:
            The configured ``breaker_name``.
        """
        return self._name

    async def _call_lua(self, action: str) -> list[Any]:
        """Execute the Lua script for ``action``; flip to fallback on Redis error.

        Supported actions: ``is_available``, ``record_success``,
        ``record_failure``, ``reset``, ``get_stats``. On Redis failure
        we mark the breaker as using the in-memory fallback (a once-only
        log) and re-raise so the caller method can route to it.

        Args:
            action: One of the action names supported by the Lua script.

        Returns:
            Raw Lua result list — interpretation depends on the action.

        Raises:
            Exception: Re-raised after marking the fallback so callers
                can swap to ``self._fallback``.
        """
        from redis.exceptions import NoScriptError

        try:
            result = await self._redis.evalsha(
                self._lua_sha,
                1,
                self._key,
                action,
                str(self._config.failure_threshold),
                str(self._config.success_threshold),
                str(self._config.recovery_timeout),
                str(time.time()),
            )
            async with self._fallback_lock:
                self._health = BackendHealth.ACTIVE
            return result
        except NoScriptError:
            self._lua_sha = await self._redis.script_load(CIRCUIT_BREAKER_LUA_SCRIPT)
            result = await self._redis.evalsha(
                self._lua_sha,
                1,
                self._key,
                action,
                str(self._config.failure_threshold),
                str(self._config.success_threshold),
                str(self._config.recovery_timeout),
                str(time.time()),
            )
            async with self._fallback_lock:
                self._health = BackendHealth.ACTIVE
            return result
        except Exception as exc:
            async with self._fallback_lock:
                if self._health is BackendHealth.ACTIVE:
                    logger.warning(
                        "Redis circuit breaker unavailable, falling back to in-memory",
                        extra={"breaker": self._name, "error": str(exc)},
                    )
                    self._health = BackendHealth.DEGRADED
            raise

    async def is_available(self) -> bool:
        """Return whether the breaker permits a call right now.

        Returns:
            ``True`` for CLOSED / HALF_OPEN states (calls allowed);
            ``False`` for OPEN.
        """
        try:
            result = await self._call_lua("is_available")
            return bool(int(result[0]))
        except Exception:
            return await self._fallback.is_available()

    async def record_success(self) -> None:
        """Note a successful call via the Lua script, falling back on error.

        Drives HALF_OPEN → CLOSED inside the Lua transaction so two
        workers cannot both reach success_threshold from the same
        starting state.
        """
        try:
            await self._call_lua("record_success")
        except Exception:
            await self._fallback.record_success()

    async def record_failure(self, exc: Exception | None = None) -> None:
        """Increment failure count, tripping OPEN past the threshold.

        Exceptions in ``config.excluded_exceptions`` are *not* counted —
        used so partner-validation errors don't open the breaker on
        legitimately bad payloads.

        Args:
            exc: The exception that triggered the failure. Inspected
                against ``excluded_exceptions``.
        """
        if exc is not None and isinstance(exc, self._config.excluded_exceptions):
            return
        try:
            await self._call_lua("record_failure")
        except Exception:
            await self._fallback.record_failure(exc)

    async def reset(self) -> None:
        """Force the breaker back to CLOSED (deletes the Redis hash).

        The ``reset`` Lua action does an unconditional ``DEL`` of the
        breaker's key so the next ``is_available`` rebuilds state from
        scratch. Falls back to the in-memory breaker on Redis failure
        so test/ops resets still take effect.
        """
        try:
            await self._call_lua("reset")
        except Exception:
            await self._fallback.reset()

    async def time_until_retry(self) -> float:
        """Seconds until the breaker transitions OPEN → HALF_OPEN.

        Returns:
            Remaining recovery time in seconds; ``0.0`` when the
            breaker is already CLOSED or HALF_OPEN.
        """
        try:
            result = await self._call_lua("is_available")
            return float(result[2])
        except Exception:
            return await self._fallback.time_until_retry()

    async def get_stats(self) -> dict[str, Any]:
        """Return a JSON-serialisable snapshot of the breaker's state.

        Returns:
            Dict with ``name``, ``state``, ``failure_count``,
            ``success_count``, ``time_until_retry``, and ``backend``
            (``"redis"`` or ``"memory-fallback"``).
        """
        try:
            result = await self._call_lua("get_stats")
            state_val = result[0]
            if isinstance(state_val, bytes):
                state_val = state_val.decode()
            return {
                "name": self._name,
                "state": state_val,
                "failure_count": int(result[1]),
                "success_count": int(result[2]),
                "time_until_retry": await self.time_until_retry(),
                "backend": "redis",
            }
        except Exception:
            stats = await self._fallback.get_stats()
            stats["backend"] = "memory-fallback"
            return stats


class RedisRegistry(BaseCircuitBreakerRegistry):
    """Registry that builds and tracks Redis-backed breakers.

    If Redis is unreachable at construction time, degrades wholesale to an
    ``InMemoryRegistry`` so the application continues with per-process
    state instead of failing every call.
    """

    def __init__(
        self,
        redis_client: Any,
        lua_sha: str,
        default_config: CircuitBreakerConfig | None = None,
        key_prefix: str = "cb",
    ) -> None:
        """Bind the registry to a Redis client + preloaded Lua script.

        Args:
            redis_client: An ``aioredis``-style async client.
            lua_sha: SHA returned by ``SCRIPT LOAD`` of the breaker Lua.
            default_config: Used when ``get_or_create`` is called
                without an explicit config.
            key_prefix: Redis hash key prefix shared across breakers.
        """
        self._redis = redis_client
        self._lua_sha = lua_sha
        self._default_config = default_config or CircuitBreakerConfig()
        self._key_prefix = key_prefix
        self._breakers: dict[str, RedisCircuitBreaker] = {}
        self._lock = asyncio.Lock()

    @classmethod
    async def create(
        cls,
        redis_client: Any,
        *,
        default_config: CircuitBreakerConfig | None = None,
        key_prefix: str = "cb",
    ) -> "RedisRegistry | InMemoryRegistry":
        """Async constructor — pings Redis, loads the script, or degrades.

        Returns an ``InMemoryRegistry`` (with a single warning log) when
        Redis is unreachable so the application can boot without Redis.

        Args:
            redis_client: An ``aioredis``-style async client.
            default_config: Default breaker config for new entries.
            key_prefix: Redis hash key prefix.

        Returns:
            A ``RedisRegistry`` when Redis is reachable, otherwise an
            ``InMemoryRegistry``.
        """
        try:
            await redis_client.ping()
            lua_sha = await redis_client.script_load(CIRCUIT_BREAKER_LUA_SCRIPT)
            logger.info(
                "Redis circuit breaker registry initialised (prefix=%s)", key_prefix
            )
            return cls(
                redis_client=redis_client,
                lua_sha=lua_sha,
                default_config=default_config,
                key_prefix=key_prefix,
            )
        except Exception as exc:
            logger.warning(
                "Failed to initialise Redis circuit breaker registry, "
                "using in-memory: %s",
                exc,
            )
            return InMemoryRegistry(default_config=default_config)

    async def get_or_create(
        self,
        name: str,
        config: CircuitBreakerConfig | None = None,
    ) -> RedisCircuitBreaker:
        """Return the existing breaker for ``name`` or create a fresh one.

        Args:
            name: Breaker identifier (e.g. ``"bhn_api"``).
            config: Optional override; falls back to the registry's
                default config.

        Returns:
            A ``RedisCircuitBreaker`` (created on first call for ``name``).
        """
        existing = self._breakers.get(name)
        if existing is not None:
            return existing
        async with self._lock:
            if name in self._breakers:
                return self._breakers[name]
            breaker = RedisCircuitBreaker(
                breaker_name=name,
                config=config or self._default_config,
                redis_client=self._redis,
                lua_sha=self._lua_sha,
                key_prefix=self._key_prefix,
            )
            self._breakers[name] = breaker
            return breaker

    async def remove(self, name: str) -> None:
        """Drop the local breaker and delete its Redis hash.

        Args:
            name: Breaker identifier to forget.
        """
        async with self._lock:
            self._breakers.pop(name, None)
        try:
            await self._redis.delete(f"{self._key_prefix}:{name}")
        except Exception:
            pass

    async def get_all_stats(self) -> dict[str, dict[str, Any]]:
        """Return a ``{name: stats}`` mapping for every registered breaker.

        Returns:
            Mapping suitable for an admin / health endpoint.
        """
        async with self._lock:
            items = list(self._breakers.items())
        return {name: await b.get_stats() for name, b in items}

    async def reset_all(self) -> None:
        """Reset every registered Redis breaker to CLOSED with zero counters.

        Snapshots the breaker list under the registry lock, then resets
        each one outside the lock so a slow Lua roundtrip doesn't block
        adds/removes elsewhere.
        """
        async with self._lock:
            items = list(self._breakers.values())
        for breaker in items:
            await breaker.reset()

    async def clear(self) -> None:
        """Drop every breaker locally and delete its hash from Redis.

        First clears the in-memory dict (so concurrent ``get_or_create``
        sees an empty registry), then walks the snapshotted names and
        best-effort deletes each Redis key. Delete failures are
        swallowed — the breaker is already forgotten locally and the
        Redis hash carries a TTL.
        """
        async with self._lock:
            names = list(self._breakers.keys())
            self._breakers.clear()
        for name in names:
            try:
                await self._redis.delete(f"{self._key_prefix}:{name}")
            except Exception:
                pass

    @property
    def backend_name(self) -> str:
        """Identify the Redis registry to the readyz probe.

        ``RedisRegistry.create(...)`` returns an ``InMemoryRegistry``
        when the boot-time ``PING`` fails, so a live ``RedisRegistry``
        instance always implies Redis was reachable as of last
        (re)initialisation. Per-call degradation lives on each
        :class:`RedisCircuitBreaker` via its ``_health`` flag
        (:data:`BackendHealth`) and does not propagate up here.

        Returns:
            The literal ``"redis"``.
        """
        return "redis"

    async def is_healthy(self) -> bool:
        """Probe Redis with ``PING`` so readyz can confirm reachability.

        Mirrors :meth:`RedisCacheBackend.is_healthy` /
        :meth:`RedisThrottle.is_healthy`. Does not flip any
        per-breaker state — per-call recovery on each
        :class:`RedisCircuitBreaker` is driven by every successful
        ``EVALSHA`` in :meth:`RedisCircuitBreaker._call_lua`.

        Returns:
            ``True`` when ``PING`` succeeds; ``False`` otherwise.
        """
        try:
            pong = await self._redis.ping()
            return bool(pong)
        except Exception:  # noqa: BLE001
            return False
