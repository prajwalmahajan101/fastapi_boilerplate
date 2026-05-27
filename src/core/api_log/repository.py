"""Abstract API log repository — backends implement ``save`` / ``startup`` / ``shutdown``."""

from __future__ import annotations

from abc import ABC, abstractmethod

from src.core.api_log.models import ApiLog


class ApiLogRepository(ABC):
    """Backends must make ``save`` safe to call from a fire-and-forget task."""

    @abstractmethod
    async def save(self, log: ApiLog) -> None:
        """Persist ``log``. Must NOT raise (called from background tasks).

        Args:
            log: A populated ``ApiLog`` record.
        """

    @abstractmethod
    async def startup(self) -> None:
        """Run any backend-side initialisation (open clients, build state)."""

    @abstractmethod
    async def shutdown(self) -> None:
        """Gracefully release backend resources owned exclusively by this repository."""
