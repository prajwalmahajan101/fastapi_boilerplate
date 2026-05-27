"""Abstract async cache backend."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BaseCacheBackend(ABC):
    """Async key/value cache with TTL, atomic incr, and conditional add."""

    @property
    @abstractmethod
    def backend_name(self) -> str:
        """Return the human-readable backend identifier.

        Returns:
            The backend name (``"memory"``, ``"redis"``, …).
        """

    @abstractmethod
    async def get(self, key: str) -> Any | None:
        """Return the value or ``None`` on miss / error (fail-open).

        Args:
            key: Cache key.

        Returns:
            The cached value, or ``None`` if missing or on backend failure.
        """

    @abstractmethod
    async def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        """Set ``key`` to ``value`` with an optional TTL in seconds.

        Args:
            key: Cache key.
            value: Payload to store.
            ttl: Lifetime in seconds; ``None`` means no expiry.
        """

    @abstractmethod
    async def delete(self, key: str) -> None:
        """Delete ``key`` (no-op if missing).

        Args:
            key: Cache key.
        """

    @abstractmethod
    async def incr(self, key: str) -> int:
        """Atomically increment ``key``. Raises ``ValueError`` if key is missing.

        Args:
            key: Cache key holding the counter.

        Returns:
            The new counter value after the increment.

        Raises:
            ValueError: If the key does not exist.
        """

    @abstractmethod
    async def add(self, key: str, value: Any, ttl: int | None = None) -> bool:
        """Set ``key`` iff it does not already exist. Returns ``True`` on success.

        Args:
            key: Cache key.
            value: Payload to store.
            ttl: Lifetime in seconds; ``None`` means no expiry.

        Returns:
            ``True`` if the value was set, ``False`` if a value already existed.
        """

    @abstractmethod
    async def clear(self) -> None:
        """Remove every key (use carefully — this is a flush)."""

    @abstractmethod
    async def is_healthy(self) -> bool:
        """Return whether the backend is reachable and operational.

        Returns:
            ``True`` if the backend is healthy.
        """
