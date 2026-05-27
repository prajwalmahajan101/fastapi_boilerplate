"""In-process sliding-window throttle — deque per identifier.

Per-process state — fine for single-worker deployments and as a
fail-open fallback when Redis is unavailable, but not safe across
multiple workers.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import Deque

from src.core.resilience.throttle.base import BaseThrottle, ThrottleResult


class InMemoryThrottle(BaseThrottle):
    """Sliding-window rate limiter keyed by identifier."""

    def __init__(self) -> None:
        """Initialise the InMemoryThrottle."""
        self._windows: dict[str, Deque[float]] = {}
        self._lock = asyncio.Lock()

    async def check(
        self,
        identifier: str,
        *,
        limit: int,
        window_seconds: int,
    ) -> ThrottleResult:
        """Sliding-window decision held in a per-identifier deque of timestamps.

        Args:
            identifier: Throttle bucket key.
            limit: Maximum allowed requests in the window.
            window_seconds: Rolling window duration in seconds.

        Returns:
            ``ThrottleResult`` describing whether the request is
            allowed plus retry/quota metadata.
        """
        now = time.time()
        cutoff = now - window_seconds
        async with self._lock:
            window = self._windows.setdefault(identifier, deque())
            while window and window[0] < cutoff:
                window.popleft()
            current = len(window)
            if current >= limit:
                oldest = window[0]
                retry_after = max(0.0, oldest + window_seconds - now)
                return ThrottleResult(
                    allowed=False,
                    limit=limit,
                    remaining=0,
                    reset_at=int(oldest + window_seconds),
                    retry_after=retry_after,
                )
            window.append(now)
            return ThrottleResult(
                allowed=True,
                limit=limit,
                remaining=limit - (current + 1),
                reset_at=int(now + window_seconds),
                retry_after=0.0,
            )

    async def is_healthy(self) -> bool:
        """In-process backends have no remote dependency to probe.

        Returns:
            Always ``True`` — an in-memory throttle cannot become
            unreachable.
        """
        return True

    @property
    def backend_name(self) -> str:
        """Identify this backend for the readiness probe.

        Returns:
            The fixed string ``"memory"``.
        """
        return "memory"
