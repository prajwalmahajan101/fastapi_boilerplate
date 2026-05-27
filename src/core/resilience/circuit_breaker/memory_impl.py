"""In-memory circuit breaker — per-process, ``asyncio.Lock``-guarded.

Used standalone in single-worker deployments and as the fallback when
Redis is unavailable. Each ``RedisCircuitBreaker`` embeds one of these so
a Redis outage can degrade without dropping calls.
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
    CircuitState,
)

logger = logging.getLogger(__name__)


class InMemoryCircuitBreaker(BaseCircuitBreaker):
    """Per-process circuit breaker with explicit state machine."""

    def __init__(self, breaker_name: str, config: CircuitBreakerConfig) -> None:
        """Bind the breaker to a config and start in CLOSED state.

        Args:
            breaker_name: Logical identifier (e.g. ``"bhn_api"``).
            config: Threshold + recovery settings.
        """
        self._name = breaker_name
        self._config = config
        self._state: CircuitState = CircuitState.CLOSED
        self._failure_count: int = 0
        self._success_count: int = 0
        self._opened_at: float = 0.0
        self._lock = asyncio.Lock()

    @property
    def name(self) -> str:
        """Breaker identifier used in stats and log records.

        Returns:
            The configured ``breaker_name``.
        """
        return self._name

    async def _maybe_half_open(self) -> None:
        """Promote OPEN → HALF_OPEN if the recovery window has elapsed."""
        if (
            self._state == CircuitState.OPEN
            and (time.monotonic() - self._opened_at) >= self._config.recovery_timeout
        ):
            self._state = CircuitState.HALF_OPEN
            self._success_count = 0

    async def is_available(self) -> bool:
        """Return whether the breaker permits a call right now.

        Returns:
            ``True`` for CLOSED / HALF_OPEN; ``False`` for OPEN.
        """
        async with self._lock:
            await self._maybe_half_open()
            return self._state != CircuitState.OPEN

    async def record_success(self) -> None:
        """Note a successful call, advancing HALF_OPEN → CLOSED when stable.

        In HALF_OPEN, each success counts toward ``success_threshold`` —
        when it hits, every counter resets and the breaker closes.
        In CLOSED, a success just clears any lingering failure count.
        """
        async with self._lock:
            await self._maybe_half_open()
            if self._state == CircuitState.HALF_OPEN:
                self._success_count += 1
                if self._success_count >= self._config.success_threshold:
                    self._state = CircuitState.CLOSED
                    self._failure_count = 0
                    self._success_count = 0
                    self._opened_at = 0.0
            elif self._state == CircuitState.CLOSED:
                self._failure_count = 0

    async def record_failure(self, exc: Exception | None = None) -> None:
        """Increment failure count, tripping OPEN past the threshold.

        Exceptions in ``config.excluded_exceptions`` are not counted —
        used so domain validation errors don't open the breaker on
        legitimately bad payloads.

        Args:
            exc: The exception that triggered the failure.
        """
        if exc is not None and isinstance(exc, self._config.excluded_exceptions):
            return
        async with self._lock:
            await self._maybe_half_open()
            if self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.OPEN
                self._opened_at = time.monotonic()
                self._success_count = 0
                return
            if self._state == CircuitState.CLOSED:
                self._failure_count += 1
                if self._failure_count >= self._config.failure_threshold:
                    self._state = CircuitState.OPEN
                    self._opened_at = time.monotonic()

    async def reset(self) -> None:
        """Force the breaker back to CLOSED with zero counters.

        Used by tests and ops-level recovery — bypasses every state
        transition rule and unconditionally returns the breaker to a
        fresh state.
        """
        async with self._lock:
            self._state = CircuitState.CLOSED
            self._failure_count = 0
            self._success_count = 0
            self._opened_at = 0.0

    async def get_stats(self) -> dict[str, Any]:
        """Return a JSON-serialisable snapshot of the breaker's state.

        Returns:
            Dict with ``name``, ``state``, ``failure_count``,
            ``success_count``, ``time_until_retry``, ``backend``.
        """
        async with self._lock:
            await self._maybe_half_open()
            remaining = await self._time_until_retry_unlocked()
            return {
                "name": self._name,
                "state": self._state.value,
                "failure_count": self._failure_count,
                "success_count": self._success_count,
                "time_until_retry": remaining,
                "backend": "memory",
            }

    async def time_until_retry(self) -> float:
        """Seconds until the breaker transitions OPEN → HALF_OPEN.

        Returns:
            Remaining recovery time; ``0.0`` when not OPEN.
        """
        async with self._lock:
            return await self._time_until_retry_unlocked()

    async def _time_until_retry_unlocked(self) -> float:
        """Compute the recovery countdown without acquiring the lock.

        Expected to be called from inside an ``async with self._lock``
        block; pulled out so other locked methods don't double-acquire.

        Returns:
            Remaining recovery time; ``0.0`` when not OPEN.
        """
        if self._state != CircuitState.OPEN:
            return 0.0
        elapsed = time.monotonic() - self._opened_at
        return max(0.0, self._config.recovery_timeout - elapsed)


class InMemoryRegistry(BaseCircuitBreakerRegistry):
    """Per-process registry of in-memory breakers."""

    def __init__(self, default_config: CircuitBreakerConfig | None = None) -> None:
        """Start an empty registry with a default config for new breakers.

        Args:
            default_config: Fallback config used when
                :meth:`get_or_create` is called without an explicit one.
        """
        self._breakers: dict[str, InMemoryCircuitBreaker] = {}
        self._default_config = default_config or CircuitBreakerConfig()
        self._lock = asyncio.Lock()

    async def get_or_create(
        self,
        name: str,
        config: CircuitBreakerConfig | None = None,
    ) -> InMemoryCircuitBreaker:
        """Return the existing breaker for ``name`` or create a fresh one.

        Args:
            name: Breaker identifier (e.g. ``"bhn_api"``).
            config: Optional override; falls back to the registry's
                default config.

        Returns:
            An ``InMemoryCircuitBreaker``.
        """
        existing = self._breakers.get(name)
        if existing is not None:
            return existing
        async with self._lock:
            if name in self._breakers:
                return self._breakers[name]
            self._breakers[name] = InMemoryCircuitBreaker(
                breaker_name=name,
                config=config or self._default_config,
            )
            return self._breakers[name]

    async def remove(self, name: str) -> None:
        """Drop the breaker for ``name`` (no-op if missing).

        Args:
            name: Breaker identifier to forget.
        """
        async with self._lock:
            self._breakers.pop(name, None)

    async def get_all_stats(self) -> dict[str, dict[str, Any]]:
        """Return a ``{name: stats}`` mapping for every registered breaker.

        Returns:
            Mapping suitable for an admin / health endpoint.
        """
        async with self._lock:
            breakers = list(self._breakers.items())
        return {name: await b.get_stats() for name, b in breakers}

    async def reset_all(self) -> None:
        """Reset every registered breaker to CLOSED with zero counters.

        Snapshots the breaker list under the registry lock, then resets
        each one outside the lock so a slow reset on one breaker does
        not block adds/removes elsewhere.
        """
        async with self._lock:
            breakers = list(self._breakers.values())
        for breaker in breakers:
            await breaker.reset()

    async def clear(self) -> None:
        """Drop every breaker from the registry (in-memory only).

        Unlike :meth:`reset_all` this discards the breaker objects
        themselves — useful for test teardown so the next test starts
        from an empty registry.
        """
        async with self._lock:
            self._breakers.clear()

    @property
    def backend_name(self) -> str:
        """Identify the in-memory registry to the readyz probe.

        Returns:
            The literal ``"memory"``.
        """
        return "memory"

    async def is_healthy(self) -> bool:
        """Return ``True`` — the in-memory registry is always operational.

        The probe exists so :func:`breaker_check` can report a
        consistent backend label; an in-memory registry never has a
        backing store to fail.

        Returns:
            ``True`` unconditionally.
        """
        return True
