"""Unit tests for ``InMemoryThrottle.check_fixed_window``.

The in-memory fallback delegates to the sliding-window ``check`` so the
allow/deny answer must be identical to the per-identifier path; the
test pins that contract.
"""

from __future__ import annotations

import pytest

from src.core.resilience.throttle.memory_impl import InMemoryThrottle


@pytest.fixture
def throttle() -> InMemoryThrottle:
    return InMemoryThrottle()


async def test_first_call_is_allowed(throttle: InMemoryThrottle) -> None:
    result = await throttle.check_fixed_window(
        "global:outbound", limit=3, window_seconds=60
    )
    assert result.allowed is True
    assert result.remaining == 2
    assert result.retry_after == 0.0


async def test_limit_blocks_extra_calls(throttle: InMemoryThrottle) -> None:
    for _ in range(3):
        await throttle.check_fixed_window("global:outbound", limit=3, window_seconds=60)
    blocked = await throttle.check_fixed_window(
        "global:outbound", limit=3, window_seconds=60
    )
    assert blocked.allowed is False
    assert blocked.remaining == 0
    assert blocked.retry_after > 0


async def test_check_and_check_fixed_window_share_buckets(
    throttle: InMemoryThrottle,
) -> None:
    """In-memory: both APIs are the same path, so they share the bucket."""
    await throttle.check("global:outbound", limit=2, window_seconds=60)
    await throttle.check_fixed_window("global:outbound", limit=2, window_seconds=60)
    blocked = await throttle.check_fixed_window(
        "global:outbound", limit=2, window_seconds=60
    )
    assert blocked.allowed is False
