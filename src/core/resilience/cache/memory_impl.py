"""Per-process async cache — dict + TTL tracking guarded by ``asyncio.Lock``.

Used as the fallback when Redis is unavailable. Single-process only: each
worker has its own counters, so callers needing cross-worker correctness
must rely on the Redis backend in production.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from src.core.resilience.cache.base import BaseCacheBackend

logger = logging.getLogger(__name__)


class InMemoryCacheBackend(BaseCacheBackend):
    """In-process dict cache with TTL eviction on read."""

    def __init__(self) -> None:
        """Initialise the InMemoryCacheBackend."""
        self._store: dict[str, tuple[Any, float | None]] = {}
        self._lock = asyncio.Lock()

    @property
    def backend_name(self) -> str:
        """Label this backend reports in health checks / logs.

        Returns:
            The literal string ``"memory"``.
        """
        return "memory"

    async def get(self, key: str) -> Any | None:
        """Fetch a value, evicting on expiry.

        Args:
            key: Cache key.

        Returns:
            Deserialised value, or ``None`` on miss / expired entry.
        """
        async with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            value, expires_at = entry
            if expires_at is not None and expires_at <= time.monotonic():
                self._store.pop(key, None)
                return None
            return _deserialize(value)

    async def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        """Store ``value`` under ``key`` with optional TTL.

        Args:
            key: Cache key.
            value: Payload (scalars stored verbatim; objects JSON-encoded).
            ttl: Lifetime in seconds; ``None`` means no expiry.
        """
        expires_at = (time.monotonic() + ttl) if ttl else None
        async with self._lock:
            self._store[key] = (_serialize(value), expires_at)

    async def delete(self, key: str) -> None:
        """Drop a key from the store (no-op if missing).

        Args:
            key: Cache key.
        """
        async with self._lock:
            self._store.pop(key, None)

    async def incr(self, key: str) -> int:
        """Increment an integer counter at ``key`` by 1.

        Args:
            key: Cache key holding the counter.

        Returns:
            The new counter value after the increment.

        Raises:
            ValueError: ``key`` doesn't exist or doesn't hold an int.
        """
        async with self._lock:
            entry = self._store.get(key)
            if entry is None:
                raise ValueError(f"Key '{key}' does not exist for incr.")
            value, expires_at = entry
            current = _deserialize(value)
            try:
                new_value = int(current) + 1
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"Key '{key}' holds non-integer value; cannot incr."
                ) from exc
            self._store[key] = (_serialize(new_value), expires_at)
            return new_value

    async def add(self, key: str, value: Any, ttl: int | None = None) -> bool:
        """Atomic create — set ``key`` only when no live entry exists.

        Existing-but-expired entries are evicted before the check so a
        late add still wins after the prior TTL lapses.

        Args:
            key: Cache key.
            value: Payload.
            ttl: Lifetime in seconds; ``None`` means no expiry.

        Returns:
            ``True`` if the key was set, ``False`` if it already existed.
        """
        async with self._lock:
            if key in self._store:
                # Honour TTL eviction even on add — re-check expiry.
                _, expires_at = self._store[key]
                if expires_at is not None and expires_at <= time.monotonic():
                    self._store.pop(key, None)
                else:
                    return False
            expires_at = (time.monotonic() + ttl) if ttl else None
            self._store[key] = (_serialize(value), expires_at)
            return True

    async def clear(self) -> None:
        """Flush every cached entry — used by test teardown.

        Holds the store lock so no concurrent read can observe a
        half-emptied dict. The in-process backend has no other state,
        so a clear leaves the backend ready for fresh use immediately.
        """
        async with self._lock:
            self._store.clear()

    async def is_healthy(self) -> bool:
        """Report health (the in-process store has no failure mode).

        Returns:
            Always ``True``.
        """
        return True


def _serialize(value: Any) -> Any:
    """Convert ``value`` for storage, JSON-encoding complex types.

    Scalars (str/int/float/bool/None) pass through unchanged. Lists /
    dicts are JSON-encoded so deserialise returns a deep copy and
    callers cannot mutate the stored entry by holding a reference.

    Args:
        value: Any payload to store.

    Returns:
        Storage-ready value (original scalar or JSON string).
    """
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return json.dumps(value, default=str)


def _deserialize(value: Any) -> Any:
    """Reverse :func:`_serialize` — JSON-decode strings, pass everything else.

    Args:
        value: Value retrieved from the store.

    Returns:
        Decoded Python value; falls back to the raw string if it
        wasn't JSON.
    """
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return value
    return value
