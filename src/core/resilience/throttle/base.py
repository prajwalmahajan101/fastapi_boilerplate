"""Async throttle contract + result dataclass."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class ThrottleResult:
    """Per-request decision from a throttle backend."""

    allowed: bool
    limit: int
    remaining: int
    reset_at: int  # unix timestamp
    retry_after: float  # seconds; 0 if allowed


class BaseThrottle(ABC):
    """Async rate limiter — one ``check`` per request decision."""

    @abstractmethod
    async def check(
        self,
        identifier: str,
        *,
        limit: int,
        window_seconds: int,
    ) -> ThrottleResult:
        """Return a decision for ``identifier`` against (``limit``, ``window_seconds``).

        Args:
            identifier: Throttle bucket key (already namespaced by scope).
            limit: Maximum allowed requests in the window.
            window_seconds: Rolling window duration in seconds.

        Returns:
            ``ThrottleResult`` carrying allow/deny + remaining quota +
            retry-after.
        """

    @abstractmethod
    async def is_healthy(self) -> bool:
        """Probe the backend; clear any sticky fallback flag on recovery.

        Wired into the readiness endpoint so a transient backend outage
        does not silently degrade the cluster's rate limiting (after the
        outage, the cluster would otherwise keep per-worker counts —
        effectively multiplying every configured limit by the worker
        count). Recovery semantics mirror ``RedisCacheBackend.is_healthy``.

        Returns:
            ``True`` when the backend is reachable (or always for an
            in-process implementation); ``False`` otherwise.
        """

    @property
    @abstractmethod
    def backend_name(self) -> str:
        """Short label identifying the active backend for health probes.

        Returns:
            ``"redis"`` / ``"redis-fallback"`` / ``"memory"`` — surfaced
            in the readiness probe ``detail`` field so operators can see
            which path is serving rate-limit decisions at a glance.
        """
