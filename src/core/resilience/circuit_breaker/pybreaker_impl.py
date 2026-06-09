"""Pybreaker-backed circuit breaker tier (per-process, sync core).

Wraps the well-tuned ``pybreaker`` library behind the async
:class:`BaseCircuitBreaker` interface. Selected by setting
``circuit_breaker_backend="pybreaker"``; the provider then skips the
Redis probe entirely and constructs a :class:`PyBreakerRegistry`
directly.

Trade-off vs. :class:`InMemoryCircuitBreaker`: pybreaker brings
battle-tested state-machine semantics (half-open probe gating,
listener hooks for metrics) at the cost of running its internal
counters under a threading lock instead of an ``asyncio.Lock``.
Acceptable because the operations are pure in-memory state mutations
that complete in microseconds — wrapping them in ``asyncio.Lock``
would buy nothing.

State is per-process. In a gunicorn / uvicorn multi-worker
deployment, each worker maintains independent breaker state — pick
the Redis backend when you need shared state across workers.
"""

from __future__ import annotations

import logging
import time
from threading import RLock
from typing import TYPE_CHECKING, Any

from src.core.resilience.circuit_breaker.base import (
    BaseCircuitBreaker,
    BaseCircuitBreakerRegistry,
    CircuitBreakerConfig,
)

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


def _pybreaker():
    """Import pybreaker lazily so the dep stays optional.

    Raises:
        RuntimeError: When pybreaker is not installed but the backend
            has been selected.
    """
    try:
        import pybreaker  # noqa: PLC0415

        return pybreaker
    except ImportError as exc:  # noqa: BLE001
        raise RuntimeError(
            "pybreaker is required for circuit_breaker_backend='pybreaker' "
            "— install the runtime dependency or pick another backend."
        ) from exc


class PyBreakerCircuitBreaker(BaseCircuitBreaker):
    """Pybreaker wrapper that satisfies the async breaker contract."""

    def __init__(self, breaker_name: str, config: CircuitBreakerConfig) -> None:
        """Build a breaker delegating to ``pybreaker.CircuitBreaker``.

        Args:
            breaker_name: Stable identifier used in stats / logs.
            config: Threshold + recovery settings.
        """
        self._name = breaker_name
        self._config = config
        self._opened_at: float = 0.0
        pybreaker = _pybreaker()
        self._pybreaker_mod = pybreaker
        self._breaker = pybreaker.CircuitBreaker(
            fail_max=config.failure_threshold,
            reset_timeout=config.recovery_timeout,
            name=breaker_name,
            exclude=list(config.excluded_exceptions),
        )

    @property
    def name(self) -> str:
        return self._name

    async def is_available(self) -> bool:
        """Return whether a call would clear the breaker right now.

        Pybreaker transitions OPEN → HALF_OPEN only on the next
        ``.call()`` — its ``current_state`` does not auto-probe. To
        keep the contract aligned with :class:`InMemoryCircuitBreaker`
        we treat "open but reset timeout elapsed" as available, so the
        base ``call`` helper lets the dispatch through and pybreaker
        then drives the half-open probe internally.
        """
        if self._breaker.current_state != "open":
            return True
        if not self._opened_at:
            return False
        return (time.monotonic() - self._opened_at) >= self._breaker.reset_timeout

    async def record_success(self) -> None:
        """Note a successful call, advancing OPEN/HALF_OPEN → CLOSED when stable."""
        state = self._breaker.current_state
        if state == "open":
            # ``is_available`` may have reported True because the reset
            # timeout has elapsed, but pybreaker only transitions on its
            # own ``.call()``. Drive a no-op probe through pybreaker so
            # the state machine catches up to reality.
            if (
                self._opened_at
                and (time.monotonic() - self._opened_at) >= self._breaker.reset_timeout
            ):
                try:
                    self._breaker.call(lambda: None)
                except self._pybreaker_mod.CircuitBreakerError:
                    return
                if self._breaker.current_state == "closed":
                    self._opened_at = 0.0
            return
        if state == "half-open":
            try:
                self._breaker.call(lambda: None)
            except self._pybreaker_mod.CircuitBreakerError:
                pass
            if self._breaker.current_state == "closed":
                self._opened_at = 0.0
            return
        # Closed: reset the failure counter via the only stable API.
        self._breaker._fail_counter = 0  # noqa: SLF001

    async def record_failure(self, exc: Exception | None = None) -> None:
        """Increment failure count and trip OPEN past the threshold."""
        if exc is not None and isinstance(exc, self._config.excluded_exceptions):
            return

        state = self._breaker.current_state
        if state == "open":
            # Already open — pybreaker's internal timer is the source of
            # truth for the half-open transition. Don't refresh
            # _opened_at; it would drift from pybreaker's timer.
            return

        from src.core.exceptions.infrastructure import (  # noqa: PLC0415
            ServiceUnavailableError,
        )

        def _fail() -> None:
            raise exc if exc is not None else ServiceUnavailableError(self._name)

        try:
            self._breaker.call(_fail)
        except self._pybreaker_mod.CircuitBreakerError:
            pass
        except Exception:  # noqa: BLE001
            # The synthesised failure pybreaker just re-raised — expected.
            pass

        if self._breaker.current_state == "open":
            self._opened_at = time.monotonic()

    async def reset(self) -> None:
        """Force the breaker back to CLOSED with zero counters."""
        self._breaker.close()
        self._opened_at = 0.0

    async def get_stats(self) -> dict[str, Any]:
        return {
            "name": self._name,
            "state": self._breaker.current_state,
            "failure_count": self._breaker.fail_counter,
            "success_count": getattr(self._breaker, "success_counter", 0),
            "time_until_retry": await self.time_until_retry(),
            "backend": "pybreaker",
        }

    async def time_until_retry(self) -> float:
        if self._breaker.current_state != "open":
            return 0.0
        if self._opened_at:
            elapsed = time.monotonic() - self._opened_at
            return max(0.0, self._breaker.reset_timeout - elapsed)
        return float(self._breaker.reset_timeout)


class PyBreakerRegistry(BaseCircuitBreakerRegistry):
    """Per-process registry of pybreaker-backed breakers."""

    def __init__(self, default_config: CircuitBreakerConfig | None = None) -> None:
        """Initialise an empty registry.

        Args:
            default_config: Fallback config used when
                :meth:`get_or_create` is called without an explicit one.
        """
        self._breakers: dict[str, PyBreakerCircuitBreaker] = {}
        self._lock = RLock()
        self._default_config = default_config or CircuitBreakerConfig()

    async def get_or_create(
        self,
        name: str,
        config: CircuitBreakerConfig | None = None,
    ) -> PyBreakerCircuitBreaker:
        existing = self._breakers.get(name)
        if existing is not None:
            return existing
        with self._lock:
            if name not in self._breakers:
                self._breakers[name] = PyBreakerCircuitBreaker(
                    breaker_name=name,
                    config=config or self._default_config,
                )
            return self._breakers[name]

    async def remove(self, name: str) -> None:
        with self._lock:
            self._breakers.pop(name, None)

    async def get_all_stats(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            breakers = list(self._breakers.items())
        return {name: await b.get_stats() for name, b in breakers}

    async def reset_all(self) -> None:
        with self._lock:
            breakers = list(self._breakers.values())
        for breaker in breakers:
            await breaker.reset()

    async def clear(self) -> None:
        with self._lock:
            self._breakers.clear()

    @property
    def backend_name(self) -> str:
        """Identify the registry to the readyz probe."""
        return "pybreaker"

    async def is_healthy(self) -> bool:
        """Always-healthy probe — pybreaker has no backing store to fail."""
        return True


__all__ = ["PyBreakerCircuitBreaker", "PyBreakerRegistry"]
