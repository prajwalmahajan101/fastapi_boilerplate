"""``drain_all`` honours its timeout as a TOTAL budget across queues."""

from __future__ import annotations

import asyncio
import time

import pytest

from src.core.utils.fire_and_forget import (
    FireAndForgetQueue,
    _reset_registry,
    drain_all,
    register,
)


@pytest.fixture(autouse=True)
def _reset():
    _reset_registry()
    yield
    _reset_registry()


@pytest.mark.asyncio
async def test_drain_all_empty_returns_true():
    assert await drain_all(timeout=0.1) is True


@pytest.mark.asyncio
async def test_drain_all_within_budget_returns_true():
    q = register(FireAndForgetQueue(name="quick"))

    async def quick():
        await asyncio.sleep(0.01)

    q.submit(quick())
    assert await drain_all(timeout=1.0) is True


@pytest.mark.asyncio
async def test_drain_all_exceeds_budget_returns_false_total():
    # Two queues each holding a 2s-long task — with a 0.3s total
    # budget the call must return False and finish near the budget,
    # not 2 * 2s == 4s.
    qa = register(FireAndForgetQueue(name="a"))
    qb = register(FireAndForgetQueue(name="b"))

    async def slow():
        await asyncio.sleep(2.0)

    qa.submit(slow())
    qb.submit(slow())

    started = time.monotonic()
    drained = await drain_all(timeout=0.3)
    elapsed = time.monotonic() - started

    assert drained is False
    # Total wall time must be bounded by the budget (plus a small
    # scheduling fudge), not N × budget.
    assert elapsed < 1.0, f"drain_all exceeded total budget: {elapsed:.2f}s"
