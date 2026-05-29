"""Unit tests for ``src.core.resilience.recovery``.

The monitor is wired to live Redis in integration tests; this module
covers the state machine and the registry contract in isolation with
in-process fakes. No network, no asyncio sleeps longer than a few
milliseconds.
"""

from __future__ import annotations

import asyncio

import pytest

from src.core.resilience import recovery as rec
from src.core.resilience.health import BackendHealth


class _FakeBackend:
    """Implements the ``RecoverableBackend`` protocol for tests."""

    def __init__(self, alias: str, *, recoverable: bool) -> None:
        self.alias = alias
        self._health = BackendHealth.DEGRADED
        self._recoverable = recoverable
        self.try_recover_calls = 0

    @property
    def health(self) -> BackendHealth:
        return self._health

    async def try_recover(self) -> bool:
        self.try_recover_calls += 1
        if self._recoverable and self._health is BackendHealth.DEGRADED:
            self._health = BackendHealth.ACTIVE
            return True
        return False


@pytest.fixture(autouse=True)
async def _isolate_registry() -> None:
    """Each test starts with an empty recovery registry."""
    await rec.reset_recovery_state()


async def test_register_and_unregister_is_idempotent() -> None:
    b = _FakeBackend("cache:default", recoverable=True)
    rec.register_for_recovery(b)
    rec.register_for_recovery(b)
    assert rec.registered_backends() == [b]

    rec.unregister_for_recovery(b)
    assert rec.registered_backends() == []


async def test_attempt_recover_all_flips_degraded_backends() -> None:
    healthy = _FakeBackend("cache:default", recoverable=True)
    stuck = _FakeBackend("throttle:default", recoverable=False)
    rec.register_for_recovery(healthy)
    rec.register_for_recovery(stuck)

    recovered = await rec.attempt_recover_all()

    assert recovered == 1
    assert healthy.health is BackendHealth.ACTIVE
    assert stuck.health is BackendHealth.DEGRADED


async def test_attempt_recover_all_runs_warm_hooks_only_on_recovery() -> None:
    calls: list[int] = []

    async def hook() -> None:
        calls.append(1)

    rec.register_warm_hook(hook)
    rec.register_warm_hook(hook)  # idempotent

    # Nothing degraded — hooks should not fire.
    await rec.attempt_recover_all()
    assert calls == []

    backend = _FakeBackend("cache:default", recoverable=True)
    rec.register_for_recovery(backend)
    await rec.attempt_recover_all()
    assert calls == [1]


async def test_warm_hook_failure_does_not_crash_recovery() -> None:
    async def bad_hook() -> None:
        raise RuntimeError("warm hook explosion")

    rec.register_warm_hook(bad_hook)
    backend = _FakeBackend("cache:default", recoverable=True)
    rec.register_for_recovery(backend)

    # Must not raise.
    recovered = await rec.attempt_recover_all()
    assert recovered == 1


async def test_boot_fallback_aliases_get_rebuilt_on_ping(monkeypatch) -> None:
    """When PING succeeds for a boot-fallback alias, ``reset_backend`` runs."""
    rec.register_boot_fallback("cache:default")
    assert "cache:default" in rec.boot_fallback_aliases()

    async def fake_ping(alias: str) -> bool:
        return alias == "default"

    rebuilt: list[str] = []

    async def fake_reset(alias: str) -> bool:
        rebuilt.append(alias)
        rec.clear_boot_fallback(alias)
        return True

    monkeypatch.setattr(rec, "_ping_alias", fake_ping)
    monkeypatch.setattr(rec, "reset_backend", fake_reset)

    recovered = await rec.attempt_recover_all()

    assert recovered == 1
    assert rebuilt == ["cache:default"]
    assert "cache:default" not in rec.boot_fallback_aliases()


async def test_reset_backend_dispatches_on_alias_prefix(monkeypatch) -> None:
    cache_calls: list[str] = []
    throttle_calls: list[bool] = []
    breaker_calls: list[bool] = []

    async def fake_cache_reset(alias: str) -> None:
        cache_calls.append(alias)

    async def fake_throttle_reset() -> None:
        throttle_calls.append(True)

    async def fake_breaker_reset() -> None:
        breaker_calls.append(True)

    import src.core.resilience.cache.provider as cache_provider
    import src.core.resilience.circuit_breaker.provider as breaker_provider
    import src.core.resilience.throttle.provider as throttle_provider

    monkeypatch.setattr(cache_provider, "reset_backend", fake_cache_reset)
    monkeypatch.setattr(throttle_provider, "reset_backend", fake_throttle_reset)
    monkeypatch.setattr(breaker_provider, "reset_backend", fake_breaker_reset)

    assert await rec.reset_backend("cache:default") is True
    assert await rec.reset_backend("throttle:default") is True
    assert await rec.reset_backend("breaker:default") is True
    assert await rec.reset_backend("unknown:thing") is False

    assert cache_calls == ["default"]
    assert throttle_calls == [True]
    assert breaker_calls == [True]


async def test_monitor_start_is_idempotent_and_stop_clean() -> None:
    """Two ``start()`` calls leave exactly one task; ``stop()`` cancels it."""
    monitor = rec.RedisRecoveryMonitor(probe_interval_seconds=0.01)
    monitor.start()
    first = monitor._task
    monitor.start()  # idempotent
    assert monitor._task is first
    assert not first.done()  # type: ignore[union-attr]

    await monitor.stop()
    assert monitor._task is None
    assert first.done()  # type: ignore[union-attr]


async def test_monitor_drives_recovery_after_stable_window(monkeypatch) -> None:
    """Three consecutive successful pings → recovery dispatch + warm hooks."""
    backend = _FakeBackend("cache:default", recoverable=True)
    rec.register_for_recovery(backend)

    pings: list[str] = []

    async def fake_ping(alias: str) -> bool:
        pings.append(alias)
        return True

    monkeypatch.setattr(rec, "_ping_alias", fake_ping)

    monitor = rec.RedisRecoveryMonitor(
        probe_interval_seconds=0.01, ping_alias="default"
    )
    monitor.start()
    # Give the loop enough ticks to: detect degraded, count 3 successes,
    # dispatch recovery. Each tick sleeps probe_interval_seconds, so
    # ~50 ms covers the stable window with margin.
    for _ in range(50):
        if backend.health is BackendHealth.ACTIVE:
            break
        await asyncio.sleep(0.005)
    await monitor.stop()

    assert backend.health is BackendHealth.ACTIVE
    assert len(pings) >= rec._STABLE_WINDOW_SUCCESSES


async def test_monitor_does_not_recover_on_flapping_redis(monkeypatch) -> None:
    """Alternating ping outcomes must not accumulate stable successes."""
    backend = _FakeBackend("cache:default", recoverable=True)
    rec.register_for_recovery(backend)

    counter = {"n": 0}

    async def flaky_ping(alias: str) -> bool:
        counter["n"] += 1
        # success, fail, success, fail, …
        return counter["n"] % 2 == 1

    monkeypatch.setattr(rec, "_ping_alias", flaky_ping)

    monitor = rec.RedisRecoveryMonitor(
        probe_interval_seconds=0.005, ping_alias="default"
    )
    monitor.start()
    await asyncio.sleep(0.1)
    await monitor.stop()

    assert backend.health is BackendHealth.DEGRADED
