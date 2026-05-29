"""Exemplar integration test — exercises ``RedisThrottle`` against real Redis.

Copy this shape for any new integration test that needs to drive **one
layer** through a real backing store. The points worth lifting:

* Take the ``redis_client`` (or ``pg_engine``) fixture — it auto-skips
  when the service is unreachable, so the test stays green on a
  workstation that hasn't booted the compose stack.
* Construct the production class against the real client. Don't unit-
  test the Lua script in isolation — the *point* of an integration
  test is to prove the round-trip works.
* Assert on **observable side effects** in the store (key present,
  TTL set, count incremented) plus the return value. That's what
  separates an integration test from a unit test.

Bring services up locally with::

    docker compose up redis -d
    pytest -m integration tests/integration/resilience/test_throttle_redis_exemplar.py
"""

from __future__ import annotations

from typing import Any

import pytest

from src.core.resilience.throttle import RedisThrottle


@pytest.mark.asyncio
async def test_redis_throttle_records_request_in_redis(redis_client: Any) -> None:
    """One ``check()`` writes one entry under the configured key prefix."""
    throttle = await RedisThrottle.create(redis_client, key_prefix="exemplar")

    result = await throttle.check("user-1", limit=5, window_seconds=60)

    assert result.allowed is True
    assert result.remaining == 4

    key = "exemplar:user-1"
    assert await redis_client.zcard(key) == 1
    ttl_ms = await redis_client.pttl(key)
    assert 0 < ttl_ms <= 60_000


@pytest.mark.asyncio
async def test_redis_throttle_denies_once_limit_exceeded(redis_client: Any) -> None:
    """The ``limit + 1`` call is denied and carries a positive ``retry_after``."""
    throttle = await RedisThrottle.create(redis_client, key_prefix="exemplar")

    for _ in range(3):
        await throttle.check("user-2", limit=3, window_seconds=60)
    denied = await throttle.check("user-2", limit=3, window_seconds=60)

    assert denied.allowed is False
    assert denied.remaining == 0
    assert denied.retry_after > 0.0
