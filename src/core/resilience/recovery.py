"""Background recovery monitor for Redis-backed resilience helpers.

Three subsystems (`cache`, `throttle`, `circuit_breaker`) fail-open to an
embedded in-memory backend when Redis goes away. Each one already has
two recovery paths of its own:

* an **in-call probe** that re-pings Redis at most once per
  :data:`RedisCacheBackend._RECOVERY_PROBE_INTERVAL_S`-style window
  whenever a request lands on the degraded code path;
* an :meth:`is_healthy` probe used by ``/readyz`` to clear the sticky
  fallback flag on a successful ``PING``.

What is missing is a path that recovers a backend **on a quiet worker**
— one that is degraded but currently servicing no traffic and whose
``/readyz`` is not being polled (worker-only deployments, sidecar
containers, scheduled-task workers). Without recovery on idle workers,
a 30-second Redis blip can leave half the fleet on per-worker counters
indefinitely while the other half (the workers serving real traffic)
silently recover. That is the split-brain this module exists to close.

Design
------
* **`RecoverableBackend` Protocol** — a Redis-backed backend opts into
  recovery by exposing ``alias`` (stable identifier, e.g.
  ``"cache:default"``), an ``health`` property
  (:class:`BackendHealth`), and an ``async try_recover()`` that returns
  ``True`` exactly when this call flipped the backend from
  ``DEGRADED`` to ``ACTIVE``. Implementations may be throttled
  internally — repeated calls during the same probe window are
  no-ops.
* **Registry** — backends call :func:`register_for_recovery` from their
  constructor. The registry holds references for the lifetime of the
  process; providers replace entries (rather than mutating in place)
  whenever they rebuild a backend from boot-fallback.
* **Warm hooks** — :func:`register_warm_hook` adds an async callable
  that runs after **any** backend successfully re-attaches. Concrete
  consumers (cache primers, session-token reprimers) register here so a
  hot service does not take a cold-cache hit immediately after Redis
  recovers.
* **`RedisRecoveryMonitor`** — a single asyncio task per process,
  spawned during the FastAPI lifespan. It sleeps cheaply while every
  backend is ``ACTIVE``; once any backend goes ``DEGRADED`` it ``PING``s
  Redis on the configured interval and waits for a stable success
  window (:data:`_STABLE_WINDOW_SUCCESSES` consecutive successes)
  before driving recovery. The stable window keeps a flaky Redis from
  flapping every backend in the registry.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any, Protocol, runtime_checkable

from src.core.resilience.health import BackendHealth

logger = logging.getLogger(__name__)


# Probe cadence and stability window. Kept short enough that recovery
# lands inside a typical readiness-window deadline (≤1 min) but long
# enough that a flapping Redis cannot pin the monitor at 100 % CPU.
_PROBE_INTERVAL_SECONDS = 10.0
_STABLE_WINDOW_SUCCESSES = 3


@runtime_checkable
class RecoverableBackend(Protocol):
    """Contract a Redis-backed backend opts into to receive recovery.

    Duck-typed via ``Protocol`` so cache / throttle / breaker
    implementations stay decoupled from this module — they need not
    subclass anything.
    """

    alias: str
    """Stable identifier (``cache:default`` / ``throttle:default`` /
    ``breaker:default``). Used by :func:`reset_backend` to dispatch a
    boot-fallback rebuild to the correct provider."""

    @property
    def health(self) -> BackendHealth:  # pragma: no cover - structural
        ...

    async def try_recover(self) -> bool:  # pragma: no cover - structural
        """Re-attach a ``DEGRADED`` backend if Redis is reachable.

        Returns:
            ``True`` exactly when this call flipped the internal state
            from ``DEGRADED`` to ``ACTIVE``. ``False`` for an already-
            ``ACTIVE`` backend or a probe that still found Redis down.
        """
        ...


# Strong references — backends are process-lifetime singletons in this
# codebase; weakrefs would buy nothing and complicate the test reset
# path. Replacement (boot-fallback rebuild) is handled by providers via
# :func:`reset_backend` which unregisters the old InMemory* and the new
# Redis backend re-registers itself in its constructor.
_registry_lock = asyncio.Lock()
_registry: list[RecoverableBackend] = []
_warm_hooks: list[Callable[[], Awaitable[None]]] = []
# Boot-fallback aliases — bare InMemory backends returned by providers
# when the very first PING failed. They do not implement
# RecoverableBackend (no Redis client to probe), so the monitor pings
# the configured Redis alias directly and dispatches reset_backend on
# success.
_boot_fallback_aliases: set[str] = set()


def register_for_recovery(backend: RecoverableBackend) -> None:
    """Add ``backend`` to the recovery monitor's worklist.

    Idempotent — re-registering the same instance is a no-op. Backends
    call this from ``__init__`` so they participate in recovery from
    the moment they exist.

    Args:
        backend: A Redis-backed backend that implements
            :class:`RecoverableBackend`.
    """
    for existing in _registry:
        if existing is backend:
            return
    _registry.append(backend)
    logger.debug(
        "Registered backend for recovery monitor",
        extra={"event": "backend_register", "alias": getattr(backend, "alias", "?")},
    )


def unregister_for_recovery(backend: RecoverableBackend) -> None:
    """Remove ``backend`` from the registry (used by provider rebuilds)."""
    _registry[:] = [b for b in _registry if b is not backend]


def registered_backends() -> list[RecoverableBackend]:
    """Snapshot the live registered backends (defensive copy)."""
    return list(_registry)


def register_boot_fallback(alias: str) -> None:
    """Track ``alias`` as a bare-InMemory backend that needs a Redis rebuild.

    Providers call this whenever their first ``PING`` failed and they
    cached an in-memory backend that has no Redis client of its own.
    The monitor pings ``alias`` directly and triggers
    :func:`reset_backend` on success so the next ``get_*`` call rebuilds
    against the now-live Redis.
    """
    _boot_fallback_aliases.add(alias)


def clear_boot_fallback(alias: str) -> None:
    """Drop ``alias`` from the boot-fallback set (used after a rebuild)."""
    _boot_fallback_aliases.discard(alias)


def boot_fallback_aliases() -> set[str]:
    """Snapshot the boot-fallback alias set (defensive copy)."""
    return set(_boot_fallback_aliases)


def register_warm_hook(hook: Callable[[], Awaitable[None]]) -> None:
    """Add an async callable to run after any DEGRADED backend re-attaches.

    Idempotent — registering the same callable twice is a no-op.
    """
    if hook not in _warm_hooks:
        _warm_hooks.append(hook)


async def _run_warm_hooks() -> None:
    """Invoke every registered warm hook; isolate failures."""
    for hook in list(_warm_hooks):
        try:
            await hook()
        except Exception:  # noqa: BLE001 — warm hooks must not crash recovery
            logger.exception("Warm hook failed during recovery")


async def reset_backend(alias: str) -> bool:
    """Dispatch a boot-fallback rebuild to the correct provider.

    Args:
        alias: Stable identifier — one of ``cache:<name>`` /
            ``throttle:<name>`` / ``breaker:<name>``.

    Returns:
        ``True`` if a rebuild was dispatched; ``False`` if the alias
        prefix is unknown.
    """
    if alias.startswith("cache:"):
        from src.core.resilience.cache import provider as cache_provider

        await cache_provider.reset_backend(alias.removeprefix("cache:"))
        clear_boot_fallback(alias)
        return True
    if alias.startswith("throttle:"):
        from src.core.resilience.throttle import provider as throttle_provider

        await throttle_provider.reset_backend()
        clear_boot_fallback(alias)
        return True
    if alias.startswith("breaker:"):
        from src.core.resilience.circuit_breaker import provider as breaker_provider

        await breaker_provider.reset_backend()
        clear_boot_fallback(alias)
        return True
    logger.warning("reset_backend: unknown alias prefix %r", alias)
    return False


async def _ping_alias(alias: str) -> bool:
    """Best-effort ``PING`` against the Redis client behind ``alias``.

    Used to decide whether a bare in-memory boot-fallback backend can
    be safely torn down.
    """
    try:
        from src.core.utils.redis import get_redis_client

        client = await get_redis_client(alias)
        return bool(await client.ping())
    except Exception:  # noqa: BLE001
        return False


async def attempt_recover_all() -> int:
    """Drive recovery for every registered backend + boot-fallback alias.

    Returns:
        The number of backends/aliases that were recovered on this call.
    """
    recovered = 0

    for backend in registered_backends():
        try:
            health = backend.health
        except Exception:  # noqa: BLE001
            continue
        if health is BackendHealth.ACTIVE:
            continue
        try:
            if await backend.try_recover():
                recovered += 1
        except Exception:  # noqa: BLE001
            logger.exception(
                "try_recover raised for %s", getattr(backend, "alias", "?")
            )

    for alias in boot_fallback_aliases():
        # Strip subsystem prefix to get the Redis client alias.
        for prefix in ("cache:", "throttle:", "breaker:"):
            if alias.startswith(prefix):
                redis_alias = alias.removeprefix(prefix)
                break
        else:
            continue
        if await _ping_alias(redis_alias) and await reset_backend(alias):
            recovered += 1

    if recovered:
        await _run_warm_hooks()
    return recovered


def _any_degraded() -> bool:
    """Cheap check: is anything in the registry non-ACTIVE *or* boot-fallback?"""
    if _boot_fallback_aliases:
        return True
    for backend in _registry:
        try:
            if backend.health is not BackendHealth.ACTIVE:
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


class RedisRecoveryMonitor:
    """Background asyncio task that drives recovery when Redis returns.

    The task is a no-op while every registered backend is ``ACTIVE`` —
    it sleeps between cheap state checks instead of hammering Redis.
    When something goes non-``ACTIVE`` it transitions to active ``PING``
    mode and waits for :data:`_STABLE_WINDOW_SUCCESSES` consecutive
    successful pings against the first registered alias before driving
    recovery, so a flapping Redis cannot whip every backend in the
    registry on each tick.
    """

    def __init__(
        self,
        *,
        probe_interval_seconds: float = _PROBE_INTERVAL_SECONDS,
        ping_alias: str = "default",
    ) -> None:
        """Configure the monitor (does not start it).

        Args:
            probe_interval_seconds: Cadence between ticks. Same value
                whether the loop is in the idle (all-ACTIVE) state or
                the active probing state — keeps the loop predictable
                under test.
            ping_alias: Redis alias used for the stable-window probe.
                Defaults to ``"default"`` which every shipped subsystem
                shares.
        """
        self._probe_interval = probe_interval_seconds
        self._ping_alias = ping_alias
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        # Successes since last failure — must reach
        # ``_STABLE_WINDOW_SUCCESSES`` before we trust Redis enough to
        # drive recovery.
        self._consecutive_successes = 0

    def start(self) -> None:
        """Spawn the monitor task. Idempotent — safe to call twice."""
        if self._task is not None and not self._task.done():
            return
        self._stop.clear()
        self._consecutive_successes = 0
        self._task = asyncio.create_task(
            self._run(), name="redis-recovery-monitor"
        )
        logger.info("RedisRecoveryMonitor started")

    async def stop(self, timeout: float = 5.0) -> None:
        """Signal the monitor to stop and await its task.

        Args:
            timeout: Seconds to wait for the task to exit before
                cancelling it.
        """
        self._stop.set()
        task = self._task
        if task is None:
            return
        try:
            await asyncio.wait_for(task, timeout=timeout)
        except asyncio.TimeoutError:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        finally:
            self._task = None
            logger.info("RedisRecoveryMonitor stopped")

    async def _run(self) -> None:
        """Main loop — see class docstring for the state machine."""
        while not self._stop.is_set():
            try:
                if not _any_degraded():
                    self._consecutive_successes = 0
                    await self._sleep_or_stop(self._probe_interval)
                    continue

                if await _ping_alias(self._ping_alias):
                    self._consecutive_successes += 1
                else:
                    self._consecutive_successes = 0

                if self._consecutive_successes >= _STABLE_WINDOW_SUCCESSES:
                    recovered = await attempt_recover_all()
                    if recovered:
                        logger.info(
                            "RedisRecoveryMonitor recovered %d backend(s)",
                            recovered,
                            extra={
                                "event": "redis_recovery",
                                "recovered": recovered,
                            },
                        )
                    # Reset the window so the next tick does not
                    # re-trigger on residual successes.
                    self._consecutive_successes = 0
            except Exception:  # noqa: BLE001 — never crash the loop
                logger.exception("RedisRecoveryMonitor loop iteration failed")
            await self._sleep_or_stop(self._probe_interval)

    async def _sleep_or_stop(self, seconds: float) -> None:
        """Sleep for ``seconds`` but wake immediately on stop."""
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            return


# Module-level singleton — process-scoped. ``start()`` from the
# FastAPI lifespan; ``await stop()`` on shutdown.
monitor = RedisRecoveryMonitor()


async def reset_recovery_state() -> None:
    """Test helper — drop every registered backend / hook / boot alias.

    The unit-test conftest resets singletons between cases via
    ``reset_all_singletons``; this helper is added to that path so the
    recovery registry is also cleaned up.
    """
    async with _registry_lock:
        _registry.clear()
        _warm_hooks.clear()
        _boot_fallback_aliases.clear()


__all__ = [
    "RecoverableBackend",
    "RedisRecoveryMonitor",
    "attempt_recover_all",
    "boot_fallback_aliases",
    "clear_boot_fallback",
    "monitor",
    "register_boot_fallback",
    "register_for_recovery",
    "register_warm_hook",
    "registered_backends",
    "reset_backend",
    "reset_recovery_state",
    "unregister_for_recovery",
]
