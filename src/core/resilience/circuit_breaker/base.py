"""Abstract circuit breaker interface.

Concrete implementations (Redis, in-memory) must implement the same
interface so the rest of the system can swap backends transparently.
"""

from __future__ import annotations

import asyncio
import inspect
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class CircuitState(StrEnum):
    """Circuit breaker states (matches Redis Lua script tokens)."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass(frozen=True)
class CircuitBreakerConfig:
    """Per-breaker behaviour knobs."""

    failure_threshold: int = 5
    """Consecutive failures before the circuit opens."""

    success_threshold: int = 2
    """Successes in HALF_OPEN required to return to CLOSED."""

    recovery_timeout: float = 30.0
    """Seconds in OPEN before allowing a probe (HALF_OPEN)."""

    excluded_exceptions: tuple[type[Exception], ...] = ()
    """Exceptions that do not count as failures."""


class BaseCircuitBreaker(ABC):
    """Async circuit breaker contract."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Stable identifier for stats / logging.

        Returns:
            The configured breaker name (e.g. ``"bhn_api"``).
        """

    @abstractmethod
    async def is_available(self) -> bool:
        """Return whether the breaker is not in OPEN state.

        Returns:
            ``True`` if the breaker is CLOSED or HALF_OPEN.
        """

    @abstractmethod
    async def record_success(self) -> None:
        """Note a successful call."""

    @abstractmethod
    async def record_failure(self, exc: Exception | None = None) -> None:
        """Note a failed call (exceptions matching ``excluded_exceptions`` are ignored).

        Args:
            exc: The exception that triggered the failure; used to
                consult ``excluded_exceptions``.
        """

    @abstractmethod
    async def reset(self) -> None:
        """Force the breaker back to CLOSED with zero counters."""

    @abstractmethod
    async def get_stats(self) -> dict[str, Any]:
        """Return a snapshot for monitoring.

        Returns:
            JSON-serialisable dict with ``state``, counters, and
            recovery info.
        """

    @abstractmethod
    async def time_until_retry(self) -> float:
        """Seconds until the next probe attempt (0 if not OPEN).

        Returns:
            Remaining recovery time in seconds.
        """

    async def call(self, func: Any, *args: Any, **kwargs: Any) -> Any:
        """Execute ``func`` through the breaker — async-aware.

        Raises ``ServiceUnavailableError`` if the breaker is OPEN.
        Otherwise runs ``func`` (awaiting if coroutine), records
        success/failure, and re-raises the original exception on
        failure.

        Args:
            func: Callable to dispatch (sync or async).
            *args: Positional arguments forwarded to ``func``.
            **kwargs: Keyword arguments forwarded to ``func``.

        Returns:
            Whatever ``func`` returned.

        Raises:
            ServiceUnavailableError: When the breaker is OPEN.
            Exception: Propagated from ``func`` after recording failure.
        """
        from src.core.exceptions.infrastructure import ServiceUnavailableError

        if not await self.is_available():
            remaining = await self.time_until_retry()
            raise ServiceUnavailableError(
                self.name,
                message=(
                    f"Service '{self.name}' is unavailable (circuit open). "
                    f"Retry in {remaining:.1f}s."
                ),
            )

        try:
            result = func(*args, **kwargs)
            if inspect.iscoroutine(result):
                result = await result
            await self.record_success()
            return result
        except Exception as exc:
            await self.record_failure(exc)
            raise


class BaseCircuitBreakerRegistry(ABC):
    """Async registry of breakers, keyed by service name."""

    @abstractmethod
    async def get_or_create(
        self,
        name: str,
        config: CircuitBreakerConfig | None = None,
    ) -> BaseCircuitBreaker:
        """Return an existing breaker or create one with ``config``.

        Args:
            name: Breaker identifier.
            config: Optional override; concrete registries fall back to
                their default config.

        Returns:
            A ``BaseCircuitBreaker`` (created on first call for ``name``).
        """

    @abstractmethod
    async def remove(self, name: str) -> None:
        """Drop the breaker for ``name``.

        Args:
            name: Breaker identifier to forget.
        """

    @abstractmethod
    async def get_all_stats(self) -> dict[str, dict[str, Any]]:
        """Stats for every registered breaker.

        Returns:
            Mapping ``{name: stats_dict}``.
        """

    @abstractmethod
    async def reset_all(self) -> None:
        """Reset every breaker."""

    @abstractmethod
    async def clear(self) -> None:
        """Remove every breaker (storage may persist until TTL)."""

    @property
    @abstractmethod
    def backend_name(self) -> str:
        """Label identifying which path is serving breaker operations.

        Mirrors :attr:`BaseCacheBackend.backend_name` and
        :attr:`BaseThrottle.backend_name` so the readyz probe can
        report a consistent vocabulary across every Redis-primary
        resilience helper.

        Returns:
            ``"redis"`` when the registry is wrapping a live Redis
            client, ``"memory"`` when the registry is the bare
            in-memory fallback.
        """

    @abstractmethod
    async def is_healthy(self) -> bool:
        """Best-effort probe of the registry's underlying backend.

        Wired into the readiness endpoint alongside
        :func:`cache.provider.reset_backend` /
        :func:`throttle.provider.reset_backend`. A failure here lets
        ``/readyz`` report a degraded breaker registry without
        affecting in-flight call dispatch (the per-breaker
        ``_health`` flag inside :class:`RedisCircuitBreaker` already
        handles per-call recovery).

        Returns:
            ``True`` when the backing store responds to a probe;
            ``False`` otherwise. In-memory registries always return
            ``True`` — the probe only exists so the readyz body can
            report the current backend label.
        """


# Suppress a noisy ``unused-import`` warning in some linters — the ABC
# methods are async, but importers may run code that uses ``asyncio``.
_ = asyncio
